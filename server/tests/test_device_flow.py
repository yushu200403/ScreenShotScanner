from app.extensions import db
from app.models import ClientCredential, Device, PairRequest, QuestionRequest
from app.realtime import _handle_approval
from app.security import hash_connection_code, hash_token


class FakeSocket:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def login(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    return response.get_json()["csrf_token"]


def test_pair_approval_device_defaults_and_force_cancel(client, app, monkeypatch):
    token = login(client)
    credential_response = client.post("/api/credentials", json={"name": "Device Flow"},
                                      headers={"X-CSRF-Token": token})
    credential_token = credential_response.get_json()["token"]
    connection_code = "012345678"

    with app.app_context():
        credential = ClientCredential.query.filter_by(token_hash=hash_token(credential_token)).one()
        credential.device_id = "deviceidentity123456789"
        device = Device(credential_id=credential.id, owner_id=credential.owner_id,
                        device_id=credential.device_id, name="Test Device", online=True,
                        connection_code_hash=hash_connection_code(connection_code),
                        connection_code_last4=connection_code[-4:])
        db.session.add(device)
        db.session.commit()

    monkeypatch.setattr("app.api.send_device", lambda device_id, payload: True)
    pair = client.post("/api/devices/pair", json={"connection_code": connection_code},
                       headers={"X-CSRF-Token": token})
    assert pair.status_code == 202
    pair_id = pair.get_json()["pair_request_id"]

    with app.app_context():
        pair_row = db.session.get(PairRequest, pair_id)
        device = db.session.get(Device, pair_row.device_id)
        _handle_approval(FakeSocket(), device, {"pair_request_id": pair_id, "decision": "approve"})

    devices = client.get("/api/devices").get_json()["items"]
    assert devices[0]["name"] == "Test Device"
    device_id = devices[0]["id"]
    prompt_id = client.get("/api/prompts").get_json()["items"][0]["id"]
    model_response = client.post("/api/models", json={
        "name": "Flow Model", "provider": "openai_chat",
        "endpoint": "https://api.example.com/v1/chat/completions",
        "api_key": "test-api-key", "models": ["vision-model"], "is_global": True,
    }, headers={"X-CSRF-Token": token})
    model_id = model_response.get_json()["model"]["id"]
    request_body = {"prompt_id": prompt_id, "model_profile_id": model_id,
                    "model_name": "vision-model", "reasoning_effort": "high",
                    "question": "回答屏幕问题"}

    first = client.post(f"/api/devices/{device_id}/requests", json=request_body,
                        headers={"X-CSRF-Token": token})
    assert first.status_code == 202
    first_id = first.get_json()["request"]["id"]
    busy = client.post(f"/api/devices/{device_id}/requests", json=request_body,
                       headers={"X-CSRF-Token": token})
    assert busy.status_code == 409
    forced = client.post(f"/api/devices/{device_id}/requests",
                         json={**request_body, "force": True}, headers={"X-CSRF-Token": token})
    assert forced.status_code == 202

    with app.app_context():
        first_row = db.session.get(QuestionRequest, first_id)
        device = db.session.get(Device, device_id)
        assert first_row.status == "cancelled"
        assert device.default_prompt_id == prompt_id
        assert device.default_model_profile_id == model_id
        assert device.default_model_name == "vision-model"
        assert device.reasoning_effort == "high"
