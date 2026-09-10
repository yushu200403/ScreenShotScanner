import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace

import pytest
import requests
import websocket
from PIL import Image
from werkzeug.serving import make_server

from app.adapters import stream_profile
from app.security import encrypt_secret
from app.development import WebSocketRequestHandler
from app.versioning import APP_VERSION


@pytest.fixture()
def model_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, dict(self.headers), body))
            if "responses" in self.path:
                events = [{"type": "response.reasoning_summary_text.delta", "delta": "读取题目"},
                          {"type": "response.output_text.delta", "delta": "答案是四"},
                          {"type": "response.completed"}]
            elif "gemini" in self.path:
                events = [{"candidates": [{"content": {"parts": [{"text": "答案是四"}]}, "finishReason": "STOP"}]}]
            else:
                events = [{"choices": [{"delta": {"reasoning_content": "读取题目", "content": "答案是四"}}]},
                          {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
            data = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()
    server.server_close()
    thread.join(3)


@pytest.mark.parametrize("provider", ["deepseek", "openai_chat", "openai_responses", "gemini"])
def test_protocols_over_real_http(app, model_server, tmp_path, provider):
    base, received = model_server
    path = tmp_path / "截图.jpg"
    Image.new("RGB", (24, 24), "white").save(path)
    with app.app_context():
        profile = SimpleNamespace(provider=provider, endpoint=base + "/" + provider,
                                  api_key_ciphertext=encrypt_secret("local-test-key"), timeout_seconds=10,
                                  options_json='{"include_thoughts":true}')
        events = []
        stream_profile(profile, "vision", "回答图中题目", "", str(path), events.append, threading.Event(), "medium")
    assert "".join(event.text for event in events if event.kind == "answer") == "答案是四"
    endpoint, headers, body = received[0]
    if provider == "gemini":
        assert "key=" not in endpoint
        assert headers["x-goog-api-key"] == "local-test-key"
        assert body["contents"][0]["parts"][1]["inline_data"]["data"]
    else:
        assert headers["Authorization"] == "Bearer local-test-key"
        assert "include_thoughts" not in body


def test_browser_device_upload_and_answer_over_network(app, model_server):
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=WebSocketRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    ws_base = base.replace("http:", "ws:")
    session = requests.Session()
    browser = device = None
    try:
        login = session.post(base + "/api/auth/login", json={"username": "admin", "password": "admin-password"}, timeout=5)
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        desktop = requests.post(base + "/api/desktop/login", headers={"X-Client-Version": APP_VERSION},
                                json={"username": "admin", "password": "admin-password", "device_name": "网络测试电脑",
                                      "device_id": "networkdevice123456789"}, timeout=5).json()
        token = desktop["session_token"]
        preset = session.post(base + "/api/presets", headers=headers, json={"name": "本地测试预设", "provider": "openai_chat",
                               "endpoint": model_server[0] + "/chat", "api_key": "local-test-key", "model_name": "vision",
                               "prompt": "解释屏幕内容"}, timeout=5).json()["preset"]
        selected = requests.put(base + "/api/desktop/settings", headers={"X-Client-Version": APP_VERSION, "Authorization": "Bearer " + token},
                                json={"name": "网络测试电脑", "preset_id": preset["id"]}, timeout=5)
        assert selected.status_code == 200
        cookie = "; ".join(f"{key}={value}" for key, value in session.cookies.items())
        browser = websocket.create_connection(ws_base + "/ws/browser", origin=base, cookie=cookie, timeout=5)
        assert json.loads(browser.recv())["type"] == "connected"
        device = websocket.create_connection(ws_base + "/ws/device", timeout=5)
        device.send(json.dumps({"type": "hello", "token": token, "device_id": "networkdevice123456789",
                                "connection_code": "234567890", "client_version": APP_VERSION}))
        assert json.loads(device.recv())["type"] == "hello_ack"
        rows = session.get(base + "/api/devices", timeout=5).json()["items"]
        created = session.post(base + f"/api/devices/{rows[0]['id']}/requests", headers=headers,
                               json={"question": "请解释截图"}, timeout=5)
        assert created.status_code == 202
        capture = json.loads(device.recv())
        assert capture["type"] == "capture_request"
        rid = capture["request_id"]
        image = BytesIO()
        Image.new("RGB", (64, 48), "white").save(image, format="JPEG")
        uploaded = requests.post(base + f"/api/device/requests/{rid}/screenshot", headers={"Authorization": "Bearer " + token, "X-Client-Version": APP_VERSION},
                                 files={"image": ("截图.jpg", image.getvalue(), "image/jpeg")}, timeout=5)
        assert uploaded.status_code == 202
        result = None
        for _ in range(20):
            message = json.loads(browser.recv())
            if message.get("request", {}).get("status") == "completed":
                result = message["request"]
                break
        assert result and result["answer"] == "答案是四"
        assert result["reasoning"] == "读取题目"
        assert session.get(base + result["screenshot_url"], timeout=5).content == image.getvalue()
        rejected = websocket.create_connection(ws_base + "/ws/browser", origin="https://untrusted.example", cookie=cookie, timeout=5)
        try:
            assert rejected.recv() == ""
        finally:
            rejected.close()
        assert session.post(base + "/api/auth/logout", headers=headers, timeout=5).status_code == 200
        assert browser.recv() == ""
    finally:
        if device:
            device.close()
        if browser:
            browser.close()
        session.close()
        server.shutdown()
        server.server_close()
        thread.join(3)
