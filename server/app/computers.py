from flask import Blueprint, jsonify
from sqlalchemy import or_

from .extensions import db
from .lifecycle import serialized, revoke_access
from .models import Device, DevicePreference, PairRequest, Preset, RemovedDevice, is_expired
from .presets import available, summary
from .realtime import _broadcast_device, decide_pairing
from .runtime import broadcast_user, send_device
from .security import audit, csrf_required, error, hash_connection_code, json_body, login_required, new_connection_code


computers_bp = Blueprint("computers", __name__)


def managed_device(user, device_id):
    device = db.session.get(Device, device_id)
    if not device or db.session.get(RemovedDevice, device_id):
        return None
    return device if device.owner_id == user.id or user.role == "admin" else None


@computers_bp.get("/devices/<int:device_id>/settings")
@login_required
def settings(user, device_id):
    device = managed_device(user, device_id)
    if not device:
        return error("电脑不存在或无权修改设置", 404, "not_found")
    presets = Preset.query.filter(or_(Preset.owner_id == device.owner_id, Preset.is_global.is_(True))).order_by(Preset.name, Preset.id).all()
    return jsonify(presets=[summary(p) for p in presets if available(p, device.owner)], name=device.name)


@computers_bp.put("/devices/<int:device_id>/settings")
@serialized
@login_required
def save_settings(user, device_id):
    denied = csrf_required()
    if denied:
        return denied
    device = managed_device(user, device_id)
    if not device:
        return error("电脑不存在或无权修改设置", 404, "not_found")
    payload = json_body()
    name = payload.get("name", device.name)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        return error("电脑名称需为 1 至 120 字")
    preference = db.session.get(DevicePreference, device.id)
    if not preference:
        preference = DevicePreference(device_id=device.id)
        db.session.add(preference)
    if "preset_id" in payload:
        preset_id = payload["preset_id"]
        if isinstance(preset_id, bool) or not isinstance(preset_id, int) or not available(db.session.get(Preset, preset_id), device.owner):
            return error("请选择可用的预设")
        preference.preset_id = preset_id
    device.name = device.credential.name = name.strip()
    db.session.commit()
    send_device(device.device_id, {"type": "settings_updated", "name": device.name})
    _broadcast_device(device, {"type": "device_update", "device_id": device.id})
    audit(user.id, "device.settings_updated", "device", device.id)
    return jsonify(status="ok")


@computers_bp.get("/devices/<int:device_id>/sharing")
@login_required
def sharing(user, device_id):
    device = managed_device(user, device_id)
    if not device:
        return error("电脑不存在或无权管理共享", 404, "not_found")
    pending = PairRequest.query.filter_by(device_id=device.id, status="pending").all()
    return jsonify(items=[{"id": p.id, "username": p.user.username, "display_name": p.user.display_name}
                          for p in pending if not is_expired(p.expires_at) and p.user.status == "active"])


@computers_bp.post("/devices/<int:device_id>/invitation")
@serialized
@login_required
def invitation(user, device_id):
    denied = csrf_required()
    if denied:
        return denied
    device = managed_device(user, device_id)
    if not device:
        return error("电脑不存在或无权管理共享", 404, "not_found")
    code = new_connection_code()
    while Device.query.filter_by(connection_code_hash=hash_connection_code(code)).first():
        code = new_connection_code()
    affected = revoke_access(device, "电脑主人已更换邀请码")
    device.connection_code_hash = hash_connection_code(code)
    device.connection_code_last4 = code[-4:]
    device.code_invalidated = False
    db.session.commit()
    for uid in affected | {device.owner_id}:
        broadcast_user(uid, {"type": "device_update", "device_id": device.id})
    audit(user.id, "device.invitation_created", "device", device.id)
    return jsonify(connection_code=code)


@computers_bp.post("/devices/<int:device_id>/sharing/<pair_id>")
@serialized
@login_required
def approve_sharing(user, device_id, pair_id):
    denied = csrf_required()
    if denied:
        return denied
    device = managed_device(user, device_id)
    if not device:
        return error("电脑不存在或无权管理共享", 404, "not_found")
    decision = json_body().get("decision")
    if not isinstance(decision, str) or decision not in {"approve", "reject"}:
        return error("请选择允许或拒绝")
    result = decide_pairing(device, {"pair_request_id": pair_id, "decision": decision})
    if result["type"] == "error":
        return error(result["message"], 409, result["code"])
    audit(user.id, "device.sharing_decided", "device", device.id, {"decision": decision})
    return jsonify(status=result["status"])
