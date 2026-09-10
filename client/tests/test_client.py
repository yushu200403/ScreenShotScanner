import json
import os
from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
from PIL import Image

import connection
import screenshot
from connection import ClientConnection
from secure_store import SecureStore
from version import APP_VERSION


def client():
    return ClientConnection(lambda *args: None, lambda *args: None, lambda *args: None, lambda: None)


class Socket:
    def __init__(self):
        self.sock = SimpleNamespace(connected=True)
        self.messages = []

    def send(self, value):
        self.messages.append(json.loads(value))

    def close(self):
        self.sock.connected = False


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com/path", "https://name:key@example.com",
                                 "https://example.com?key=abc", "https://example.com:bad"])
def test_reject_invalid_server_url(url):
    with pytest.raises(ValueError):
        ClientConnection.validate_server_url(url)


def test_cancel_allows_immediate_next_capture(monkeypatch):
    started, release, done = Event(), Event(), Event()
    captures = []
    uploads = []

    def capture():
        captures.append(True)
        if len(captures) == 1:
            started.set()
            assert release.wait(3)
        return b"image"

    def post(url, **kwargs):
        uploads.append((url, kwargs["headers"]["Authorization"]))
        done.set()
        return SimpleNamespace(status_code=202, headers={"X-Server-Version": APP_VERSION}, close=lambda: None)

    monkeypatch.setattr(connection, "capture_current_monitor", capture)
    monkeypatch.setattr(connection.requests, "post", post)
    peer = client()
    peer.ws = Socket()
    peer.server_url, peer.token = "http://localhost", "测试凭证"
    peer._start_capture("first")
    first_worker = peer.capture_worker
    assert started.wait(3)
    peer._cancel_capture("first")
    peer._start_capture("second")
    second_worker = peer.capture_worker
    release.set()
    assert done.wait(3)
    first_worker.join(3)
    second_worker.join(3)
    assert uploads == [("http://localhost/api/device/requests/second/screenshot", "Bearer 测试凭证")]
    assert not any(m["type"] == "capture_failed" for m in peer.ws.messages)


def test_old_socket_cannot_update_new_session():
    peer = client()
    old, current = Socket(), Socket()
    peer.ws = current
    peer.connection_code = "123456789"
    peer._on_message(old, json.dumps({"type": "hello_ack", "connection_code": "987654321"}))
    assert peer.connection_code == "123456789"
    assert peer._send({"type": "approval_response"}, ws=old) is False
    assert not current.messages


def test_old_connection_stop_event_remains_set(monkeypatch):
    peer = client()
    previous = peer.stop_event
    monkeypatch.setattr(peer, "_run", lambda event: None)
    peer.start("http://localhost", "测试凭证", "device123456789012345", "123456789")
    peer.worker.join(2)
    assert previous.is_set()
    assert peer.stop_event is not previous
    peer.disconnect()


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows DPAPI")
def test_secure_store_round_trip_and_corruption(tmp_path):
    store = SecureStore(tmp_path / "client.dat")
    data = {"credential": "测试秘密123456789", "connection_code": "123456789", "pending_reset": True}
    store.save(data)
    assert store.load() == data
    assert data["credential"].encode() not in store.path.read_bytes()
    store.path.write_bytes(b"invalid")
    with pytest.raises(RuntimeError, match="无法读取"):
        store.load()


def test_monitor_selection_supports_negative_coordinates(monkeypatch):
    monitors = [{"left": -1200, "top": 0, "width": 1200, "height": 1920},
                {"left": 0, "top": 0, "width": 1920, "height": 1080}]
    monkeypatch.setattr(screenshot, "_cursor_position", lambda: (-200, 300))
    assert screenshot._current_monitor(monitors) is monitors[0]
