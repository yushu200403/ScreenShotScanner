import queue
import secrets
import socket
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from uuid import uuid4

from PIL import Image, ImageDraw
import pystray

from account import AccountClient, AccountError
from connection import ClientConnection
from screenshot import enable_high_dpi
from secure_store import SecureStore
from version import APP_VERSION


class ScreenAnswerClient:
    def __init__(self):
        enable_high_dpi()
        self.root = tk.Tk()
        self.root.title("屏幕问答器 · " + APP_VERSION)
        self.root.geometry("570x700")
        self.root.minsize(540, 670)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_application)
        self.root.bind("<Unmap>", self._on_unmap)
        self.store = SecureStore()
        self.store_error = ""
        try:
            self.saved = self.store.load()
        except (RuntimeError, OSError):
            self.saved = {}
            self.store_error = "无法读取上次的登录信息，请重新登录"
        self.identities = self.saved.get("identities", {})
        self.device_id = ""
        self.connection_code = self.saved.get("connection_code") or self._new_code()
        self.tray = None
        self.quitting = False
        self.generation = 0
        self.busy = False
        self.account = None
        self.presets = []
        self.user = None
        self.ui_events = queue.Queue()
        self.server_var = tk.StringVar(value=self.saved.get("server_url", "https://sss.im33.xyz/"))
        self.username_var = tk.StringVar(value=self.saved.get("username", ""))
        self.password_var = tk.StringVar()
        self.name_var = tk.StringVar(value=self.saved.get("device_name") or socket.gethostname())
        self.preset_var = tk.StringVar()
        self.description_var = tk.StringVar(value="预设包含完整的回答设置，选择后即可在网页提问。")
        self.status_var = tk.StringVar(value=self.store_error or "请使用与网页相同的账号登录")
        self.code_var = tk.StringVar(value="尚未连接")
        self.connection = ClientConnection(self._status_callback, self._connected_callback,
                                           self._approval_callback, self._invalidated_callback)
        self._build_ui()
        self.root.after(80, self._drain_events)
        if self.saved.get("session_token") and self.saved.get("server_url") and self.saved.get("username"):
            self.root.after(300, self.connect)

    @staticmethod
    def _new_code():
        return f"{secrets.randbelow(1_000_000_000):09d}"

    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Status.TLabel", padding=12)
        self.outer = ttk.Frame(self.root, padding=24)
        self.outer.pack(fill="both", expand=True)
        ttk.Label(self.outer, text="屏幕问答器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.outer, text="电脑负责截图，网页负责提问和查看回答").pack(anchor="w", pady=(6, 18))
        self.login_panel = ttk.Frame(self.outer)
        self.login_panel.pack(fill="x")
        for label, variable, hidden in [("服务器地址", self.server_var, False), ("账号", self.username_var, False),
                                         ("密码", self.password_var, True), ("这台电脑的名称", self.name_var, False)]:
            ttk.Label(self.login_panel, text=label).pack(anchor="w", pady=(8, 4))
            entry = ttk.Entry(self.login_panel, textvariable=variable, show="●" if hidden else "")
            entry.pack(fill="x")
            if hidden:
                entry.bind("<Return>", lambda event: self.connect())
        self.connect_button = ttk.Button(self.login_panel, text="登录", command=self.connect)
        self.connect_button.pack(fill="x", pady=(18, 8))
        ttk.Button(self.login_panel, text="打开网页注册或管理电脑", command=self.open_web).pack(fill="x")
        self.settings_panel = ttk.Frame(self.outer)
        self.account_label = ttk.Label(self.settings_panel)
        self.account_label.pack(anchor="w", pady=(0, 12))
        ttk.Label(self.settings_panel, text="这台电脑的名称").pack(anchor="w")
        ttk.Entry(self.settings_panel, textvariable=self.name_var).pack(fill="x", pady=(6, 14))
        ttk.Label(self.settings_panel, text="使用哪个预设").pack(anchor="w")
        self.preset_combo = ttk.Combobox(self.settings_panel, textvariable=self.preset_var, state="readonly")
        self.preset_combo.pack(fill="x", pady=(6, 6))
        self.preset_combo.bind("<<ComboboxSelected>>", self.describe_preset)
        ttk.Label(self.settings_panel, textvariable=self.description_var, wraplength=480).pack(anchor="w", pady=(0, 12))
        actions = ttk.Frame(self.settings_panel)
        actions.pack(fill="x")
        self.save_button = ttk.Button(actions, text="保存设置", command=self.save_settings)
        self.save_button.pack(side="left")
        self.refresh_button = ttk.Button(actions, text="刷新预设", command=self.refresh_presets)
        self.refresh_button.pack(side="left", padx=8)
        ttk.Button(self.settings_panel, text="打开网页提问", command=self.open_web).pack(fill="x", pady=(18, 8))
        self.reconnect_button = ttk.Button(self.settings_panel, text="重新连接", command=self.connect)
        self.reconnect_button.pack(fill="x", pady=(0, 8))
        ttk.Button(self.settings_panel, text="退出登录", command=self.logout).pack(fill="x")
        ttk.Button(self.settings_panel, text="邀请其他账号使用这台电脑", command=self.show_invite).pack(fill="x", pady=(18, 6))
        ttk.Label(self.outer, textvariable=self.status_var, style="Status.TLabel", wraplength=480).pack(fill="x", pady=(20, 0))

    def _background(self, work, done):
        if self.busy:
            return
        self.busy = True
        generation = self.generation
        for button in (self.connect_button, self.save_button, self.refresh_button, self.reconnect_button):
            button.configure(state="disabled")
        def run():
            try:
                result, failure = work(), None
            except (AccountError, ValueError) as exc:
                result, failure = None, exc
            except Exception:
                result, failure = None, AccountError("操作没有完成，请稍后重试")
            def complete():
                if self.quitting or generation != self.generation:
                    return
                self.busy = False
                for button in (self.connect_button, self.save_button, self.refresh_button, self.reconnect_button):
                    button.configure(state="normal")
                if failure:
                    self.status_var.set(str(failure))
                    if getattr(failure, "code", "") == "auth_required":
                        self._clear_login()
                        self.status_var.set("登录已过期，请重新输入密码登录")
                    return
                done(result)
            self.ui_events.put(complete)
        threading.Thread(target=run, name="账号操作", daemon=True).start()

    def connect(self):
        if self.busy:
            return
        try:
            server = ClientConnection.validate_server_url(self.server_var.get())
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        username = self.username_var.get().strip().lower()
        if not username:
            self.status_var.set("请填写账号")
            return
        name = self.name_var.get().strip()
        if not name or len(name) > 120:
            self.status_var.set("请填写电脑名称，最多 120 字")
            return
        identity = server + "|" + username
        self.device_id = self.identities.setdefault(identity, uuid4().hex)
        token = self.saved.get("session_token", "") if (self.saved.get("server_url") == server and self.saved.get("username") == username) else ""
        password = self.password_var.get()
        if not token and not password:
            self.status_var.set("请填写密码")
            return
        account = AccountClient(server, token)
        self.status_var.set("正在恢复登录..." if token else "正在登录...")
        device_id = self.device_id
        def work():
            if token:
                try:
                    return account.me()
                except AccountError as exc:
                    if exc.code != "auth_required" or not password:
                        raise
                    account.token = ""
            return account.login(username, password, device_id, name)
        def done(result):
            self.account, self.user = account, result["user"]
            self.device_id = result["computer"]["device_id"]
            self.identities[identity] = self.device_id
            self.server_var.set(server)
            self.username_var.set(username)
            self.password_var.set("")
            self.name_var.set(result["computer"]["name"])
            self.login_panel.pack_forget()
            self.settings_panel.pack(fill="x", before=self.outer.winfo_children()[-1])
            self.account_label.configure(text="已登录：" + self.user["display_name"])
            self._save_current()
            self.connection.device_name = self.name_var.get()
            self.connection.start(server, account.token, self.device_id, self.connection_code)
            self.refresh_presets()
        self._background(work, done)

    def _save_current(self):
        value = {"server_url": self.server_var.get().strip().rstrip("/"), "username": self.username_var.get().strip().lower(),
                 "session_token": self.account.token if self.account else "", "identities": self.identities,
                 "device_name": self.name_var.get(), "connection_code": self.connection_code}
        self.saved = value
        try:
            self.store.save(value)
            return True
        except (RuntimeError, OSError):
            self.status_var.set("无法保存登录状态，下次打开时需要重新登录")
            return False

    def refresh_presets(self):
        if not self.account:
            return
        account = self.account
        selected = self.presets[self.preset_combo.current()]["id"] if self.preset_combo.current() >= 0 else None
        def work():
            return account.me(), account.presets()
        def done(result):
            me, self.presets = result
            current = me["computer"].get("preset")
            chosen = selected or (current["id"] if current else None)
            labels = [p["name"] + (" · 公共" if p["is_global"] else " · 我的") for p in self.presets]
            labels = [label + (" · 编号 " + str(preset["id"]) if labels.count(label) > 1 else "")
                      for label, preset in zip(labels, self.presets)]
            self.preset_combo.configure(values=labels)
            self.preset_var.set("")
            for index, preset in enumerate(self.presets):
                if preset["id"] == chosen:
                    self.preset_combo.current(index)
                    break
            self.describe_preset()
            if not self.presets:
                self.status_var.set("还没有可用预设，请到网页创建，或联系管理员提供公共预设")
            elif self.preset_combo.current() < 0:
                self.status_var.set("请选择一个预设，然后点击保存设置")
        self._background(work, done)

    def describe_preset(self, event=None):
        index = self.preset_combo.current()
        self.description_var.set(self.presets[index].get("description") or "这个预设已包含完整的回答设置。" if index >= 0 else "请先选择预设；也可以在网页的预设页面创建。")

    def save_settings(self):
        index = self.preset_combo.current()
        if not self.account or index < 0:
            self.status_var.set("请先选择一个预设")
            return
        name, preset_id, account = self.name_var.get().strip(), self.presets[index]["id"], self.account
        def done(result):
            self.name_var.set(result["computer"]["name"])
            self.connection.device_name = self.name_var.get()
            if self._save_current():
                self.status_var.set("设置已保存，现在可以在网页选择这台电脑并提问")
        self._background(lambda: account.save_settings(name, preset_id), done)

    def open_web(self):
        try:
            webbrowser.open(ClientConnection.validate_server_url(self.server_var.get()))
        except ValueError as exc:
            self.status_var.set(str(exc))

    def disconnect(self):
        self.connection.disconnect()
        self.status_var.set("已断开，点击重新连接即可继续使用")
        self.reconnect_button.configure(state="normal")

    def _clear_login(self):
        self.connection.disconnect()
        self.account = None
        self.user = None
        self.presets = []
        self.preset_combo.configure(values=[])
        self.preset_var.set("")
        self.code_var.set("尚未连接")
        self.settings_panel.pack_forget()
        self.login_panel.pack(fill="x", before=self.outer.winfo_children()[-1])
        self.password_var.set("")
        self._save_current()

    def logout(self):
        if not self.account or self.busy:
            return
        account = self.account
        def work():
            try:
                account.logout()
                return ""
            except AccountError as exc:
                return "" if exc.code == "auth_required" else "；服务器暂时未能确认退出"
        def done(message):
            self._clear_login()
            self.status_var.set("已在这台电脑退出登录" + message)
        self._background(work, done)

    def show_invite(self):
        if not self.account:
            return
        window = tk.Toplevel(self.root)
        window.title("邀请其他账号")
        window.geometry("440x240")
        ttk.Label(window, text="同账号无需邀请，登录网页即可看到这台电脑。", wraplength=400).pack(padx=20, pady=16)
        ttk.Label(window, text="将下面的邀请码告诉对方：").pack()
        ttk.Label(window, textvariable=self.code_var, font=("Consolas", 28)).pack(pady=12)
        ttk.Button(window, text="更换邀请码并取消原有邀请", command=self.reset_code).pack()

    def reset_code(self):
        if not self.account:
            return
        previous = self.connection_code
        self.connection_code = self._new_code()
        self._save_current()
        self.connection.reset_code(self.connection_code, previous)

    def _drain_events(self):
        if self.quitting:
            return
        for _ in range(50):
            try:
                callback = self.ui_events.get_nowait()
            except queue.Empty:
                break
            callback()
            if self.quitting:
                return
        self.root.after(80, self._drain_events)

    def _status_callback(self, text, connected):
        self.ui_events.put(lambda: self._set_status(text, connected))

    def _set_status(self, text, connected):
        if not self.quitting:
            self.status_var.set(text)
            if self.account and not connected and self.connection.fatal_error:
                self.reconnect_button.configure(state="normal")

    def _connected_callback(self, code):
        self.ui_events.put(lambda: self._accept_connection(code))

    def _accept_connection(self, code):
        if self.quitting or not self.account:
            return
        self.connection_code = code
        self.code_var.set(code)
        self._save_current()
        self.reconnect_button.configure(state="disabled")

    def _invalidated_callback(self):
        self.ui_events.put(lambda: self.code_var.set("已停用"))

    def _approval_callback(self, message, respond):
        self.ui_events.put(lambda: self._show_approval(message, respond))

    def _show_approval(self, message, respond):
        if self.quitting:
            respond(False)
            return
        was_hidden = self.root.state() == "withdrawn"
        if was_hidden:
            self.root.deiconify()
            self.root.lift()
        name = message.get("display_name") or message.get("username") or "未知用户"
        approved = messagebox.askyesno("允许使用这台电脑？", f"“{name}”想通过网页截取这台电脑的屏幕并提问。\n\n是否允许？", parent=self.root)
        respond(approved)
        if was_hidden and not self.quitting:
            self.root.withdraw()

    def _on_unmap(self, event):
        if not self.quitting:
            self.root.after(80, self._minimize_if_needed)

    def _minimize_if_needed(self):
        if not self.quitting and self.root.state() == "iconic":
            self.root.withdraw()
            self._start_tray()

    def _start_tray(self):
        if self.tray:
            return
        image = Image.new("RGB", (64, 64), "#147d73")
        draw = ImageDraw.Draw(image)
        draw.rectangle((17, 13, 47, 51), fill="#e7f4f1")
        draw.rectangle((23, 21, 41, 27), fill="#147d73")
        draw.rectangle((23, 34, 41, 40), fill="#147d73")
        self.tray = pystray.Icon("ScreenShotScanner", image, "屏幕问答器客户端", pystray.Menu(
            pystray.MenuItem("显示窗口", lambda: self.ui_events.put(self.show_window), default=True),
            pystray.MenuItem("断开连接", lambda: self.ui_events.put(self.disconnect)),
            pystray.MenuItem("退出", lambda: self.ui_events.put(self.exit_application)),
        ))
        self.tray.run_detached()

    def show_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        if self.tray:
            self.tray.stop()
            self.tray = None

    def exit_application(self):
        if self.quitting:
            return
        self.quitting = True
        self.connection.disconnect(wait=True)
        if self.tray:
            self.tray.stop()
            self.tray = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ScreenAnswerClient().run()
