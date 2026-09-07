import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from functools import wraps
import redis

from flask import current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import ServiceUnavailable

from .extensions import db
from .models import AuditLog, User, utcnow


def error(message, status=400, code="bad_request"):
    return jsonify({"error": {"code": code, "message": message}}), status


def json_body():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def rate_limited(scope, identity, limit, window_seconds):
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    key = f"rate:{scope}:{digest}"
    try:
        client = redis.Redis.from_url(current_app.config["REDIS_URL"], socket_connect_timeout=1,
                                      socket_timeout=1, decode_responses=True)
        with client.pipeline() as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = pipe.execute()
        if ttl < 0:
            client.expire(key, window_seconds)
        return int(count) > limit
    except redis.RedisError as exc:
        raise ServiceUnavailable("限流服务暂时不可用，请稍后重试") from exc


def user_from_session():
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if (not user or user.status != "active"
            or session.get("auth_version") != hash_token(user.password_hash)
            or session.get("expires_at", 0) <= time.time()):
        session.clear()
        return None
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = user_from_session()
        if not user:
            return error("需要登录", 401, "auth_required")
        return view(user, *args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = user_from_session()
        if not user:
            return error("需要登录", 401, "auth_required")
        if user.role != "admin":
            return error("需要管理员权限", 403, "forbidden")
        return view(user, *args, **kwargs)
    return wrapped


def require_csrf():
    token = request.headers.get("X-CSRF-Token")
    expected = session.get("csrf_token")
    return bool(token and expected and hmac.compare_digest(token, expected))


def csrf_required():
    if not require_csrf():
        return error("CSRF 校验失败", 403, "csrf_failed")
    return None


def establish_session(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["csrf_token"] = secrets.token_urlsafe(32)
    session["auth_version"] = hash_token(user.password_hash)
    session["expires_at"] = time.time() + current_app.permanent_session_lifetime.total_seconds()


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def hash_token(value):
    secret = current_app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_connection_code(value):
    secret = (current_app.config["SECRET_KEY"] + ":connection-code").encode("utf-8")
    return hmac.new(secret, value.encode("ascii"), hashlib.sha256).hexdigest()


def new_credential_token():
    return secrets.token_urlsafe(32)


def new_connection_code():
    return f"{secrets.randbelow(1_000_000_000):09d}"


def password_hash(password):
    return generate_password_hash(password, method="scrypt")


def valid_password(user, password):
    return bool(password and check_password_hash(user.password_hash, password))


def is_locked(user):
    if not user.locked_until:
        return False
    locked_until = user.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > utcnow()


def record_login_failure(user):
    user.failed_login_count += 1
    if user.failed_login_count >= current_app.config["LOGIN_FAILURE_LIMIT"]:
        user.locked_until = utcnow() + timedelta(seconds=current_app.config["LOGIN_LOCK_SECONDS"])
        user.failed_login_count = 0
    db.session.commit()


def record_login_success(user):
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.session.commit()


def _encryption_key():
    key_material = current_app.config.get("ENCRYPTION_KEY", "") or current_app.config["SECRET_KEY"]
    return hashlib.sha256(key_material.encode("utf-8")).digest()


def encrypt_secret(value):
    if not value:
        return ""
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_encryption_key()).encrypt(nonce, value.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_secret(value):
    if not value:
        return ""
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    return AESGCM(_encryption_key()).decrypt(raw[:12], raw[12:], None).decode("utf-8")


def audit(user_id, action, target_type=None, target_id=None, detail=None):
    row = AuditLog(user_id=user_id, action=action, target_type=target_type,
                   target_id=str(target_id) if target_id is not None else None,
                   ip_address=request.remote_addr,
                   detail=json.dumps(detail, ensure_ascii=False) if detail is not None else None)
    db.session.add(row)
    db.session.commit()
