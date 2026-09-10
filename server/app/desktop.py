from datetime import timedelta
from functools import wraps
import re

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import or_

from .extensions import db
from .lifecycle import disconnect, serialized
from .models import ClientCredential, ClientSession, Device, DevicePreference, Preset, RemovedDevice, utcnow
from .presets import available, selected_preset, summary
from .security import (client_session_valid, error, hash_token, json_body,
                       new_credential_token)


desktop_bp = Blueprint("desktop", __name__)


def desktop_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        credential = ClientCredential.query.filter_by(token_hash=hash_token(token)).first() if token else None
        if not client_session_valid(credential) or not db.session.get(ClientSession, credential.id):
            return error("登录已过期，请重新登录", 401, "auth_required")
        return view(credential, *args, **kwargs)
    return wrapped


def computer_json(credential):
    device = credential.device
    preset = selected_preset(device)
    return {"id": device.id, "device_id": device.device_id, "name": device.name,
            "preset": summary(preset) if preset else None,
            "preset_available": available(preset, credential.owner)}


@desktop_bp.post("/login")
@serialized
def login():
    from .api import authenticate_account
    payload = json_body()
    user, denied = authenticate_account(payload)
    if denied:
        return denied
    device_id = payload.get("device_id", "")
    name = payload.get("device_name", "")
    if not isinstance(device_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,64}", device_id):
        return error("无法识别这台电脑，请重启客户端")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        return error("请为这台电脑取一个名称，最多 120 字")
    device = Device.query.filter_by(device_id=device_id).first()
    if device and device.owner_id != user.id:
        return error("请退出原账号后重新登录", 409, "computer_account_changed")
    removed = db.session.get(RemovedDevice, device.id) if device else None
    if not device or removed:
        count = Device.query.filter(Device.owner_id == user.id,
                                    ~Device.id.in_(db.session.query(RemovedDevice.device_id))).count()
        limit = current_app.config["MAX_DEVICES_PER_USER"]
        if count >= limit:
            return error(f"账号最多保存 {limit} 台电脑，请打开网页，在电脑列表中删除不再使用的电脑后重试",
                         409, "device_limit_reached")
    if removed:
        db.session.delete(removed)
    token = new_credential_token()
    if device:
        credential = device.credential
        disconnect(device, "这台电脑已重新登录")
        credential.token_hash, credential.token_last4 = hash_token(token), token[-4:]
        credential.revoked_at = None
        credential.name = name.strip()
        device.name = name.strip()
    else:
        credential = ClientCredential(owner_id=user.id, device_id=device_id, name=name.strip(),
                                      token_hash=hash_token(token), token_last4=token[-4:])
        db.session.add(credential)
        db.session.flush()
        device = Device(credential_id=credential.id, owner_id=user.id, device_id=device_id, name=name.strip())
        db.session.add(device)
        db.session.flush()
    saved = db.session.get(ClientSession, credential.id)
    if not saved:
        saved = ClientSession(credential_id=credential.id)
        db.session.add(saved)
    saved.auth_version = hash_token(user.password_hash)
    saved.expires_at = utcnow() + timedelta(days=current_app.config["CLIENT_SESSION_DAYS"])
    if not db.session.get(DevicePreference, device.id):
        db.session.add(DevicePreference(device_id=device.id))
    db.session.commit()
    return jsonify(session_token=token, user={"id": user.id, "username": user.username,
                                            "display_name": user.display_name}, computer=computer_json(credential))


@desktop_bp.get("/me")
@desktop_required
def me(credential):
    return jsonify(user={"id": credential.owner.id, "username": credential.owner.username,
                         "display_name": credential.owner.display_name}, computer=computer_json(credential))


@desktop_bp.get("/presets")
@desktop_required
def presets(credential):
    rows = Preset.query.filter(or_(Preset.is_global.is_(True), Preset.owner_id == credential.owner_id)).order_by(Preset.name, Preset.id).all()
    return jsonify(items=[summary(row) for row in rows if available(row, credential.owner)])


@desktop_bp.put("/settings")
@serialized
@desktop_required
def settings(credential):
    payload = json_body()
    name, preset_id = payload.get("name"), payload.get("preset_id")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        return error("请填写电脑名称，最多 120 字")
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        return error("请选择一个预设")
    preset = db.session.get(Preset, preset_id)
    if not available(preset, credential.owner):
        return error("这个预设已停用或无法使用，请刷新后重新选择")
    device = credential.device
    preference = db.session.get(DevicePreference, device.id)
    if not preference:
        preference = DevicePreference(device_id=device.id)
        db.session.add(preference)
    preference.preset_id = preset.id
    device.name, credential.name = name.strip(), name.strip()
    db.session.commit()
    from .realtime import _broadcast_device
    _broadcast_device(device, {"type": "device_update", "device_id": device.id, "name": device.name})
    return jsonify(computer=computer_json(credential))


@desktop_bp.post("/logout")
@serialized
@desktop_required
def logout(credential):
    credential.revoked_at = utcnow()
    db.session.commit()
    disconnect(credential.device, "已退出登录")
    return jsonify(status="ok")
