from datetime import datetime, timezone
from uuid import uuid4

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_expired(value):
    return as_utc(value) <= utcnow()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    registration_note = db.Column(db.Text, nullable=True)
    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class ClientCredential(db.Model):
    __tablename__ = "client_credentials"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    token_last4 = db.Column(db.String(4), nullable=False)
    device_id = db.Column(db.String(64), unique=True, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    owner = db.relationship("User", backref=db.backref("credentials", lazy=True))


class Device(db.Model):
    __tablename__ = "devices"
    id = db.Column(db.Integer, primary_key=True)
    credential_id = db.Column(db.Integer, db.ForeignKey("client_credentials.id"), unique=True, nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    device_id = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False, default="Windows 客户端")
    connection_code_hash = db.Column(db.String(64), nullable=True, index=True)
    connection_code_last4 = db.Column(db.String(4), nullable=True)
    code_invalidated = db.Column(db.Boolean, nullable=False, default=False)
    default_prompt_id = db.Column(db.Integer, db.ForeignKey("prompt_templates.id"), nullable=True)
    default_model_profile_id = db.Column(db.Integer, db.ForeignKey("model_profiles.id"), nullable=True)
    default_model_name = db.Column(db.String(160), nullable=True)
    reasoning_effort = db.Column(db.String(20), nullable=False, default="medium")
    online = db.Column(db.Boolean, nullable=False, default=False, index=True)
    client_version = db.Column(db.String(40), nullable=True)
    platform = db.Column(db.String(80), nullable=True)
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=True)
    connected_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    credential = db.relationship("ClientCredential", backref=db.backref("device", uselist=False))
    owner = db.relationship("User", foreign_keys=[owner_id])


class DeviceAccess(db.Model):
    __tablename__ = "device_access"
    __table_args__ = (db.UniqueConstraint("device_id", "user_id", name="uq_device_access_user"),)
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    granted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    granted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    device = db.relationship("Device", backref=db.backref("access_grants", lazy=True))


class PairRequest(db.Model):
    __tablename__ = "pair_requests"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    requested_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)
    device = db.relationship("Device")
    user = db.relationship("User")


class PromptTemplate(db.Model):
    __tablename__ = "prompt_templates"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_global = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    owner = db.relationship("User")


class ModelProfile(db.Model):
    __tablename__ = "model_profiles"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    provider = db.Column(db.String(40), nullable=False)
    endpoint = db.Column(db.String(1000), nullable=False)
    api_key_ciphertext = db.Column(db.Text, nullable=False)
    models_json = db.Column(db.Text, nullable=False, default="[]")
    timeout_seconds = db.Column(db.Integer, nullable=False, default=120)
    options_json = db.Column(db.Text, nullable=False, default="{}")
    is_global = db.Column(db.Boolean, nullable=False, default=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    owner = db.relationship("User")


class QuestionRequest(db.Model):
    __tablename__ = "question_requests"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    prompt_id = db.Column(db.Integer, db.ForeignKey("prompt_templates.id"), nullable=False)
    model_profile_id = db.Column(db.Integer, db.ForeignKey("model_profiles.id"), nullable=False)
    model_name = db.Column(db.String(160), nullable=False)
    reasoning_effort = db.Column(db.String(20), nullable=False, default="medium")
    question = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="waiting_capture", index=True)
    screenshot_path = db.Column(db.String(1000), nullable=True)
    screenshot_size = db.Column(db.Integer, nullable=True)
    reasoning = db.Column(db.Text, nullable=False, default="")
    answer = db.Column(db.Text, nullable=False, default="")
    error = db.Column(db.Text, nullable=True)
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    device = db.relationship("Device")
    user = db.relationship("User")
    prompt = db.relationship("PromptTemplate")
    model_profile = db.relationship("ModelProfile")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(40), nullable=True)
    target_id = db.Column(db.String(80), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User")
