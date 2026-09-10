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

    # 与网页端一致的配色，客户端和浏览器界面保持同一套视觉语言
    CANVAS = "#f2f6f4"
    CARD = "#ffffff"
    INK = "#15201f"
    FIELD = "#3c4e4a"
    MUTED = "#5f7370"
    LINE = "#e2eae7"
    ACCENT = "#147d73"
    ACCENT_DARK = "#0b6259"
    ACCENT_SOFT = "#eaf3f0"

    def _build_ui(self):
        px = lambda value: round(value * self.ui_scale)
        font = ("Microsoft YaHei UI", 10)
        self.root.configure(background=self.CANVAS)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.CANVAS)
        style.configure("TLabel", background=self.CANVAS, foreground=self.INK, font=font)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Muted.TLabel", foreground=self.MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Card.TFrame", background=self.CARD, bordercolor=self.LINE, relief="solid", borderwidth=1)
        style.configure("CardHead.TLabel", background=self.CARD, foreground=self.INK, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("CardField.TLabel", background=self.CARD, foreground=self.FIELD, font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("CardMuted.TLabel", background=self.CARD, foreground=self.MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("CardName.TLabel", background=self.CARD, foreground=self.INK, font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Chip.TLabel", background=self.ACCENT_SOFT, foreground=self.ACCENT_DARK,
                        font=("Microsoft YaHei UI", 9, "bold"), padding=(px(10), px(6)))
        style.configure("StatusWrap.TFrame", background="#eef3f1")
        style.configure("StatusEdge.TFrame", background=self.ACCENT)
        style.configure("Status.TLabel", background="#eef3f1", foreground=self.ACCENT_DARK,
                        font=("Microsoft YaHei UI", 9), padding=(px(12), px(11)))
        style.configure("TButton", font=font, padding=(px(13), px(10)), relief="solid", borderwidth=1,
                        background="#ffffff", foreground=self.INK, bordercolor="#d5e0dd",
                        lightcolor="#ffffff", darkcolor="#ffffff")
        style.map("TButton", background=[("pressed", "#eef3f1"), ("active", "#f6faf9"), ("disabled", "#f5f8f7")],
                  bordercolor=[("active", "#a4c7c0"), ("disabled", "#e6ecea")],
                  foreground=[("disabled", "#9aaba7")])
        style.configure("Primary.TButton", background=self.ACCENT, foreground="#ffffff", padding=(px(13), px(11)),
                        font=("Microsoft YaHei UI", 10, "bold"), bordercolor=self.ACCENT,
                        lightcolor=self.ACCENT, darkcolor=self.ACCENT)
        style.map("Primary.TButton",
                  background=[("pressed", self.ACCENT_DARK), ("active", self.ACCENT_DARK), ("disabled", "#bacfca")],
                  bordercolor=[("pressed", self.ACCENT_DARK), ("active", self.ACCENT_DARK), ("disabled", "#bacfca")],
                  lightcolor=[("pressed", self.ACCENT_DARK), ("active", self.ACCENT_DARK), ("disabled", "#bacfca")],
                  darkcolor=[("pressed", self.ACCENT_DARK), ("active", self.ACCENT_DARK), ("disabled", "#bacfca")],
                  foreground=[("disabled", "#f1f6f5")])
        style.configure("Link.TButton", background=self.CARD, foreground=self.ACCENT_DARK, padding=(px(8), px(9)),
                        relief="flat", borderwidth=0, bordercolor=self.CARD, lightcolor=self.CARD, darkcolor=self.CARD)
        style.map("Link.TButton", background=[("pressed", self.CARD), ("active", self.CARD)],
                  bordercolor=[("active", self.CARD)], foreground=[("active", self.ACCENT)])
        style.configure("TEntry", padding=px(10), relief="flat", fieldbackground="#ffffff", foreground=self.INK,
                        bordercolor="#d7e1de", lightcolor="#d7e1de", darkcolor="#d7e1de", insertcolor=self.INK)
        style.map("TEntry", bordercolor=[("focus", self.ACCENT)], lightcolor=[("focus", self.ACCENT)],
                  darkcolor=[("focus", self.ACCENT)])
        self.outer = ttk.Frame(self.root, padding=(px(24), px(22)))
        self.outer.pack(fill="both", expand=True)
        header = ttk.Frame(self.outer)
        header.pack(fill="x", pady=(0, px(18)))
        mark = tk.Canvas(header, width=px(42), height=px(42), background=self.CANVAS, highlightthickness=0, borderwidth=0)
        mark.pack(side="left")
        self._draw_mark(mark, px(42))
        heading = ttk.Frame(header)
        heading.pack(side="left", padx=(px(12), 0))
        ttk.Label(heading, text="屏幕问答器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(heading, text="连接这台电脑，在网页开始截图问答 · " + APP_VERSION, style="Muted.TLabel").pack(anchor="w", pady=(px(3), 0))
        self.login_panel = ttk.Frame(self.outer, style="Card.TFrame", padding=px(18))
        self.login_panel.pack(fill="x")
        ttk.Label(self.login_panel, text="登录账号", style="CardHead.TLabel").pack(anchor="w")
        ttk.Label(self.login_panel, text="与网页使用同一个账号密码。", style="CardMuted.TLabel").pack(anchor="w", pady=(px(4), px(12)))
        for label, variable, hidden in [("服务器地址", self.server_var, False), ("账号", self.username_var, False),
                                         ("密码", self.password_var, True)]:
            ttk.Label(self.login_panel, text=label, style="CardField.TLabel").pack(anchor="w", pady=(px(9), px(5)))
            entry = ttk.Entry(self.login_panel, textvariable=variable, show="●" if hidden else "", font=font)
            entry.pack(fill="x")
            if variable is self.server_var:
                ttk.Label(self.login_panel, text="例如：https://sss.im33.xyz", style="CardMuted.TLabel").pack(anchor="w", pady=(px(4), px(0)))
            if hidden:
                entry.bind("<Return>", lambda event: self.connect())
        self.connect_button = ttk.Button(self.login_panel, text="登录并连接", style="Primary.TButton", command=self.connect)
        self.connect_button.pack(fill="x", pady=(px(18), px(6)))
        ttk.Button(self.login_panel, text="打开网页注册", style="Link.TButton", command=self.open_web).pack(fill="x")
        self.settings_panel = ttk.Frame(self.outer, style="Card.TFrame", padding=px(18))
        self.account_label = ttk.Label(self.settings_panel, style="Chip.TLabel")
        self.account_label.pack(anchor="w", pady=(px(0), px(16)))
        ttk.Label(self.settings_panel, text="当前电脑", style="CardField.TLabel").pack(anchor="w")
        ttk.Label(self.settings_panel, textvariable=self.name_var, style="CardName.TLabel", wraplength=px(400)).pack(anchor="w", pady=(px(5), px(12)))
        ttk.Label(self.settings_panel, text="在网页选择预设、管理电脑并查看回答。", style="CardMuted.TLabel", wraplength=px(400)).pack(anchor="w", pady=(px(0), px(18)))
        ttk.Button(self.settings_panel, text="打开截图问答", style="Primary.TButton", command=self.open_web).pack(fill="x", pady=(px(0), px(8)))
        self.reconnect_button = ttk.Button(self.settings_panel, text="重新连接", command=self.connect)
        self.reconnect_button.pack(fill="x", pady=(px(0), px(8)))
        ttk.Button(self.settings_panel, text="退出登录", command=self.logout).pack(fill="x")
        self.status_panel = ttk.Frame(self.outer, style="StatusWrap.TFrame")
        self.status_panel.pack(fill="x", pady=(px(18), px(0)))
        ttk.Frame(self.status_panel, style="StatusEdge.TFrame", width=px(3)).pack(side="left", fill="y")
        ttk.Label(self.status_panel, textvariable=self.status_var, style="Status.TLabel", wraplength=px(380)).pack(side="left", fill="x", expand=True)

    def _draw_mark(self, canvas, size):
        """绘制与网页标识一致的应用图标。"""
        def rounded(x1, y1, x2, y2, radius, fill):
            points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius, x2, y2 - radius,
                      x2, y2, x2 - radius, y2, x1 + radius, y2, x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
            canvas.create_polygon(points, smooth=True, fill=fill, outline=fill)
        rounded(1, 1, size - 1, size - 1, size * 0.28, self.ACCENT)
        inset, arm = size * 0.24, size * 0.13
        width = max(2, round(size * 0.055))
        for x, y, dx, dy in ((inset, inset, 1, 1), (size - inset, inset, -1, 1),
                             (inset, size - inset, 1, -1), (size - inset, size - inset, -1, -1)):
            canvas.create_line(x, y + dy * arm, x, y, x + dx * arm, y, fill="#d9ede7", width=width,
                               capstyle="round", joinstyle="round")
        rounded(size * 0.34, size * 0.38, size * 0.66, size * 0.62, size * 0.05, "#ffffff")

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
