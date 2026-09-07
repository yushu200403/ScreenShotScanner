import json
import os
import re
from io import BytesIO
from datetime import timedelta
from functools import wraps
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, send_file
from sqlalchemy import or_
from PIL import Image, UnidentifiedImageError

from .adapters import model_names
from .extensions import db
from .lifecycle import disconnect, revoke_access, serialized, stop_requests, ACTIVE_STATUSES
from .models import (AuditLog, ClientCredential, Device, DeviceAccess, ModelProfile,
                     PairRequest, PromptTemplate, QuestionRequest, User, is_expired, utcnow, as_utc)
from .runtime import (add_cancel_event, broadcast_user, get_cancel_event, send_device,
                      submit_request, close_browsers)
from .security import (admin_required, audit, csrf_required, csrf_token, decrypt_secret,
                       encrypt_secret, error, establish_session, hash_connection_code,
                       hash_token, is_locked, json_body, login_required, new_connection_code,
                       new_credential_token, password_hash, rate_limited, record_login_failure,
                       record_login_success, user_from_session, valid_password)


api_bp = Blueprint("api", __name__)


def _iso(value):
    return as_utc(value).isoformat() if value else None


def _parse_list(value, fallback=None):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return fallback or []


def _parse_options(value):
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("高级参数必须是 JSON 对象")


def _safe_name(value, fallback):
    value = str(value or "").strip()
    return value[:120] or fallback


def _profile_visible(profile, user):
    return profile.is_global or profile.owner_id == user.id


def _prompt_visible(prompt, user):
    return prompt.is_global or prompt.owner_id == user.id


def _device_accessible(device, user):
    if user.role == "admin":
        return True
    return DeviceAccess.query.filter_by(device_id=device.id, user_id=user.id, revoked_at=None).first() is not None


def _device_json(device, user=None):
    return {
        "id": device.id,
        "device_id": device.device_id,
        "credential_id": device.credential_id,
        "name": device.name,
        "online": device.online,
        "owner_id": device.owner_id,
        "owner_name": device.owner.display_name if device.owner else "",
        "platform": device.platform,
        "client_version": device.client_version,
        "connection_code_last4": device.connection_code_last4,
        "code_invalidated": device.code_invalidated,
        "default_prompt_id": device.default_prompt_id,
        "default_model_profile_id": device.default_model_profile_id,
        "default_model_name": device.default_model_name,
        "reasoning_effort": device.reasoning_effort,
        "last_seen_at": _iso(device.last_seen_at),
        "connected_at": _iso(device.connected_at),
        "created_at": _iso(device.created_at),
    }


def _request_json(row):
    return {
        "id": row.id,
        "device_id": row.device_id,
        "device_name": row.device.name if row.device else "",
        "user_id": row.user_id,
        "status": row.status,
        "model_name": row.model_name,
        "reasoning_effort": row.reasoning_effort,
        "question": row.question or "",
        "reasoning": row.reasoning or "",
        "answer": row.answer or "",
        "error": row.error,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "screenshot_available": bool(row.screenshot_path),
        "screenshot_url": f"/api/requests/{row.id}/image" if row.screenshot_path else None,
    }


def _profile_json(profile):
    return {
        "id": profile.id,
        "name": profile.name,
        "provider": profile.provider,
        "endpoint": profile.endpoint,
        "models": _parse_list(profile.models_json),
        "timeout_seconds": profile.timeout_seconds,
        "options": json.loads(profile.options_json or "{}"),
        "is_global": profile.is_global,
        "enabled": profile.enabled,
        "owner_id": profile.owner_id,
        "api_key_configured": bool(profile.api_key_ciphertext),
        "api_key_last4": decrypt_secret(profile.api_key_ciphertext)[-4:] if profile.api_key_ciphertext else "",
        "created_at": _iso(profile.created_at),
        "updated_at": _iso(profile.updated_at),
    }


def _prompt_json(prompt):
    return {
        "id": prompt.id,
        "name": prompt.name,
        "description": prompt.description or "",
        "content": prompt.content,
        "is_global": prompt.is_global,
        "owner_id": prompt.owner_id,
        "created_at": _iso(prompt.created_at),
        "updated_at": _iso(prompt.updated_at),
    }


def _credential_json(credential):
    return {
        "id": credential.id,
        "name": credential.name,
        "token_last4": credential.token_last4,
        "device_id": credential.device_id,
        "revoked": bool(credential.revoked_at),
        "created_at": _iso(credential.created_at),
        "last_used_at": _iso(credential.last_used_at),
    }


def _cancel_row(row, reason="网页强制中断", commit=True):
    row.cancel_requested = True
    row.status = "cancelled"
    row.error = reason
    row.finished_at = utcnow()
    event = get_cancel_event(row.id)
    if event:
        event.set()
    send_device(row.device.device_id, {"type": "cancel_request", "request_id": row.id})
    if commit:
        db.session.commit()
        broadcast_user(row.user_id, {"type": "request_update", "request": _request_json(row)})


@api_bp.post("/auth/register")
def register():
    if rate_limited("register_ip", request.remote_addr or "unknown", 10, 3600):
        return error("注册请求过于频繁，请稍后再试", 429, "rate_limited")
    payload = json_body()
    username = str(payload.get("username", "")).strip().lower()
    password = str(payload.get("password", ""))
    display_name = _safe_name(payload.get("display_name"), username)
    note = str(payload.get("registration_note", "")).strip()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,80}", username):
        return error("用户名需为 3-80 位字母、数字、下划线、点或短横线")
    if len(password) < 8:
        return error("密码至少需要 8 位")
    if not note or len(note) > 500:
        return error("注册备注必填且不能超过 500 字")
    if User.query.filter_by(username=username).first():
        return error("用户名已存在", 409, "username_exists")
    user = User(username=username, password_hash=password_hash(password), display_name=display_name,
                role="user", status="pending", registration_note=note)
    db.session.add(user)
    db.session.commit()
    audit(user.id, "user.register", "user", user.id, {"registration_note": note})
    return jsonify({"status": "pending", "message": "注册成功，请等待管理员审批"}), 201


@api_bp.post("/auth/login")
def login():
    if rate_limited("login_ip", request.remote_addr or "unknown", 30, 300):
        return error("登录请求过于频繁，请稍后再试", 429, "rate_limited")
    payload = json_body()
    username = str(payload.get("username", "")).strip().lower()
    password = str(payload.get("password", ""))
    user = User.query.filter_by(username=username).first()
    if not user or not valid_password(user, password):
        if user:
            record_login_failure(user)
            audit(user.id, "auth.login_failed", "user", user.id)
        return error("用户名或密码错误", 401, "invalid_credentials")
    if user.status == "pending":
        return error("账号正在等待管理员审批", 403, "account_pending")
    if user.status != "active":
        return error("账号不可用", 403, "account_disabled")
    if is_locked(user):
        return error("登录失败次数过多，请稍后再试", 423, "account_locked")
    establish_session(user)
    record_login_success(user)
    audit(user.id, "auth.login", "user", user.id)
    return jsonify({"user": {"id": user.id, "username": user.username, "display_name": user.display_name,
                              "role": user.role}, "csrf_token": csrf_token()})


@api_bp.post("/auth/logout")
@login_required
def logout(user):
    denied = csrf_required()
    if denied:
        return denied
    audit(user.id, "auth.logout", "user", user.id)
    from flask import session
    session.clear()
    close_browsers(user.id)
    return jsonify({"status": "ok"})


@api_bp.get("/auth/me")
def me():
    user = user_from_session()
    if not user:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "csrf_token": csrf_token(),
                    "user": {"id": user.id, "username": user.username,
                              "display_name": user.display_name, "role": user.role}})


@api_bp.post("/auth/change-password")
@login_required
def change_password(user):
    denied = csrf_required()
    if denied:
        return denied
    payload = json_body()
    if not valid_password(user, str(payload.get("current_password", ""))):
        return error("当前密码错误", 400)
    new_password = str(payload.get("new_password", ""))
    if len(new_password) < 8:
        return error("新密码至少需要 8 位")
    user.password_hash = password_hash(new_password)
    db.session.commit()
    audit(user.id, "auth.password_changed", "user", user.id)
    close_browsers(user.id)
    establish_session(user)
    return jsonify({"status": "ok", "csrf_token": csrf_token()})


@api_bp.get("/credentials")
@login_required
def credentials(user):
    rows = ClientCredential.query.filter_by(owner_id=user.id).order_by(ClientCredential.created_at.desc()).all()
    return jsonify({"items": [_credential_json(row) for row in rows]})


@api_bp.post("/credentials")
@login_required
def create_credential(user):
    denied = csrf_required()
    if denied:
        return denied
    payload = json_body()
    token = new_credential_token()
    row = ClientCredential(owner_id=user.id, name=_safe_name(payload.get("name"), "Windows 客户端"),
                           token_hash=hash_token(token), token_last4=token[-4:])
    db.session.add(row)
    db.session.commit()
    audit(user.id, "credential.created", "credential", row.id)
    return jsonify({"credential": _credential_json(row), "token": token}), 201


@api_bp.delete("/credentials/<int:credential_id>")
@serialized
@login_required
def revoke_credential(user, credential_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(ClientCredential, credential_id)
    if not row or row.owner_id != user.id:
        return error("凭证不存在", 404, "not_found")
    row.revoked_at = utcnow()
    db.session.commit()
    if row.device:
        disconnect(row.device, "客户端凭证已撤销")
    audit(user.id, "credential.revoked", "credential", row.id)
    return jsonify({"status": "ok"})


@api_bp.get("/prompts")
@login_required
def prompts(user):
    rows = PromptTemplate.query.filter(or_(PromptTemplate.is_global.is_(True), PromptTemplate.owner_id == user.id)).order_by(PromptTemplate.updated_at.desc()).all()
    return jsonify({"items": [_prompt_json(row) for row in rows]})


@api_bp.post("/prompts")
@login_required
def create_prompt(user):
    denied = csrf_required()
    if denied:
        return denied
    payload = json_body()
    content = str(payload.get("content", "")).strip()
    if not content or len(content) > 20000:
        return error("提示词不能为空且不能超过 20000 字")
    is_global = user.role == "admin" and bool(payload.get("is_global", True))
    row = PromptTemplate(owner_id=user.id, name=_safe_name(payload.get("name"), "未命名提示词"),
                         description=str(payload.get("description", ""))[:255], content=content,
                         is_global=is_global)
    db.session.add(row)
    db.session.commit()
    audit(user.id, "prompt.created", "prompt", row.id)
    return jsonify({"prompt": _prompt_json(row)}), 201


@api_bp.put("/prompts/<int:prompt_id>")
@login_required
def update_prompt(user, prompt_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(PromptTemplate, prompt_id)
    if not row or (row.owner_id != user.id and not (row.is_global and user.role == "admin")):
        return error("提示词不存在或无权修改", 404, "not_found")
    payload = json_body()
    if "content" in payload:
        content = str(payload.get("content", "")).strip()
        if not content or len(content) > 20000:
            return error("提示词不能为空且不能超过 20000 字")
        row.content = content
    if "name" in payload:
        row.name = _safe_name(payload.get("name"), row.name)
    if "description" in payload:
        row.description = str(payload.get("description", ""))[:255]
    db.session.commit()
    audit(user.id, "prompt.updated", "prompt", row.id)
    return jsonify({"prompt": _prompt_json(row)})


@api_bp.delete("/prompts/<int:prompt_id>")
@login_required
def delete_prompt(user, prompt_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(PromptTemplate, prompt_id)
    if not row or (row.owner_id != user.id and not (row.is_global and user.role == "admin")):
        return error("提示词不存在或无权删除", 404, "not_found")
    if QuestionRequest.query.filter_by(prompt_id=row.id).first():
        return error("该提示词已被历史请求使用，不能删除")
    db.session.delete(row)
    db.session.commit()
    audit(user.id, "prompt.deleted", "prompt", row.id)
    return jsonify({"status": "ok"})


@api_bp.get("/models")
@login_required
def models(user):
    rows = ModelProfile.query.filter(or_(ModelProfile.is_global.is_(True), ModelProfile.owner_id == user.id)).order_by(ModelProfile.updated_at.desc()).all()
    return jsonify({"items": [_profile_json(row) for row in rows]})


@api_bp.post("/models")
@login_required
def create_model(user):
    denied = csrf_required()
    if denied:
        return denied
    payload = json_body()
    provider = str(payload.get("provider", "")).strip()
    if provider not in {"deepseek", "openai_chat", "openai_responses", "gemini"}:
        return error("模型协议必须是 deepseek、openai_chat、openai_responses 或 gemini")
    endpoint = str(payload.get("endpoint", "")).strip()
    api_key = str(payload.get("api_key", ""))
    model_list = _parse_list(payload.get("models"))
    if not endpoint or not api_key or not model_list:
        return error("API 端点、API Key 和至少一个模型名称均为必填")
    if len(endpoint) > 1000 or len(api_key) > 10000:
        return error("API 端点或 API Key 过长")
    if len(model_list) > 100 or any(len(name) > 160 for name in model_list):
        return error("模型名称最多 100 个，每个不能超过 160 字符")
    if not endpoint.startswith(("https://", "http://")):
        return error("API 端点必须使用 HTTP 或 HTTPS")
    try:
        options = _parse_options(payload.get("options"))
    except ValueError as exc:
        return error(str(exc))
    try:
        timeout_seconds = max(10, min(int(payload.get("timeout_seconds", 120)), 600))
    except (TypeError, ValueError):
        return error("超时时间必须是 10 到 600 秒的整数")
    is_global = user.role == "admin" and bool(payload.get("is_global", True))
    row = ModelProfile(owner_id=user.id, name=_safe_name(payload.get("name"), "未命名模型配置"),
                       provider=provider, endpoint=endpoint, api_key_ciphertext=encrypt_secret(api_key),
                       models_json=json.dumps(model_list, ensure_ascii=False),
                       timeout_seconds=timeout_seconds,
                       options_json=json.dumps(options, ensure_ascii=False), is_global=is_global)
    db.session.add(row)
    db.session.commit()
    audit(user.id, "model.created", "model", row.id, {"provider": provider, "endpoint": endpoint})
    return jsonify({"model": _profile_json(row)}), 201


@api_bp.put("/models/<int:model_id>")
@login_required
def update_model(user, model_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(ModelProfile, model_id)
    if not row or (row.owner_id != user.id and not (row.is_global and user.role == "admin")):
        return error("模型配置不存在或无权修改", 404, "not_found")
    payload = json_body()
    if "name" in payload:
        row.name = _safe_name(payload.get("name"), row.name)
    if "endpoint" in payload:
        endpoint = str(payload.get("endpoint", "")).strip()
        if not endpoint.startswith(("https://", "http://")):
            return error("API 端点必须使用 HTTP 或 HTTPS")
        if len(endpoint) > 1000:
            return error("API 端点过长")
        row.endpoint = endpoint
    if "api_key" in payload and str(payload.get("api_key", "")):
        if len(str(payload.get("api_key"))) > 10000:
            return error("API Key 过长")
        row.api_key_ciphertext = encrypt_secret(str(payload.get("api_key")))
    if "models" in payload:
        model_list = _parse_list(payload.get("models"))
        if not model_list:
            return error("至少需要一个模型名称")
        if len(model_list) > 100 or any(len(name) > 160 for name in model_list):
            return error("模型名称最多 100 个，每个不能超过 160 字符")
        row.models_json = json.dumps(model_list, ensure_ascii=False)
    if "timeout_seconds" in payload:
        try:
            row.timeout_seconds = max(10, min(int(payload.get("timeout_seconds", 120)), 600))
        except (TypeError, ValueError):
            return error("超时时间必须是 10 到 600 秒的整数")
    if "options" in payload:
        try:
            row.options_json = json.dumps(_parse_options(payload.get("options")), ensure_ascii=False)
        except ValueError as exc:
            return error(str(exc))
    if "enabled" in payload:
        row.enabled = bool(payload.get("enabled"))
    db.session.commit()
    audit(user.id, "model.updated", "model", row.id)
    return jsonify({"model": _profile_json(row)})


@api_bp.delete("/models/<int:model_id>")
@login_required
def disable_model(user, model_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(ModelProfile, model_id)
    if not row or (row.owner_id != user.id and not (row.is_global and user.role == "admin")):
        return error("模型配置不存在或无权修改", 404, "not_found")
    row.enabled = False
    db.session.commit()
    audit(user.id, "model.disabled", "model", row.id)
    return jsonify({"status": "ok"})


@api_bp.get("/devices")
@login_required
def devices(user):
    if user.role == "admin":
        rows = Device.query.order_by(Device.last_seen_at.desc().nullslast(), Device.created_at.desc()).all()
    else:
        rows = Device.query.join(DeviceAccess, DeviceAccess.device_id == Device.id).filter(
            DeviceAccess.user_id == user.id, DeviceAccess.revoked_at.is_(None)).order_by(Device.created_at.desc()).all()
    return jsonify({"items": [_device_json(row, user) for row in rows]})


@api_bp.post("/devices/pair")
@serialized
@login_required
def pair_device(user):
    denied = csrf_required()
    if denied:
        return denied
    if rate_limited("pair_user", user.id, 20, 60):
        return error("连接码尝试过于频繁，请稍后再试", 429, "rate_limited")
    code = str(json_body().get("connection_code", "")).strip()
    if not re.fullmatch(r"[0-9]{9}", code):
        return error("连接码必须是 9 位数字")
    device = Device.query.filter_by(connection_code_hash=hash_connection_code(code), code_invalidated=False).first()
    if not device:
        return error("连接码无效或设备尚未连接", 404, "pair_not_found")
    if not device.online:
        return error("设备当前不在线", 409, "device_offline")
    existing = DeviceAccess.query.filter_by(device_id=device.id, user_id=user.id, revoked_at=None).first()
    if existing:
        return jsonify({"status": "approved", "device": _device_json(device, user)})
    pending = PairRequest.query.filter_by(device_id=device.id, user_id=user.id, status="pending").first()
    if pending and not is_expired(pending.expires_at):
        return jsonify({"status": "pending", "pair_request_id": pending.id})
    expires = utcnow() + timedelta(seconds=current_app.config["PAIRING_TIMEOUT_SECONDS"])
    pending = PairRequest(device_id=device.id, user_id=user.id, expires_at=expires)
    db.session.add(pending)
    db.session.commit()
    if not send_device(device.device_id, {"type": "approval_request", "pair_request_id": pending.id,
                                           "username": user.username, "display_name": user.display_name}):
        pending.status = "expired"
        db.session.commit()
        return error("设备连接已断开", 409, "device_offline")
    audit(user.id, "device.pair_requested", "device", device.id)
    return jsonify({"status": "pending", "pair_request_id": pending.id}), 202


@api_bp.get("/pairings/<pair_id>")
@serialized
@login_required
def pairing_status(user, pair_id):
    row = db.session.get(PairRequest, pair_id)
    if not row or row.user_id != user.id:
        return error("连接请求不存在", 404, "not_found")
    if (row.status == "pending" and is_expired(row.expires_at)) or (row.status == "approved" and not DeviceAccess.query.filter_by(device_id=row.device_id, user_id=user.id, revoked_at=None).first()):
        row.status = "expired"
        db.session.commit()
    return jsonify({"status": row.status, "pair_request_id": row.id,
                    "device": _device_json(row.device, user) if row.status == "approved" else None})


@api_bp.post("/devices/<int:device_id>/unpair")
@serialized
@login_required
def unpair_device(user, device_id):
    denied = csrf_required()
    if denied:
        return denied
    device = db.session.get(Device, device_id)
    if not device or not _device_accessible(device, user):
        return error("设备不存在或无权操作", 404, "not_found")
    affected_user_ids = revoke_access(device, "网页已解绑连接码")
    device.connection_code_hash = None
    device.connection_code_last4 = None
    device.code_invalidated = True
    db.session.commit()
    send_device(device.device_id, {"type": "code_invalidated", "reason": "网页已解绑连接码"})
    affected_user_ids.add(device.owner_id)
    for affected_user_id in affected_user_ids:
        broadcast_user(affected_user_id, {"type": "device_update", "device_id": device.id,
                                          "online": device.online, "code_invalidated": True})
    audit(user.id, "device.unpaired", "device", device.id)
    return jsonify({"status": "ok"})


@api_bp.post("/devices/<int:device_id>/disconnect")
@serialized
@login_required
def disconnect_device(user, device_id):
    denied = csrf_required()
    if denied:
        return denied
    device = db.session.get(Device, device_id)
    if not device or not _device_accessible(device, user):
        return error("设备不存在或无权操作", 404, "not_found")
    disconnect(device, "网页已断开设备")
    audit(user.id, "device.disconnected", "device", device.id)
    return jsonify({"status": "ok"})


@api_bp.post("/devices/<int:device_id>/requests")
@serialized
@login_required
def create_request(user, device_id):
    denied = csrf_required()
    if denied:
        return denied
    device = Device.query.filter_by(id=device_id).with_for_update().first()
    if not device or not _device_accessible(device, user):
        return error("设备不存在或无权操作", 404, "not_found")
    if device.code_invalidated:
        return error("连接码已失效，请在客户端重置", 409, "code_invalidated")
    if not device.online or device.credential.revoked_at or device.owner.status != "active":
        return error("客户端不在线或凭证不可用", 409, "device_offline")
    payload = json_body()
    try:
        prompt_id = int(payload.get("prompt_id", 0))
        profile_id = int(payload.get("model_profile_id", 0))
    except (TypeError, ValueError):
        return error("请选择有效的提示词和模型配置")
    if prompt_id <= 0 or profile_id <= 0:
        return error("请选择有效的提示词和模型配置")
    if "force" in payload and not isinstance(payload["force"], bool):
        return error("强制中断参数必须为布尔值")
    prompt = db.session.get(PromptTemplate, prompt_id)
    profile = db.session.get(ModelProfile, profile_id)
    if not prompt or not _prompt_visible(prompt, user):
        return error("提示词不存在或无权使用")
    if not profile or not profile.enabled or not _profile_visible(profile, user):
        return error("模型配置不存在、已禁用或无权使用")
    names = model_names(profile)
    model_name = str(payload.get("model_name") or (names[0] if names else "")).strip()
    if not model_name or model_name not in names:
        return error("请选择该模型配置中的模型名称")
    reasoning_effort = str(payload.get("reasoning_effort") or device.reasoning_effort or "medium")
    if reasoning_effort not in {"none", "low", "medium", "high", "xhigh"}:
        return error("推理强度必须是 none、low、medium、high 或 xhigh")
    active = QuestionRequest.query.filter(QuestionRequest.device_id == device.id,
                                          QuestionRequest.status.in_(["waiting_capture", "capturing", "uploading", "processing"])).order_by(QuestionRequest.created_at.desc()).first()
    if active:
        if not bool(payload.get("force")):
            return error("该设备已有请求执行中", 409, "device_busy")
        _cancel_row(active, commit=False)
    row = QuestionRequest(device_id=device.id, user_id=user.id, prompt_id=prompt.id,
                          model_profile_id=profile.id, model_name=model_name,
                          reasoning_effort=reasoning_effort,
                          question=str(payload.get("question", ""))[:5000], status="waiting_capture")
    device.default_prompt_id = prompt.id
    device.default_model_profile_id = profile.id
    device.default_model_name = model_name
    device.reasoning_effort = reasoning_effort
    db.session.add(row)
    db.session.commit()
    if active:
        broadcast_user(active.user_id, {"type": "request_update", "request": _request_json(active)})
    if not send_device(device.device_id, {"type": "capture_request", "request_id": row.id}):
        row.status = "failed"
        row.error = "客户端不在线"
        row.finished_at = utcnow()
        db.session.commit()
        return error("客户端不在线", 409, "device_offline")
    audit(user.id, "request.created", "request", row.id, {"device_id": device.id, "model": model_name,
                                                               "reasoning_effort": reasoning_effort})
    broadcast_user(user.id, {"type": "request_update", "request": _request_json(row)})
    return jsonify({"request": _request_json(row)}), 202


@api_bp.post("/requests/<request_id>/cancel")
@serialized
@login_required
def cancel_request(user, request_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(QuestionRequest, request_id)
    if not row or (user.role != "admin" and row.user_id != user.id):
        return error("请求不存在或无权操作", 404, "not_found")
    if row.status in {"completed", "failed", "cancelled"}:
        return jsonify({"request": _request_json(row)})
    _cancel_row(row)
    audit(user.id, "request.cancelled", "request", row.id)
    return jsonify({"request": _request_json(row)})


@api_bp.post("/device/requests/<request_id>/screenshot")
@serialized
def upload_screenshot(request_id):
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    credential = ClientCredential.query.filter_by(token_hash=hash_token(token)).first()
    if (not credential or credential.revoked_at or not credential.device
            or credential.owner.status != "active"):
        return error("客户端凭证无效", 401, "device_auth_failed")
    row = db.session.get(QuestionRequest, request_id)
    if not row or row.device.credential_id != credential.id:
        return error("请求不存在", 404, "not_found")
    if row.cancel_requested or row.status == "cancelled":
        return error("请求已取消", 409, "request_cancelled")
    if row.status not in {"waiting_capture", "capturing", "uploading"}:
        return error("该请求已接收过截图", 409, "screenshot_already_uploaded")
    upload = request.files.get("image")
    if not upload:
        return error("缺少截图文件")
    data = upload.read(current_app.config["MAX_UPLOAD_BYTES"] + 1)
    if len(data) > current_app.config["MAX_UPLOAD_BYTES"]:
        return error("截图不能超过 5 MB", 413, "file_too_large")
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
        return error("只允许 JPEG 或 PNG 截图")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG"} or image.width * image.height > 40_000_000:
                return error("截图格式无效或像素过大")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return error("截图文件损坏或格式无效")
    folder = os.path.join(current_app.config["STORAGE_DIR"], utcnow().strftime("%Y/%m/%d"))
    path = os.path.join(folder, f"{row.id}.jpg" if data.startswith(b"\xff\xd8\xff") else f"{row.id}.png")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError:
        return error("截图存储失败，请联系管理员检查磁盘", 503, "storage_unavailable")
    row.screenshot_path = path
    row.screenshot_size = len(data)
    row.status = "processing"
    row.started_at = row.started_at or utcnow()
    db.session.commit()
    broadcast_user(row.user_id, {"type": "request_update", "request": _request_json(row)})
    submit_request(current_app._get_current_object(), row.id)
    return jsonify({"status": "accepted"}), 202


@api_bp.get("/requests")
@login_required
def list_requests(user):
    query = QuestionRequest.query
    if user.role != "admin":
        query = query.filter_by(user_id=user.id)
    device_id = request.args.get("device_id", type=int)
    if device_id:
        query = query.filter_by(device_id=device_id)
    rows = query.order_by(QuestionRequest.created_at.desc()).limit(100).all()
    return jsonify({"items": [_request_json(row) for row in rows]})


@api_bp.get("/requests/<request_id>")
@login_required
def get_request(user, request_id):
    row = db.session.get(QuestionRequest, request_id)
    if not row or (user.role != "admin" and row.user_id != user.id):
        return error("请求不存在或无权查看", 404, "not_found")
    return jsonify({"request": _request_json(row)})


@api_bp.get("/requests/<request_id>/image")
@login_required
def request_image(user, request_id):
    row = db.session.get(QuestionRequest, request_id)
    if not row or not row.screenshot_path or (user.role != "admin" and row.user_id != user.id):
        return error("图片不存在或无权查看", 404, "not_found")
    if not os.path.isfile(row.screenshot_path):
        return error("图片文件已清理", 404, "file_missing")
    return send_file(row.screenshot_path, max_age=300)


@api_bp.get("/admin/users")
@admin_required
def admin_users(user):
    rows = User.query.order_by(User.created_at.desc()).all()
    return jsonify({"items": [{"id": row.id, "username": row.username, "display_name": row.display_name,
                                "role": row.role, "status": row.status, "registration_note": row.registration_note or "",
                                "failed_login_count": row.failed_login_count, "locked_until": _iso(row.locked_until),
                                "last_login_at": _iso(row.last_login_at), "created_at": _iso(row.created_at)} for row in rows]})


@api_bp.patch("/admin/users/<int:user_id>")
@serialized
@admin_required
def update_user(admin, user_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(User, user_id)
    if not row:
        return error("用户不存在", 404, "not_found")
    payload = json_body()
    if row.id == admin.id and payload.get("status") in {"disabled", "pending"}:
        return error("不能禁用当前管理员账号")
    if row.id == admin.id and payload.get("role") == "user":
        return error("不能降低当前管理员账号的权限")
    if payload.get("status") in {"pending", "active", "disabled"}:
        row.status = payload["status"]
    if payload.get("role") in {"user", "admin"}:
        row.role = payload["role"]
    if payload.get("display_name"):
        row.display_name = _safe_name(payload.get("display_name"), row.display_name)
    if payload.get("reset_password"):
        new_password = str(payload["reset_password"])
        if len(new_password) < 8:
            return error("重置密码至少需要 8 位")
        row.password_hash = password_hash(new_password)
    if payload.get("unlock"):
        row.locked_until = None
        row.failed_login_count = 0
    db.session.commit()
    if "status" in payload or "role" in payload or payload.get("reset_password"):
        close_browsers(row.id)
    if row.status != "active":
        for device in Device.query.filter_by(owner_id=row.id).all():
            disconnect(device, "账号已停用")
        for active in QuestionRequest.query.filter(QuestionRequest.user_id == row.id,
                                                   QuestionRequest.status.in_(ACTIVE_STATUSES)).all():
            _cancel_row(active, "账号已停用")
    audit(admin.id, "user.updated", "user", row.id, {"fields": list(payload.keys())})
    return jsonify({"status": "ok"})


@api_bp.delete("/admin/users/<int:user_id>")
@serialized
@admin_required
def delete_user(admin, user_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(User, user_id)
    if not row:
        return error("用户不存在", 404, "not_found")
    if row.id == admin.id:
        return error("不能删除当前管理员账号")
    row.status = "deleted"
    row.username = f"deleted-{row.id}-{uuid4().hex[:8]}"
    row.display_name = "已删除用户"
    row.password_hash = password_hash(uuid4().hex + uuid4().hex)
    for credential in ClientCredential.query.filter_by(owner_id=row.id, revoked_at=None).all():
        credential.revoked_at = utcnow()
    db.session.commit()
    close_browsers(row.id)
    for device in Device.query.filter_by(owner_id=row.id).all():
        disconnect(device, "账号已删除")
    for active in QuestionRequest.query.filter(QuestionRequest.user_id == row.id,
                                               QuestionRequest.status.in_(ACTIVE_STATUSES)).all():
        _cancel_row(active, "账号已删除")
    audit(admin.id, "user.deleted", "user", row.id)
    return jsonify({"status": "ok"})


@api_bp.get("/admin/audit-logs")
@admin_required
def audit_logs(admin):
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return jsonify({"items": [{"id": row.id, "username": row.user.username if row.user else "system",
                                "action": row.action, "target_type": row.target_type, "target_id": row.target_id,
                                "ip_address": row.ip_address, "detail": row.detail, "created_at": _iso(row.created_at)} for row in rows]})
