import queue
import secrets
import socket
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
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
        self.ui_scale = self.root.winfo_fpixels("1i") / 96
        self.root.geometry(f"{round(520 * self.ui_scale)}x{round(620 * self.ui_scale)}")
        self.root.minsize(round(500 * self.ui_scale), round(600 * self.ui_scale))
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
        self.user = None
        self.ui_events = queue.Queue()
        self.server_var = tk.StringVar(value=self.saved.get("server_url", "https://sss.im33.xyz/"))
        self.username_var = tk.StringVar(value=self.saved.get("username", ""))
        self.password_var = tk.StringVar()
        self.name_var = tk.StringVar(value=self.saved.get("device_name") or socket.gethostname())
        self.status_var = tk.StringVar(value=self.store_error or "请使用与网页相同的账号登录")
        self.code_var = tk.StringVar(value="尚未连接")
        self.connection = ClientConnection(self._status_callback, self._connected_callback,
                                           self._approval_callback, self._invalidated_callback)
        self.connection.on_settings = self._settings_callback
        self._build_ui()
        self.root.after(80, self._drain_events)
        if self.saved.get("session_token") and self.saved.get("server_url") and self.saved.get("username"):
            self.root.after(300, self.connect)

    @staticmethod
    def _new_code():
        return f"{secrets.randbelow(1_000_000_000):09d}"

    def _build_ui(self):
        px = lambda value: round(value * self.ui_scale)
        self.root.configure(background="#f3f7f6")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f7f6")
        style.configure("TLabel", background="#f3f7f6", foreground="#243b38", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 24, "bold"))
        style.configure("Muted.TLabel", foreground="#657a75")
        style.configure("Status.TLabel", padding=px(12), background="#e4efeb", foreground="#126e61")
        style.configure("TButton", padding=(px(12), px(10)), font=("Microsoft YaHei UI", 10))
        style.configure("Primary.TButton", background="#147d73", foreground="white")
        style.map("Primary.TButton", background=[("active", "#0b6259"), ("disabled", "#94afa9")])
        style.configure("TEntry", padding=px(9), font=("Microsoft YaHei UI", 10))
        self.outer = ttk.Frame(self.root, padding=px(28))
        self.outer.pack(fill="both", expand=True)
        ttk.Label(self.outer, text="屏幕问答器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.outer, text="连接这台电脑，在网页开始截图问答。", style="Muted.TLabel").pack(anchor="w", pady=(px(6), px(24)))
        self.login_panel = ttk.Frame(self.outer)
        self.login_panel.pack(fill="x")
        for label, variable, hidden in [("服务器地址", self.server_var, False), ("账号", self.username_var, False),
                                         ("密码", self.password_var, True)]:
            ttk.Label(self.login_panel, text=label).pack(anchor="w", pady=(px(8), px(5)))
            entry = ttk.Entry(self.login_panel, textvariable=variable, show="●" if hidden else "")
            entry.pack(fill="x")
            if variable is self.server_var:
                ttk.Label(self.login_panel, text="例如：https://sss.im33.xyz", style="Muted.TLabel").pack(anchor="w", pady=(px(4), px(0)))
            if hidden:
                entry.bind("<Return>", lambda event: self.connect())
        self.connect_button = ttk.Button(self.login_panel, text="登录并连接", style="Primary.TButton", command=self.connect)
        self.connect_button.pack(fill="x", pady=(px(20), px(8)))
        ttk.Button(self.login_panel, text="打开网页注册", command=self.open_web).pack(fill="x")
        self.settings_panel = ttk.Frame(self.outer)
        self.account_label = ttk.Label(self.settings_panel)
        self.account_label.pack(anchor="w", pady=(px(0), px(18)))
        ttk.Label(self.settings_panel, text="当前电脑", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(self.settings_panel, textvariable=self.name_var, font=("Microsoft YaHei UI", 16, "bold"), wraplength=px(440)).pack(anchor="w", pady=(px(6), px(16)))
        ttk.Label(self.settings_panel, text="在网页选择预设、管理电脑并查看回答。", style="Muted.TLabel").pack(anchor="w", pady=(px(0), px(20)))
        ttk.Button(self.settings_panel, text="打开截图问答", style="Primary.TButton", command=self.open_web).pack(fill="x", pady=(px(0), px(10)))
        self.reconnect_button = ttk.Button(self.settings_panel, text="重新连接", command=self.connect)
        self.reconnect_button.pack(fill="x", pady=(px(0), px(10)))
        ttk.Button(self.settings_panel, text="退出登录", command=self.logout).pack(fill="x")
        ttk.Label(self.outer, textvariable=self.status_var, style="Status.TLabel", wraplength=px(410)).pack(fill="x", pady=(px(22), px(0)))

    def _background(self, work, done):
        if self.busy:
            return
        self.busy = True
        generation = self.generation
        for button in (self.connect_button, self.reconnect_button):
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
                for button in (self.connect_button, self.reconnect_button):
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

    def _settings_callback(self, name):
        def update():
            if not self.quitting and self.account:
                self.name_var.set(name)
                self._save_current()
        self.ui_events.put(update)

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
        respond(False)

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
