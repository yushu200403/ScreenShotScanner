def login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def csrf(response):
    return response.get_json()["csrf_token"]


def test_registration_requires_note_and_admin_approval(client):
    response = client.post("/api/auth/register", json={
        "username": "new-user", "display_name": "New User", "password": "password-123",
        "registration_note": "用于测试屏幕问答",
    })
    assert response.status_code == 201
    assert login(client, "new-user", "password-123").status_code == 403

    admin_login = login(client, "admin", "admin-password")
    assert admin_login.status_code == 200
    token = csrf(admin_login)
    users = client.get("/api/admin/users").get_json()["items"]
    user = next(item for item in users if item["username"] == "new-user")
    approved = client.patch(f"/api/admin/users/{user['id']}", json={"status": "active"},
                            headers={"X-CSRF-Token": token})
    assert approved.status_code == 200
    client.post("/api/auth/logout", headers={"X-CSRF-Token": token})

    assert login(client, "new-user", "password-123").status_code == 200


def test_retired_configuration_routes_require_upgrade(client):
    for path in ("credentials", "models", "prompts"):
        assert client.get("/api/" + path).status_code == 410


def test_preset_key_is_hidden_and_encrypted(client, app):
    response = login(client, "admin", "admin-password")
    token = csrf(response)
    created = client.post("/api/presets", json={
        "name": "回答预设", "prompt": "解释这张图", "provider": "openai_responses",
        "endpoint": "https://api.example.com/v1/responses", "api_key": "secret-api-key-1234",
        "model_name": "vision-test", "timeout_seconds": 90, "is_global": True,
    }, headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    payload = created.json["preset"]
    assert payload["api_key_configured"]
    assert "api_key" not in payload
    from app.models import ModelProfile
    from app.security import decrypt_secret
    with app.app_context():
        row = ModelProfile.query.filter_by(name="回答预设").one()
        assert row.api_key_ciphertext != "secret-api-key-1234"
        assert decrypt_secret(row.api_key_ciphertext) == "secret-api-key-1234"
