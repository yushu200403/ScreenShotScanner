import importlib.util
import queue
from pathlib import Path
from types import SimpleNamespace

from account import AccountError


spec = importlib.util.spec_from_file_location("desktop_window", Path(__file__).parents[1] / "app.py")
desktop_window = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop_window)


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    def configure(self, **kwargs):
        pass

    def pack(self, **kwargs):
        pass

    def pack_forget(self):
        pass


def window():
    peer = desktop_window.ScreenAnswerClient.__new__(desktop_window.ScreenAnswerClient)
    peer.busy = False
    peer.quitting = False
    peer.ui_events = queue.Queue()
    peer.server_var = Value("http://localhost")
    peer.username_var = Value("tester")
    peer.password_var = Value()
    peer.name_var = Value("书房电脑")
    peer.status_var = Value()
    peer.code_var = Value()
    peer.connection_code = "123456789"
    peer.identities = {}
    peer.saved = {"server_url": "http://localhost", "username": "tester", "session_token": "saved-login"}
    peer.account = None
    peer.login_panel = Widget()
    peer.settings_panel = Widget()
    peer.account_label = Widget()
    peer.outer = SimpleNamespace(winfo_children=lambda: [Widget()])
    peer.written = []
    peer.store = SimpleNamespace(save=peer.written.append)
    peer.started = []
    peer.connection = SimpleNamespace(start=lambda *args: peer.started.append(args), disconnect=lambda: None)
    peer._background = lambda work, done: done(work())
    return peer


def test_window_restores_login_without_password_and_uses_server_device_id(monkeypatch):
    calls = []
    class Account:
        def __init__(self, server, token):
            self.token = token

        def me(self):
            calls.append("恢复登录")
            return {"user": {"display_name": "测试用户"}, "computer": {"device_id": "server-device-identity", "name": "书房电脑"}}

        def login(self, *args):
            raise AssertionError("有效登录状态不应要求重新登录")
    monkeypatch.setattr(desktop_window, "AccountClient", Account)
    peer = window()
    peer.connect()
    assert calls == ["恢复登录"]
    assert peer.started[0][1:3] == ("saved-login", "server-device-identity")
    assert peer.identities["http://localhost|tester"] == "server-device-identity"
    assert "password" not in peer.written[-1]


def test_window_can_log_out_locally_when_server_is_unreachable():
    peer = window()
    def unavailable():
        raise AccountError("网络暂时不可用", "network_error")
    peer.account = SimpleNamespace(token="saved-login", logout=unavailable)
    peer.logout()
    assert peer.account is None
    assert peer.written[-1]["session_token"] == ""
    assert "已在这台电脑退出登录" in peer.status_var.get()


def test_server_name_update_is_saved_on_ui_thread():
    peer = window()
    peer.account = SimpleNamespace(token="saved-login")
    peer._settings_callback("网页设置的电脑名称")
    assert peer.name_var.get() == "书房电脑"
    peer.ui_events.get_nowait()()
    assert peer.name_var.get() == "网页设置的电脑名称"
    assert peer.written[-1]["device_name"] == "网页设置的电脑名称"
