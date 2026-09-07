import os
import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["APP_ENCRYPTION_KEY"] = "test-encryption-key"
os.environ["INITIAL_ADMIN_USERNAME"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin-password"
os.environ["INITIAL_ADMIN_DISPLAY_NAME"] = "Test Admin"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"

from app import create_app
from app.extensions import db


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.rate_limited", lambda *args: False)
    application = create_app()
    application.config.update(TESTING=True, STORAGE_DIR=str(tmp_path), SESSION_COOKIE_SECURE=False)
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
