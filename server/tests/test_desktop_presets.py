import pytest
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import ClientCredential, ClientSession, DatabaseVersion, Device, Preset, QuestionRequest, User
from app.realtime import _authenticate_device, _handle_approval
from app.security import password_hash
from app.versioning import APP_VERSION, DATABASE_VERSION
from test_lifecycle import Socket, ask, flow, upload


def browser_login(client, username="admin", password="admin-password"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json["csrf_token"]}


def desktop_login(client, identity="desktopidentity123456", **extra):
    return client.post("/api/desktop/login", headers={"X-Client-Version": APP_VERSION}, json={
        "username": "admin", "password": "admin-password", "device_id": identity, "device_name": "同名电脑", **extra})


def native_headers(token):
    return {"X-Client-Version": APP_VERSION, "Authorization": "Bearer " + token}


def preset_body(**extra):
    return {"name": "讲解题目", "description": "逐步讲解屏幕上的题目", "prompt": "请详细讲解题目",
            "provider": "openai_chat", "endpoint": "https://example.com/v1/chat/completions",
            "api_key": "测试服务密钥", "model_name": "vision", **extra}


@pytest.mark.parametrize("version", [None, "1.0.0", "2.0.1"])
def test_version_mismatch_blocks_login_socket_and_upload(app, client, version):
    headers = {"X-Client-Version": version} if version else {}
    for path in ("/api/desktop/login", "/api/device/requests/unknown/screenshot"):
        result = client.post(path, headers=headers, json={})
        assert result.status_code == 426
        assert result.json["error"]["code"] == "version_mismatch"
    with app.app_context():
        socket = Socket()
        assert _authenticate_device(socket, {"client_version": version}) is None
        assert socket.messages[-1]["code"] == "version_mismatch"
        assert Device.query.count() == 0


def test_session_reuse_does_not_create_computers_and_same_names_keep_ids(app, client):
    browser_login(client)
    first = desktop_login(client).json
    second = desktop_login(client, "otheridentity123456789").json
    assert first["computer"]["id"] != second["computer"]["id"]
    headers = native_headers(first["session_token"])
    for _ in range(3):
        assert client.get("/api/desktop/me", headers=headers).status_code == 200
    devices = client.get("/api/devices").json["items"]
    assert len(devices) == 2 and {d["name"] for d in devices} == {"同名电脑"}
    with app.app_context():
        assert ClientSession.query.count() == ClientCredential.query.count() == Device.query.count() == 2


def test_device_limit_delete_release_and_relogin(app, client):
    headers = browser_login(client)
    app.config["MAX_DEVICES_PER_USER"] = 2
    first = desktop_login(client).json
    assert desktop_login(client, "otheridentity123456789").status_code == 200
    limited = desktop_login(client, "thirdidentity123456789")
    assert limited.status_code == 409
    assert limited.json["error"]["code"] == "device_limit_reached"
    assert "删除" in limited.json["error"]["message"]
    renewed = desktop_login(client).json
    assert renewed["computer"]["id"] == first["computer"]["id"]
    assert client.get("/api/desktop/me", headers=native_headers(first["session_token"])).status_code == 401
    assert client.delete(f"/api/devices/{first['computer']['id']}", headers=headers).status_code == 200
    assert client.get("/api/desktop/me", headers=native_headers(renewed["session_token"])).status_code == 401
    assert desktop_login(client, "thirdidentity123456789").status_code == 200
    assert desktop_login(client).json["error"]["code"] == "device_limit_reached"
    assert len(client.get("/api/devices").json["items"]) == 2


def test_presets_private_and_public_access(app, client):
    admin_headers = browser_login(client)
    public = client.post("/api/presets", headers=admin_headers, json=preset_body(is_global=True)).json["preset"]
    private = client.post("/api/presets", headers=admin_headers, json=preset_body(name="私人设置")).json["preset"]
    with app.app_context():
        db.session.add(User(username="other", display_name="另一位用户", password_hash=password_hash("password-123"), status="active"))
        db.session.commit()
    other = app.test_client()
    other_headers = browser_login(other, "other", "password-123")
    items = other.get("/api/presets").json["items"]
    assert len(items) == 1 and items[0]["id"] == public["id"]
    assert not items[0]["editable"]
    assert not {"api_key", "endpoint", "prompt"} & items[0].keys()
    assert other.put(f"/api/presets/{public['id']}", headers=other_headers, json=preset_body()).status_code == 404
    assert other.post("/api/presets", headers=other_headers, json=preset_body(is_global=True)).status_code == 400
    personal = other.post("/api/presets", headers=other_headers, json=preset_body()).json["preset"]
    login = desktop_login(other, username="other", password="password-123").json
    native = native_headers(login["session_token"])
    assert other.put("/api/desktop/settings", headers=native, json={"name": "我的电脑", "preset_id": private["id"]}).status_code == 400
    for preset_id in (public["id"], personal["id"]):
        assert other.put("/api/desktop/settings", headers=native, json={"name": "我的电脑", "preset_id": preset_id}).status_code == 200
    assert "api_key" not in str(other.get("/api/desktop/presets", headers=native).json)
    assert client.get("/api/devices").json["items"][0]["name"] == "我的电脑"
    mine = desktop_login(client, "adminidentity123456789").json["computer"]["id"]
    assert other.delete(f"/api/devices/{mine}", headers=other_headers).status_code == 404
    assert other.post(f"/api/devices/{mine}/requests", headers=other_headers, json={}).status_code == 404


def test_missing_and_disabled_presets_are_explicit(app, client, flow):
    native = native_headers(flow["token"])
    assert client.patch(f"/api/presets/{flow['preset_id']}", headers=flow["headers"], json={"enabled": False}).status_code == 200
    assert ask(client, flow).json["error"]["code"] == "preset_required"
    assert not client.get("/api/devices").json["items"][0]["preset_available"]
    assert client.put("/api/desktop/settings", headers=native, json={"name": "测试电脑", "preset_id": flow["preset_id"]}).status_code == 400


def test_inapplicable_options_are_rejected_instead_of_ignored(client):
    headers = browser_login(client)
    response = client.post("/api/presets", headers=headers,
                           json=preset_body(provider="openai_responses", options={"max_tokens": 100}))
    assert response.status_code == 400
    assert "不支持" in response.json["error"]["message"]


def test_preset_edit_preserves_pending_request_configuration(app, client, flow):
    rid = ask(client, flow).json["request"]["id"]
    result = client.put(f"/api/presets/{flow['preset_id']}", headers=flow["headers"], json=preset_body(name="新的预设", prompt="新的回答要求", api_key=""))
    assert result.status_code == 200
    with app.app_context():
        row = db.session.get(QuestionRequest, rid)
        current = db.session.get(Preset, flow["preset_id"])
        assert row.prompt.content == "解释屏幕内容"
        assert current.prompt.content == "新的回答要求"
        assert current.model_profile_id != row.model_profile_id
    assert client.get(f"/api/requests/{rid}").json["request"]["preset_name"] == "测试预设"


def test_password_change_and_logout_invalidate_native_login(app, client, flow):
    headers = native_headers(flow["token"])
    response = client.post("/api/auth/change-password", headers=flow["headers"],
                           json={"current_password": "admin-password", "new_password": "new-password-123"})
    assert response.status_code == 200
    assert client.get("/api/desktop/me", headers=headers).status_code == 401

    with app.app_context():
        assert _authenticate_device(Socket(), {"client_version": APP_VERSION, "token": flow["token"]}) is None
    login = desktop_login(client, "device123456789012345", password="new-password-123")
    assert login.status_code == 200
    headers = native_headers(login.json["session_token"])
    assert client.post("/api/desktop/logout", headers=headers).status_code == 200
    assert client.get("/api/desktop/me", headers=headers).status_code == 401

def test_shared_computer_uses_owner_preset_and_removal_keeps_owner_access(app, client, flow):
    with app.app_context():
        db.session.add(User(username="guest", display_name="受邀用户", password_hash=password_hash("password-123"), status="active"))
        db.session.commit()
    guest = app.test_client()
    headers = browser_login(guest, "guest", "password-123")
    assert guest.get("/api/devices").json["items"] == []
    result = guest.post("/api/devices/pair", headers=headers, json={"connection_code": "123456789"})
    assert result.status_code == 202
    pair_id = result.json["pair_request_id"]
    with app.app_context():
        _handle_approval(flow["socket"], db.session.get(Device, flow["device_id"]),
                         {"pair_request_id": pair_id, "decision": "approve"})
    assert len(guest.get("/api/devices").json["items"]) == 1
    assert guest.get("/api/presets").json["items"] == []
    asked = guest.post(f"/api/devices/{flow['device_id']}/requests", headers=headers, json={})
    assert asked.status_code == 202
    assert asked.json["request"]["preset_name"] == "测试预设"
    assert guest.post(f"/api/devices/{flow['device_id']}/unpair", headers=headers).status_code == 200
    assert guest.get("/api/devices").json["items"] == []
    assert ask(client, flow).status_code == 202


def test_delete_account_keeps_admin_available(app, client):
    headers = browser_login(client)
    with app.app_context():
        user = User(username="remove-me", display_name="待删除用户", password_hash=password_hash("password-123"), status="active")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    result = client.delete(f"/api/admin/users/{user_id}", headers=headers)
    assert result.status_code == 200
    assert client.get("/api/auth/me").json["authenticated"]
    with app.app_context():
        assert db.session.get(User, user_id).status == "deleted"



@pytest.mark.parametrize("old_version", [None, 1])
def test_old_database_is_reset_current_database_is_preserved(monkeypatch, tmp_path, old_version):
    from app.config import Config
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", "sqlite:///" + str(tmp_path / "upgrade.sqlite"))
    app = create_app()
    with app.app_context():
        db.session.add(User(username="old-user", display_name="旧用户", password_hash=password_hash("password123"), status="active"))
        db.session.execute(text("CREATE TABLE unrelated (value INTEGER)"))
        db.session.execute(text("INSERT INTO unrelated VALUES (123)"))
        db.session.commit()
        if old_version is None:
            DatabaseVersion.__table__.drop(db.engine)
        else:
            db.session.get(DatabaseVersion, 1).version = old_version
            db.session.commit()
    updated = create_app()
    with updated.app_context():
        assert User.query.filter_by(username="old-user").first() is None
        assert User.query.filter_by(username="admin").count() == 1
        assert db.session.get(DatabaseVersion, 1).version == DATABASE_VERSION
        assert db.session.execute(text("SELECT value FROM unrelated")).scalar() == 123
        db.session.add(User(username="keep-user", display_name="新用户", password_hash=password_hash("password123"), status="active"))
        db.session.commit()
    restarted = create_app()
    with restarted.app_context():
        assert User.query.filter_by(username="keep-user").count() == 1
