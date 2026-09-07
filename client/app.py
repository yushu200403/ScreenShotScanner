import secrets
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from uuid import uuid4

from PIL import Image, ImageDraw
import pystray

from connection import ClientConnection
from screenshot import enable_high_dpi
from secure_store import SecureStore


class ScreenAnswerClient:
    def __init__(self):
        enable_high_dpi()
        self.root = tk.Tk()
        self.root.title("屏幕问答器客户端")
        self.root.geometry("540x580")
        self.root.minsize(520, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.exit_application)
        self.root.bind("<Unmap>", self._on_unmap)
        self.store = SecureStore()
        self.store_error = ""
        try:
            self.saved = self.store.load()
        except RuntimeError as exc:
            self.saved = {}
            self.store_error = str(exc)
        self.device_id = self.saved.get("device_id") or uuid4().hex
        self.connection_code = self.saved.get("connection_code") or self._new_code()
        self.code_invalidated = bool(self.saved.get("code_invalidated", False))
        self.pending_reset = bool(self.saved.get("pending_reset", False))
        self.previous_code = self.saved.get("previous_connection_code", "")
        if self.code_invalidated:
            self.connection_code = ""
        self.tray = None
        self.quitting = False
        self.ui_events = queue.Queue()
        self.server_var = tk.StringVar(value=self.saved.get("server_url", ""))
        self.token_var = tk.StringVar(value=self.saved.get("credential", ""))
        self.status_var = tk.StringVar(value=self.store_error or "尚未连接")
        self.code_var = tk.StringVar(value="---------")
        self.connection = ClientConnection(self._status_callback, self._connected_callback,
                                           self._approval_callback, self._invalidated_callback)
        self._build_ui()
        self.root.after(80, self._drain_events)
        if self.saved.get("server_url") and self.saved.get("credential") and not self.code_invalidated:
            self.root.after(700, self.connect)

    @staticmethod
    def _new_code():
        return f"{secrets.randbelow(1_000_000_000):09d}"

    def _build_ui(self):
        self.root.configure(bg="#f1f5f4")
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), background="#f1f5f4", foreground="#173033")
        style.configure("Code.TLabel", font=("Consolas", 30, "bold"), background="#ffffff", foreground="#126e65")
        style.configure("Status.TLabel", font=("Segoe UI", 10), background="#e6efed", foreground="#365d59", padding=10)

        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="屏幕问答器客户端", style="Title.TLabel").pack(anchor="w")
        form = ttk.Frame(outer)
        form.pack(fill="x", pady=(20, 0))
        ttk.Label(form, text="服务器地址").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(form, textvariable=self.server_var).grid(row=1, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(form, text="客户端凭证").grid(row=2, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(form, textvariable=self.token_var, show="●").grid(row=3, column=0, sticky="ew", pady=(0, 16))
        form.columnconfigure(0, weight=1)

        actions = ttk.Frame(form)
        actions.grid(row=4, column=0, sticky="ew")
        self.connect_button = ttk.Button(actions, text="连接服务器", command=self.connect)
        self.connect_button.pack(side="left")
        self.disconnect_button = ttk.Button(actions, text="断开连接", command=self.disconnect, state="disabled")
        self.disconnect_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="退出", command=self.exit_application).pack(side="right")

        code_panel = tk.Frame(outer, bg="#ffffff", highlightthickness=1,
                              highlightbackground="#d3dfdc", padx=16, pady=13)
        code_panel.pack(fill="x", pady=(22, 12))
        tk.Label(code_panel, text="连接码", bg="#ffffff", fg="#637174", font=("Segoe UI", 9)).pack(anchor="w")
        ttk.Label(code_panel, textvariable=self.code_var, style="Code.TLabel").pack(anchor="w", pady=(3, 6))
        self.reset_button = ttk.Button(code_panel, text="重置连接码", command=self.reset_code)
        self.reset_button.pack(anchor="e")
        ttk.Label(outer, textvariable=self.status_var, style="Status.TLabel", wraplength=450).pack(fill="x")

    def connect(self):
        server = self.server_var.get().strip()
        token = self.token_var.get().strip()
        if not server or not token:
            self.status_var.set("请填写服务器地址和客户端凭证")
            return
        try:
            server = ClientConnection.validate_server_url(server)
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        if not self.connection_code:
            self.status_var.set("连接码已失效，请先点击重置连接码")
            return
        self.connect_button.configure(state="disabled")
        self.disconnect_button.configure(state="normal")
        self.connection.start(server, token, self.device_id, self.connection_code,
                              reset_on_connect=self.pending_reset,
                              previous_connection_code=self.previous_code)

    def disconnect(self):
        self.connection.disconnect()
        self.status_var.set("已断开连接")
        self.connect_button.configure(state="normal")
        self.disconnect_button.configure(state="disabled")

    def reset_code(self):
        if not self.server_var.get().strip() or not self.token_var.get().strip():
            self.status_var.set("请先填写服务器地址和客户端凭证")
            return
        try:
            server = ClientConnection.validate_server_url(self.server_var.get())
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        previous_code = self.previous_code if self.pending_reset else self.connection_code
        self.previous_code = previous_code
        self.pending_reset = True
        self.connection_code = self._new_code()
        self.code_invalidated = False
        self.code_var.set(self.connection_code)
        if not self._save_current():
            return
        self.connection.start(server, self.token_var.get().strip(), self.device_id,
                              self.connection_code, reset_on_connect=True,
                              previous_connection_code=previous_code)
        self.connect_button.configure(state="disabled")
        self.disconnect_button.configure(state="normal")
        self.status_var.set("正在提交新的连接码...")

    def _save_current(self):
        value = {
            "server_url": self.server_var.get().strip().rstrip("/"),
            "credential": self.token_var.get().strip(),
            "device_id": self.device_id,
            "connection_code": self.connection_code,
            "code_invalidated": self.code_invalidated,
            "pending_reset": self.pending_reset,
            "previous_connection_code": self.previous_code,
        }
        try:
            self.store.save(value)
            return True
        except (RuntimeError, OSError) as exc:
            self.status_var.set("本地连接信息保存失败：" + str(exc))
            return False

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
        if self.quitting:
            return
        self.status_var.set(text)
        active = connected or (self.connection.worker and self.connection.worker.is_alive()
                               and not self.connection.stop_event.is_set() and not self.connection.fatal_error)
        self.connect_button.configure(state="disabled" if active else "normal")
        self.disconnect_button.configure(state="normal" if active else "disabled")

    def _connected_callback(self, code):
        self.ui_events.put(lambda: self._accept_connection(code))

    def _accept_connection(self, code):
        if self.quitting:
            return
        self.connection_code = code
        self.pending_reset = False
        self.previous_code = ""
        self.code_invalidated = False
        self.code_var.set(code)
        self._save_current()
        self.connect_button.configure(state="disabled")
        self.disconnect_button.configure(state="normal")

    def _invalidated_callback(self):
        self.ui_events.put(self._mark_invalidated)

    def _mark_invalidated(self):
        if self.quitting:
            return
        self.connection_code = ""
        self.code_invalidated = True
        self.pending_reset = False
        self.previous_code = ""
        self.code_var.set("已失效")
        self._save_current()

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
        approved = messagebox.askyesno("连接许可", f"网页用户“{name}”请求控制此客户端。\n\n是否允许连接？", parent=self.root)
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
