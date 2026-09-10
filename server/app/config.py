import os
from pathlib import Path


def as_bool(value, default=False):
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///screen_scanner.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    STORAGE_DIR = os.getenv("STORAGE_DIR", str(Path("/data/screenshots")))
    MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_BYTES + 64 * 1024
    CAPTURE_TIMEOUT_SECONDS = int(os.getenv("CAPTURE_TIMEOUT_SECONDS", "90"))
    SOCK_SERVER_OPTIONS = {"ping_interval": 25, "max_message_size": 64 * 1024}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = as_bool(os.getenv("SESSION_COOKIE_SECURE"), True)
    PERMANENT_SESSION_LIFETIME = int(os.getenv("SESSION_LIFETIME_SECONDS", str(8 * 60 * 60)))
    LOGIN_FAILURE_LIMIT = int(os.getenv("LOGIN_FAILURE_LIMIT", "5"))
    LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", str(15 * 60)))
    PAIRING_TIMEOUT_SECONDS = int(os.getenv("PAIRING_TIMEOUT_SECONDS", "120"))
    SCREENSHOT_RETENTION_DAYS = int(os.getenv("SCREENSHOT_RETENTION_DAYS", "7"))
    RESULT_RETENTION_DAYS = int(os.getenv("RESULT_RETENTION_DAYS", "90"))
    TRUST_PROXY = as_bool(os.getenv("TRUST_PROXY"), False)
    ENCRYPTION_KEY = os.getenv("APP_ENCRYPTION_KEY", "")
    MAX_DEVICES_PER_USER = int(os.getenv("MAX_DEVICES_PER_USER", "5"))
    CLIENT_SESSION_DAYS = int(os.getenv("CLIENT_SESSION_DAYS", "30"))
