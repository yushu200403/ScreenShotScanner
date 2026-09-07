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


def test_credential_plaintext_is_returned_once(client):
    response = login(client, "admin", "admin-password")
    token = csrf(response)
    created = client.post("/api/credentials", json={"name": "Test PC"},
                          headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    assert created.get_json()["token"]
    listing = client.get("/api/credentials").get_json()["items"]
    assert listing[0]["name"] == "Test PC"
    assert "token" not in listing[0]


def test_model_api_key_is_masked_and_encrypted(client, app):
    response = login(client, "admin", "admin-password")
    token = csrf(response)
    created = client.post("/api/models", json={
        "name": "Responses Test",
        "provider": "openai_responses",
        "endpoint": "https://api.example.com/v1/responses",
        "api_key": "secret-api-key-1234",
        "models": ["vision-test"],
        "timeout_seconds": 90,
        "options": {"reasoning_effort": "high"},
        "is_global": True,
    }, headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    payload = created.get_json()["model"]
    assert payload["api_key_last4"] == "1234"
    assert "api_key" not in payload

    from app.models import ModelProfile
    from app.security import decrypt_secret
    with app.app_context():
        row = ModelProfile.query.filter_by(name="Responses Test").one()
        assert row.api_key_ciphertext != "secret-api-key-1234"
        assert decrypt_secret(row.api_key_ciphertext) == "secret-api-key-1234"
