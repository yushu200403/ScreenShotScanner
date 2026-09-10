import json
import re
from datetime import timedelta

from flask import current_app, request, session
from urllib.parse import urlsplit
from flask_sock import Sock

from .extensions import db
from .lifecycle import serialized, state_lock, revoke_access, stop_requests, expire_pairings, CAPTURE_STATUSES
from .models import ClientCredential, Device, DeviceAccess, PairRequest, QuestionRequest, is_expired, utcnow
from .runtime import (broadcast_user, register_browser, register_device, send_device,
                      unregister_browser, unregister_device, is_current_device)
from .security import client_session_valid, hash_connection_code, hash_token, new_connection_code
from .versioning import APP_VERSION


sock = Sock()


def _send(ws, payload):
    try:
        ws.send(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


def _device_error(ws, message, code="device_auth_failed"):
    _send(ws, {"type": "error", "code": code, "message": message})
    try:
        ws.close()
    except Exception:
        pass


def _parse(raw):
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


@serialized
def _handle_approval(ws, device, message):
    pair_id = str(message.get("pair_request_id", ""))
    decision = message.get("decision")
    pair = db.session.get(PairRequest, pair_id)
    if not pair or pair.device_id != device.id or pair.status != "pending":
        _send(ws, {"type": "error", "code": "pairing_not_found", "message": "使用申请已失效，请重新申请"})
        return
    if device.code_invalidated or pair.user.status != "active" or is_expired(pair.expires_at):
        pair.status = "expired"
        db.session.commit()
        _send(ws, {"type": "error", "code": "pairing_expired", "message": "使用申请已过期，请重新申请"})
        return
    pair.status = "approved" if decision == "approve" else "rejected"
    pair.decided_at = utcnow()
    if pair.status == "approved":
        Device.query.filter_by(id=device.id).with_for_update().first()
        existing = DeviceAccess.query.filter_by(device_id=device.id, user_id=pair.user_id).first()
        if existing:
            existing.revoked_at = None
            existing.granted_at = utcnow()
            existing.granted_by = device.owner_id
        else:
            db.session.add(DeviceAccess(device_id=device.id, user_id=pair.user_id, granted_by=device.owner_id))
        for other in PairRequest.query.filter(PairRequest.device_id == device.id,
                                              PairRequest.user_id == pair.user_id,
                                              PairRequest.status == "pending",
                                              PairRequest.id != pair.id).all():
            other.status = "expired"
    db.session.commit()
    broadcast_user(pair.user_id, {"type": "pairing_update", "pair_request_id": pair.id,
                                  "device_id": device.id, "status": pair.status})
    _send(ws, {"type": "approval_recorded", "pair_request_id": pair.id, "status": pair.status})


@serialized
def _handle_device_message(ws, device, message):
    if not client_session_valid(device.credential):
        _device_error(ws, "登录已过期，请重新登录")
        return
    message_type = message.get("type")
    if message_type == "heartbeat":
        device.last_seen_at = utcnow()
        db.session.commit()
        return
    if message_type == "approval_response":
        _handle_approval(ws, device, message)
        return
    if message_type == "reset_code":
        new_code = str(message.get("connection_code", ""))
        if not re.fullmatch(r"[0-9]{9}", new_code):
            _send(ws, {"type": "error", "code": "invalid_code", "message": "邀请码必须是 9 位数字"})
            return
        collision = Device.query.filter(Device.connection_code_hash == hash_connection_code(new_code),
                                         Device.id != device.id).first()
        if collision:
            _send(ws, {"type": "error", "code": "code_in_use", "message": "邀请码重复，请重新生成"})
            return
        affected_user_ids = revoke_access(device, "客户端已重置邀请码")
        device.connection_code_hash = hash_connection_code(new_code)
        device.connection_code_last4 = new_code[-4:]
        device.code_invalidated = False
        db.session.commit()
        _send(ws, {"type": "code_reset_ack", "connection_code_last4": device.connection_code_last4, "connection_code": new_code})
        affected_user_ids.add(device.owner_id)
        for affected_user_id in affected_user_ids:
            broadcast_user(affected_user_id, {"type": "device_update", "device_id": device.id,
                                              "online": True, "code_invalidated": False})
        return
    if message_type == "device_status":
        request_id = str(message.get("request_id", ""))
        row = db.session.get(QuestionRequest, request_id)
        if row and row.device_id == device.id and row.status in {"waiting_capture", "capturing", "uploading"}:
            status = str(message.get("status", ""))
            if status in {"capturing", "uploading"}:
                row.status = status
                db.session.commit()
                broadcast_user(row.user_id, {"type": "request_update", "request": _request_payload(row)})
        return
    if message_type == "request_cancelled":
        request_id = str(message.get("request_id", ""))
        row = db.session.get(QuestionRequest, request_id)
        if row and row.device_id == device.id and row.status in {"waiting_capture", "capturing", "uploading"}:
            row.status = "cancelled"
            row.cancel_requested = True
            row.finished_at = utcnow()
            db.session.commit()
            broadcast_user(row.user_id, {"type": "request_update", "request": _request_payload(row)})
        return
    if message_type == "capture_failed":
        request_id = str(message.get("request_id", ""))
        row = db.session.get(QuestionRequest, request_id)
        if row and row.device_id == device.id and row.status in {"waiting_capture", "capturing", "uploading"}:
            row.status = "failed"
            row.error = str(message.get("error") or "客户端截图失败")[:2000]
            row.finished_at = utcnow()
            db.session.commit()
            broadcast_user(row.user_id, {"type": "request_update", "request": _request_payload(row)})
        return
    if message_type == "pong":
        device.last_seen_at = utcnow()
        db.session.commit()


def _broadcast_device(device, payload):
    user_ids = {device.owner_id}
    user_ids.update(access.user_id for access in DeviceAccess.query.filter_by(device_id=device.id, revoked_at=None).all())
    for user_id in user_ids:
        broadcast_user(user_id, payload)


def _request_payload(row):
    from .tasks import serialize_request
    return serialize_request(row)


@serialized
def _authenticate_device(ws, first):
    if first.get("client_version") != APP_VERSION:
        _device_error(ws, f"客户端与服务器版本不同，请安装 {APP_VERSION} 版客户端后重试", "version_mismatch")
        return
    token = str(first.get("token", ""))
    credential = ClientCredential.query.filter_by(token_hash=hash_token(token)).first()
    if not client_session_valid(credential):
        _device_error(ws, "登录已过期，请重新登录")
        return
    device = credential.device
    if not device or device.device_id != first.get("device_id"):
        _device_error(ws, "登录信息与这台电脑不匹配，请重新登录")
        return
    code = str(first.get("connection_code", ""))
    if not re.fullmatch(r"[0-9]{9}", code) or device.code_invalidated:
        code = new_connection_code()
    while Device.query.filter(Device.connection_code_hash == hash_connection_code(code), Device.id != device.id).first():
        code = new_connection_code()
    if device.connection_code_hash and device.connection_code_hash != hash_connection_code(code):
        affected = revoke_access(device, "这台电脑已更换邀请码")
        for user_id in affected:
            broadcast_user(user_id, {"type": "device_update", "device_id": device.id})
    else:
        stop_requests(device, "这台电脑已重新连接，请重试", CAPTURE_STATUSES)
        expire_pairings(device)
    device.connection_code_hash, device.connection_code_last4 = hash_connection_code(code), code[-4:]
    device.code_invalidated = False
    device.online = True
    device.client_version = APP_VERSION
    device.platform = str(first.get("platform") or "")[:80]
    device.connected_at = device.last_seen_at = credential.last_used_at = utcnow()
    db.session.commit()
    previous_socket = register_device(device.device_id, ws)
    if previous_socket and previous_socket is not ws:
        try:
            previous_socket.send(json.dumps({"type": "server_disconnect", "reason": "这台电脑已重新连接"}, ensure_ascii=False))
            previous_socket.close()
        except Exception:
            pass
    _send(ws, {"type": "hello_ack", "device_id": device.device_id, "server_version": APP_VERSION,
               "connection_code_last4": device.connection_code_last4, "connection_code": code})
    _broadcast_device(device, {"type": "device_update", "device_id": device.id,
                               "online": True, "name": device.name})
    return device


@sock.route("/ws/device")
def device_socket(ws):
    device = None
    try:
        first = _parse(ws.receive(timeout=10))
        if not first or first.get("type") != "hello":
            _device_error(ws, "无法连接，请重新登录")
            return
        device = _authenticate_device(ws, first)
        if device is None:
            return
        while True:
            raw = ws.receive(timeout=45)
            if raw is None:
                break
            message = _parse(raw)
            if message is None or message.get("type") == "close":
                break
            with state_lock:
                if not is_current_device(device.device_id, ws):
                    break
                _handle_device_message(ws, device, message)
    except Exception:
        db.session.rollback()
        current_app.logger.debug("设备实时连接已结束", exc_info=True)
    finally:
        with state_lock:
            if device and unregister_device(device.device_id, ws):
                db.session.expire_all()
                device.online = False
                device.last_seen_at = utcnow()
                db.session.commit()
                stop_requests(device, "客户端连接已断开，请重试", CAPTURE_STATUSES)
                expire_pairings(device)
                _broadcast_device(device, {"type": "device_update", "device_id": device.id, "online": False})


@sock.route("/ws/browser")
def browser_socket(ws):
    from .security import user_from_session
    origin = request.headers.get("Origin", "")
    expected = urlsplit(request.host_url)
    incoming = urlsplit(origin)
    if (incoming.scheme, incoming.netloc) != (expected.scheme, expected.netloc):
        ws.close()
        return
    user = user_from_session()
    if not user:
        try:
            ws.close()
        except Exception:
            pass
        return
    register_browser(ws, user.id, user.role == "admin")
    _send(ws, {"type": "connected"})
    try:
        while True:
            raw = ws.receive(timeout=20)
            db.session.remove()
            current = user_from_session()
            if not current or current.role != user.role:
                ws.close()
                break
            if raw is None:
                if not ws.connected:
                    break
                continue
            message = _parse(raw)
            if message is None:
                break
            if message.get("type") == "ping":
                _send(ws, {"type": "pong"})
    except Exception:
        current_app.logger.debug("浏览器实时连接已结束", exc_info=True)
    finally:
        unregister_browser(ws)
