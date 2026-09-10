import pytest

from app.endpoints import base_endpoint, request_endpoint
from app.extensions import db
from app.models import User
from app.security import password_hash
from test_desktop_presets import browser_login, desktop_login, preset_body
from test_lifecycle import flow


@pytest.mark.parametrize("provider,suffix", [("openai_chat", "/chat/completions"), ("deepseek", "/chat/completions"), ("openai_responses", "/responses")])
@pytest.mark.parametrize("address", ["https://example.com", "https://example.com/v1", "https://example.com/v1/", "https://example.com/v1/chat/completions", "https://example.com/v1/responses"])
def test_endpoint_normalization(address, provider, suffix):
    assert base_endpoint(address, provider) == "https://example.com/v1"
    assert request_endpoint(address, provider) == "https://example.com/v1" + suffix


@pytest.mark.parametrize("address", ["ftp://example.com/v1", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret", "https://example.com:bad/v1"])
def test_invalid_endpoints_are_rejected(address):
    with pytest.raises(ValueError):
        base_endpoint(address, "openai_chat")


def test_web_settings_and_sharing_permissions(app, client, flow):
    device_id = flow["device_id"]
    headers = flow["headers"]
    endpoint = f"/api/devices/{device_id}"
    assert client.put(endpoint + "/settings", json={"name": "无权限修改"}).status_code == 403
    assert client.put(endpoint + "/settings", headers=headers, json={"name": "网页电脑", "preset_id": flow["preset_id"]}).status_code == 200
    assert client.get("/api/devices").json["items"][0]["name"] == "网页电脑"
    assert client.put(endpoint + "/settings", headers=headers, json={"preset_id": True}).status_code == 400
    assert client.put(endpoint + "/settings", headers=headers, json={"preset_id": 999999}).status_code == 400
    with app.app_context():
        db.session.add(User(username="guest", display_name="受邀用户", password_hash=password_hash("password-123"), status="active"))
        db.session.commit()
    guest = app.test_client()
    gh = browser_login(guest, "guest", "password-123")
    assert guest.get(endpoint + "/settings").status_code == 404
    code = client.post(endpoint + "/invitation", headers=headers).json["connection_code"]
    pending = guest.post("/api/devices/pair", headers=gh, json={"connection_code": code})
    assert pending.status_code == 202
    pair_id = pending.json["pair_request_id"]
    assert client.get(endpoint + "/sharing").json["items"][0]["id"] == pair_id
    assert client.post(endpoint + "/sharing/" + pair_id, headers=headers, json={"decision": []}).status_code == 400
    assert guest.post(endpoint + "/sharing/" + pair_id, headers=gh, json={"decision": "approve"}).status_code == 404
    assert client.post(endpoint + "/sharing/" + pair_id, headers=headers, json={"decision": "approve"}).status_code == 200
    assert len(guest.get("/api/devices").json["items"]) == 1
    assert guest.put(endpoint + "/settings", headers=gh, json={"name": "擅自改名"}).status_code == 404
    assert guest.post(endpoint + "/invitation", headers=gh).status_code == 404
    assert client.get(endpoint + "/sharing").json["items"] == []


def test_web_preset_stores_base_and_relogin_preserves_name(client):
    headers = browser_login(client)
    preset = client.post("/api/presets", headers=headers, json=preset_body()).json["preset"]
    assert preset["endpoint"] == "https://example.com/v1"
    first = desktop_login(client).json
    client.put(f"/api/devices/{first['computer']['id']}/settings", headers=headers, json={"name": "网页命名"})
    assert desktop_login(client, device_name="本机旧名称").json["computer"]["name"] == "网页命名"
