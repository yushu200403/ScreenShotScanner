from types import SimpleNamespace

import pytest

import account
from account import AccountClient, AccountError
from version import APP_VERSION


class Response:
    def __init__(self, data, status=200, version=APP_VERSION):
        self.data, self.status_code = data, status
        self.headers = {"X-Server-Version": version}

    def json(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_saved_login_reuses_token_and_sends_version(monkeypatch):
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response({"user": {"username": "tester"}})
    monkeypatch.setattr(account.requests, "request", request)
    peer = AccountClient("http://127.0.0.1:8000", "saved-login")
    peer.me()
    peer.me()
    assert len(calls) == 2
    assert all(url.endswith("/api/desktop/me") for _, url, _ in calls)
    assert all(kwargs["headers"] == {"X-Client-Version": APP_VERSION, "Authorization": "Bearer saved-login"} for _, _, kwargs in calls)
    assert all(kwargs["json"] is None and kwargs["allow_redirects"] is False for _, _, kwargs in calls)


@pytest.mark.parametrize("version", [None, "1.0.0", "2.0.1"])
def test_reject_wrong_server_version(monkeypatch, version):
    monkeypatch.setattr(account.requests, "request", lambda *args, **kwargs: Response({}, version=version))
    with pytest.raises(AccountError) as exc:
        AccountClient("http://localhost").me()
    assert exc.value.code == "version_mismatch"


def test_device_limit_message_is_preserved(monkeypatch):
    message = "账号最多保存 5 台电脑，请到网页删除不再使用的电脑"
    monkeypatch.setattr(account.requests, "request", lambda *args, **kwargs: Response(
        {"error": {"code": "device_limit_reached", "message": message}}, status=409))
    with pytest.raises(AccountError, match="删除") as exc:
        AccountClient("http://localhost").login("tester", "password123", "test-device-identity", "书房电脑")
    assert exc.value.code == "device_limit_reached"


def test_network_error_has_readable_message(monkeypatch):
    def unavailable(*args, **kwargs):
        raise account.requests.ConnectionError("内部网络异常")
    monkeypatch.setattr(account.requests, "request", unavailable)
    with pytest.raises(AccountError, match="检查网络"):
        AccountClient("http://localhost").me()
