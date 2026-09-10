import json
from datetime import timedelta
from io import BytesIO
from threading import Event

import pytest
import redis
from PIL import Image

from app import create_app
from app.extensions import db
from app.lifecycle import expire_captures, recover_runtime
from app.models import ClientCredential, Device, DeviceAccess, PairRequest, QuestionRequest, User, utcnow
from app.realtime import _authenticate_device, _handle_approval, _handle_device_message
from app.runtime import register_device, unregister_device, add_cancel_event, remove_cancel_event
from app.security import hash_connection_code, hash_token, password_hash
from app.versioning import APP_VERSION


class Socket:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send(self, raw):
        self.messages.append(json.loads(raw))

    def close(self):
        self.closed = True


@pytest.fixture()
def flow(app, client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}).json
    headers = {"X-CSRF-Token": login["csrf_token"]}
    desktop = client.post("/api/desktop/login", headers={"X-Client-Version": APP_VERSION},
                          json={"username": "admin", "password": "admin-password",
                                "device_id": "device123456789012345", "device_name": "测试电脑"}).json
    token = desktop["session_token"]
    preset = client.post("/api/presets", json={"name": "测试预设", "prompt": "解释屏幕内容", "provider": "openai_chat",
                        "endpoint": "https://example.com/v1/chat/completions", "api_key": "测试密钥",
                        "model_name": "vision"}, headers=headers).json["preset"]
    settings = client.put("/api/desktop/settings", headers={"X-Client-Version": APP_VERSION, "Authorization": "Bearer " + token},
                          json={"name": "测试电脑", "preset_id": preset["id"]})
    assert settings.status_code == 200
    socket = Socket()
    with app.app_context():
        device = _authenticate_device(socket, {"token": token, "client_version": APP_VERSION,
                "device_id": "device123456789012345", "connection_code": "123456789"})
        device_id, credential_id = device.id, device.credential_id
    result = {"headers": headers, "token": token, "credential_id": credential_id,
              "device_id": device_id, "payload": {"question": "解释屏幕内容"}, "socket": socket, "preset_id": preset["id"]}
    yield result
    unregister_device("device123456789012345", socket)


def ask(client, flow, **extra):
    return client.post(f"/api/devices/{flow['device_id']}/requests", headers=flow["headers"],
                       json={**flow["payload"], **extra})


def picture():
    output = BytesIO()
    Image.new("RGB", (32, 24), "white").save(output, format="JPEG")
    return output.getvalue()


def upload(client, flow, request_id, data=None):
    return client.post(f"/api/device/requests/{request_id}/screenshot",
                       headers={"Authorization": "Bearer " + flow["token"], "X-Client-Version": APP_VERSION},
                       data={"image": (BytesIO(picture() if data is None else data), "截图.jpg")})


def test_upload_accepts_once_and_rejects_corruption(client, flow, monkeypatch):
    submitted = []
    monkeypatch.setattr("app.api.submit_request", lambda app, rid: submitted.append(rid))
    rid = ask(client, flow).json["request"]["id"]
    assert upload(client, flow, rid, b"\xff\xd8\xffbad").status_code == 400
    assert upload(client, flow, rid).status_code == 202
    assert upload(client, flow, rid).status_code == 409
    assert submitted == [rid]
    assert client.get(f"/api/requests/{rid}/image").data == picture()


def test_image_limit_excludes_multipart_envelope(app, client, flow, monkeypatch):
    monkeypatch.setattr("app.api.submit_request", lambda *args: None)
    data = picture()
    app.config.update(MAX_UPLOAD_BYTES=len(data), MAX_CONTENT_LENGTH=len(data) + 65536)
    rid = ask(client, flow).json["request"]["id"]
    assert upload(client, flow, rid, data + b"x").status_code == 413
    assert upload(client, flow, rid, data).status_code == 202


def test_unpair_expires_approval_and_cancels_upload(app, client, flow):
    pair = client.post("/api/devices/pair", headers=flow["headers"], json={"connection_code": "123456789"}).json
    rid = ask(client, flow).json["request"]["id"]
    assert client.post(f"/api/devices/{flow['device_id']}/unpair", headers=flow["headers"]).status_code == 200
    with app.app_context():
        device = db.session.get(Device, flow["device_id"])
        _handle_approval(flow["socket"], device, {"pair_request_id": pair["pair_request_id"], "decision": "approve"})
        assert db.session.get(PairRequest, pair["pair_request_id"]).status == "expired"
        assert DeviceAccess.query.filter_by(revoked_at=None).count() == 0
    assert upload(client, flow, rid).json["error"]["code"] == "request_cancelled"
    assert ask(client, flow).status_code == 202


def test_delete_closes_device_and_cancels_requests(client, flow):
    rid = ask(client, flow).json["request"]["id"]
    result = client.delete(f"/api/devices/{flow['device_id']}", headers=flow["headers"])
    assert result.status_code == 200
    assert flow["socket"].closed
    assert client.get(f"/api/requests/{rid}").json["request"]["status"] == "cancelled"
    assert upload(client, flow, rid).status_code == 401


def test_reset_ack_can_be_retried(app, flow):
    with app.app_context():
        hello = {"client_version": APP_VERSION, "token": flow["token"], "device_id": "device123456789012345", "connection_code": "987654321",
                 "reset_code": True, "previous_connection_code": "123456789"}
        assert _authenticate_device(flow["socket"], hello) is not None
        assert _authenticate_device(flow["socket"], hello) is not None
        assert flow["socket"].messages[-1]["type"] == "hello_ack"


def test_disabled_owner_cannot_connect_or_upload(app, client, flow):
    rid = ask(client, flow).json["request"]["id"]
    with app.app_context():
        credential = db.session.get(ClientCredential, flow["credential_id"])
        credential.owner.status = "disabled"
        db.session.commit()
        socket = Socket()
        assert _authenticate_device(socket, {"token": flow["token"], "client_version": APP_VERSION}) is None
        assert socket.closed
    assert upload(client, flow, rid).status_code == 401


def test_reset_rejects_collision(app, flow):
    with app.app_context():
        credential = ClientCredential(owner_id=1, name="另一台设备", token_hash=hash_token("另一个凭证"), token_last4="1234")
        db.session.add(credential)
        db.session.flush()
        db.session.add(Device(owner_id=1, credential_id=credential.id, device_id="otherdevice123456789",
                              connection_code_hash=hash_connection_code("987654321")))
        db.session.commit()
        device = db.session.get(Device, flow["device_id"])
        _handle_device_message(flow["socket"], device, {"type": "reset_code", "connection_code": "987654321"})
        assert flow["socket"].messages[-1]["code"] == "code_in_use"
        assert device.connection_code_hash == hash_connection_code("123456789")


def test_capture_timeout_and_restart_recover(app, client, flow):
    rid = ask(client, flow).json["request"]["id"]
    with app.app_context():
        db.session.get(QuestionRequest, rid).created_at = utcnow() - timedelta(minutes=5)
        db.session.commit()
    expire_captures(app)
    assert client.get(f"/api/requests/{rid}").json["request"]["status"] == "failed"
    second = ask(client, flow).json["request"]["id"]
    with app.app_context():
        recover_runtime()
        assert not db.session.get(Device, flow["device_id"]).online
        assert db.session.get(QuestionRequest, second).status == "failed"


def test_worker_cannot_overwrite_cancel(app, client, flow, monkeypatch):
    from app.adapters import StreamEvent
    from app.tasks import run_model_request
    monkeypatch.setattr("app.api.submit_request", lambda *args: None)
    rid = ask(client, flow).json["request"]["id"]
    assert upload(client, flow, rid).status_code == 202

    def stream(profile, model, prompt, question, image_path, emit, event, effort):
        emit(StreamEvent("answer", "第一段"))
        assert client.post(f"/api/requests/{rid}/cancel", headers=flow["headers"]).status_code == 200
        emit(StreamEvent("answer", "不应追加"))

    monkeypatch.setattr("app.tasks.stream_profile", stream)
    run_model_request(app, rid)
    result = client.get(f"/api/requests/{rid}").json["request"]
    assert result["status"] == "cancelled"
    assert result["answer"] == "第一段"


def test_password_change_revokes_other_sessions(app, client):
    other = app.test_client()
    payload = {"username": "admin", "password": "admin-password"}
    csrf = client.post("/api/auth/login", json=payload).json["csrf_token"]
    other.post("/api/auth/login", json=payload)
    result = client.post("/api/auth/change-password", headers={"X-CSRF-Token": csrf},
                         json={"current_password": "admin-password", "new_password": "新的测试密码12345"})
    assert result.status_code == 200
    assert result.json["csrf_token"] != csrf
    assert other.get("/api/auth/me").json["authenticated"] is False
    assert client.get("/api/auth/me").json["authenticated"] is True


def test_rate_limit_service_failure_is_explicit(client, monkeypatch):
    from app.security import rate_limited
    monkeypatch.setattr("app.api.rate_limited", rate_limited)
    def unavailable(*args, **kwargs):
        raise redis.ConnectionError("测试连接失败")
    monkeypatch.setattr("app.security.redis.Redis.from_url", unavailable)
    result = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    assert result.status_code == 503
    assert "稍后再试" in result.json["error"]["message"]


def test_retention_command_preserves_device_state(app, client, flow, tmp_path):
    rid = ask(client, flow).json["request"]["id"]
    image = tmp_path / "过期截图.jpg"
    image.write_bytes(picture())
    with app.app_context():
        row = db.session.get(QuestionRequest, rid)
        row.status = "completed"
        row.created_at = utcnow() - timedelta(days=100)
        row.question, row.answer, row.reasoning = "问题", "回答", "推理"
        row.screenshot_path = str(image)
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["cleanup-retention"])
    assert result.exit_code == 0
    assert not image.exists()
    with app.app_context():
        row = db.session.get(QuestionRequest, rid)
        assert not row.answer and not row.reasoning and not row.question
        assert db.session.get(Device, flow["device_id"]).online


def test_application_factory_does_not_reset_devices(monkeypatch, tmp_path):
    from app.config import Config
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", "sqlite:///" + str(tmp_path / "factory.sqlite"))
    application = create_app()
    with application.app_context():
        credential = ClientCredential(owner_id=1, name="测试凭证", token_hash=hash_token("factory"), token_last4="1234")
        db.session.add(credential)
        db.session.flush()
        db.session.add(Device(owner_id=1, credential_id=credential.id, device_id="factorydevice123456", online=True))
        db.session.commit()
    second = create_app()
    with second.app_context():
        assert Device.query.one().online


def test_request_times_include_timezone(client, flow):
    row = ask(client, flow).json["request"]
    assert row["created_at"].endswith("+00:00")
