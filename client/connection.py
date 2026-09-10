import json
import platform
import socket
import threading
from urllib.parse import urlparse, urlunparse

import requests
import websocket

from screenshot import capture_current_monitor
from version import APP_VERSION


class ClientConnection:
    def __init__(self, on_status, on_connected, on_approval, on_code_invalidated):
        self.on_status = on_status
        self.on_connected = on_connected
        self.on_approval = on_approval
        self.on_code_invalidated = on_code_invalidated
        self.server_url = ""
        self.token = ""
        self.device_id = ""
        self.device_name = ""
        self.connection_code = ""
        self.reset_on_connect = False
        self.previous_connection_code = ""
        self.ws = None
        self.worker = None
        self.stop_event = threading.Event()
        self.send_lock = threading.RLock()
        self.request_lock = threading.RLock()
        self.capture_lock = threading.Lock()
        self.current_request_id = None
        self.current_cancel = None
        self.capture_worker = None
        self.fatal_error = False

    @staticmethod
    def validate_server_url(value):
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("请填写完整服务器地址，例如 https://example.com")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("请使用以 https:// 开头的服务器地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("请只填写服务器首页地址，不要附带页面路径或登录信息")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("服务器端口无效") from exc
        return value

    def _websocket_url(self):
        parsed = urlparse(self.server_url)
        path = parsed.path.rstrip("/") + "/ws/device"
        return urlunparse(parsed._replace(scheme="wss" if parsed.scheme == "https" else "ws",
                                          path=path, query="", fragment=""))

    def start(self, server_url, token, device_id, connection_code, reset_on_connect=False,
              previous_connection_code=""):
        self.disconnect(wait=True)
        self.server_url = self.validate_server_url(server_url)
        self.token = token.strip()
        self.device_id = device_id
        self.connection_code = connection_code
        self.reset_on_connect = reset_on_connect
        self.previous_connection_code = previous_connection_code
        self.stop_event = threading.Event()
        self.fatal_error = False
        self.worker = threading.Thread(target=self._run, args=(self.stop_event,), name="服务连接", daemon=True)
        self.worker.start()

    def _run(self, stop_event):
        while not stop_event.is_set() and not self.fatal_error:
            self.on_status("正在连接服务器...", False)
            ws = websocket.WebSocketApp(self._websocket_url(), on_open=self._on_open,
                                             on_message=self._on_message, on_error=self._on_error,
                                             on_close=self._on_close)
            if stop_event.is_set():
                return
            self.ws = ws
            try:
                ws.run_forever(ping_interval=25, ping_timeout=10)
            except Exception:
                if not stop_event.is_set():
                    self.on_status("网络连接异常，正在重试", False)
            if stop_event.is_set() or self.fatal_error:
                break
            self.on_status("连接中断，5 秒后自动重连", False)
            stop_event.wait(5)

    def _on_open(self, ws):
        if ws is not self.ws or self.stop_event.is_set():
            ws.close()
            return
        self._send({
            "type": "hello",
            "token": self.token,
            "device_id": self.device_id,
            "device_name": self.device_name or socket.gethostname(),
            "client_version": APP_VERSION,
            "platform": platform.platform(),
            "connection_code": self.connection_code,
            "reset_code": self.reset_on_connect,
            "previous_connection_code": self.previous_connection_code,
        })
        threading.Thread(target=self._heartbeat, args=(ws, self.stop_event), daemon=True).start()

    def _heartbeat(self, ws, stop_event):
        while not stop_event.wait(20):
            if self.ws is not ws:
                return
            self._send({"type": "heartbeat"})

    def _on_message(self, ws, raw):
        if ws is not self.ws or self.stop_event.is_set():
            return
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return
        message_type = message.get("type")
        if message_type in {"hello_ack", "code_reset_ack"}:
            if message_type == "hello_ack" and message.get("server_version") != APP_VERSION:
                self.fatal_error = True
                self.on_status("客户端与服务器版本不同，请更新后重新登录", False)
                ws.close()
                return
            self.connection_code = message.get("connection_code", self.connection_code)
            self.reset_on_connect = False
            self.previous_connection_code = ""
            self.on_status("已连接，等待网页操作", True)
            self.on_connected(self.connection_code)
            return
        if message_type == "approval_request":
            self.on_approval(message, lambda approved: self._send({
                "type": "approval_response",
                "pair_request_id": message.get("pair_request_id"),
                "decision": "approve" if approved else "reject",
            }, ws=ws))
            return
        if message_type == "capture_request":
            self._start_capture(str(message.get("request_id", "")), ws)
            return
        if message_type == "cancel_request":
            self._cancel_capture(str(message.get("request_id", "")))
            return
        if message_type == "code_invalidated":
            self.on_code_invalidated()
            self.on_status("邀请码已停用，同账号仍可使用这台电脑", True)
            return
        if message_type == "server_disconnect":
            self.fatal_error = True
            self.on_status(str(message.get("reason") or "服务端已断开连接"), False)
            ws.close()
            return
        if message_type == "error":
            code = message.get("code")
            self.on_status(str(message.get("message") or "服务端拒绝连接"), False)
            if code in {"version_mismatch", "device_auth_failed", "credential_bound", "code_invalidated", "code_mismatch", "code_reset_denied", "invalid_code", "code_in_use"}:
                self.fatal_error = True
                if code == "code_invalidated":
                    self.on_code_invalidated()
                ws.close()

    def _on_error(self, ws, error):
        if ws is not self.ws:
            return
        if not self.stop_event.is_set() and not self.fatal_error:
            self.on_status("网络连接异常，正在重试", False)

    def _on_close(self, ws, status_code, message):
        if ws is not self.ws:
            return
        self._cancel_capture(self.current_request_id)
        if not self.stop_event.is_set() and not self.fatal_error:
            self.on_status("服务器连接已断开", False)

    def _send(self, payload, ws=None):
        with self.send_lock:
            if ws is not None and ws is not self.ws:
                return False
            try:
                if self.ws and self.ws.sock and self.ws.sock.connected:
                    self.ws.send(json.dumps(payload, ensure_ascii=False))
                    return True
            except Exception:
                pass
        return False

    def _start_capture(self, request_id, ws=None):
        if not request_id:
            return
        with self.request_lock:
            if ws is not None and ws is not self.ws:
                return
            if self.current_request_id:
                self._send({"type": "capture_failed", "request_id": request_id,
                            "error": "客户端正在处理其他截图请求"})
                return
            self.current_request_id = request_id
            cancel_event = threading.Event()
            self.current_cancel = cancel_event
            worker = threading.Thread(target=self._capture_and_upload,
                                      args=(request_id, cancel_event, self.server_url, self.token, self.ws), daemon=True)
            self.capture_worker = worker
        worker.start()

    def _capture_and_upload(self, request_id, cancel_event, server_url, token, ws):
        def send(payload):
            return self._send(payload, ws=ws)
        try:
            send({"type": "device_status", "request_id": request_id, "status": "capturing"})
            with self.capture_lock:
                if cancel_event.is_set():
                    return
                image = capture_current_monitor()
            if cancel_event.is_set():
                send({"type": "request_cancelled", "request_id": request_id})
                return
            send({"type": "device_status", "request_id": request_id, "status": "uploading"})
            response = requests.post(server_url + "/api/device/requests/" + request_id + "/screenshot",
                                     headers={"Authorization": "Bearer " + token, "X-Client-Version": APP_VERSION},
                                     files={"image": ("screenshot.jpg", image, "image/jpeg")}, timeout=(10, 45), allow_redirects=False)
            if response.headers.get("X-Server-Version") != APP_VERSION:
                self.fatal_error = True
                self.on_status("客户端与服务器版本不同，请更新客户端", False)
                ws.close()
                return
            if response.status_code >= 400:
                try:
                    error_data = response.json().get("error", {})
                    error_code = error_data.get("code")
                    message = error_data.get("message", "截图上传失败")
                except ValueError:
                    error_code = None
                    message = "截图上传没有成功，请稍后重试"
                if error_code == "version_mismatch":
                    self.fatal_error = True
                    self.on_status("客户端与服务器版本不同，请更新客户端", False)
                    ws.close()
                if error_code == "request_cancelled":
                    send({"type": "request_cancelled", "request_id": request_id})
                elif error_code != "screenshot_already_uploaded":
                    send({"type": "capture_failed", "request_id": request_id, "error": message})
        except Exception:
            send({"type": "capture_failed", "request_id": request_id, "error": "截图或上传没有成功，请检查网络后重试"})
        finally:
            if "response" in locals():
                response.close()
            with self.request_lock:
                if self.current_request_id == request_id:
                    self.current_request_id = None
                    self.current_cancel = None
                    self.capture_worker = None

    def _cancel_capture(self, request_id):
        with self.request_lock:
            if self.current_request_id == request_id and self.current_cancel:
                self.current_cancel.set()
                self.current_request_id = None
                self.current_cancel = None
                self.capture_worker = None

    def reset_code(self, connection_code, previous_connection_code=""):
        self.connection_code = connection_code
        self.reset_on_connect = True
        self.previous_connection_code = previous_connection_code
        if not self._send({"type": "reset_code", "connection_code": connection_code}):
            server_url, token, device_id = self.server_url, self.token, self.device_id
            self.start(server_url, token, device_id, connection_code, reset_on_connect=True,
                       previous_connection_code=previous_connection_code)

    def disconnect(self, wait=False):
        self.stop_event.set()
        self._cancel_capture(self.current_request_id)
        ws = self.ws
        if ws:
            try:
                self._send({"type": "close"})
                ws.close()
            except Exception:
                pass
        if wait and self.worker and self.worker.is_alive() and self.worker is not threading.current_thread():
            self.worker.join(timeout=2)
        capture_worker = self.capture_worker
        if wait and capture_worker and capture_worker.is_alive() and capture_worker is not threading.current_thread():
            capture_worker.join(timeout=2)
        self.ws = None
