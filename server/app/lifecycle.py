import threading
from datetime import timedelta
from functools import wraps

from .extensions import db
from .models import Device, DeviceAccess, PairRequest, QuestionRequest, utcnow
from .runtime import broadcast_user, close_device, get_cancel_event, send_device


ACTIVE_STATUSES = {"waiting_capture", "capturing", "uploading", "processing"}
CAPTURE_STATUSES = ACTIVE_STATUSES - {"processing"}
state_lock = threading.RLock()


def serialized(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        with state_lock:
            db.session.expire_all()
            return view(*args, **kwargs)
    return wrapped


def notify_request(row):
    from .tasks import serialize_request
    broadcast_user(row.user_id, {"type": "request_update", "request": serialize_request(row)})


def stop_requests(device, reason, statuses=ACTIVE_STATUSES):
    rows = QuestionRequest.query.filter(QuestionRequest.device_id == device.id,
                                       QuestionRequest.status.in_(statuses)).all()
    for row in rows:
        row.cancel_requested = True
        row.status = "cancelled"
        row.error = reason
        row.finished_at = utcnow()
        event = get_cancel_event(row.id)
        if event:
            event.set()
        send_device(device.device_id, {"type": "cancel_request", "request_id": row.id})
    db.session.commit()
    for row in rows:
        notify_request(row)


def expire_pairings(device):
    rows = PairRequest.query.filter_by(device_id=device.id, status="pending").all()
    for row in rows:
        row.status = "expired"
        row.decided_at = utcnow()
    db.session.commit()
    for row in rows:
        broadcast_user(row.user_id, {"type": "pairing_update", "pair_request_id": row.id,
                                      "device_id": device.id, "status": "expired"})


def revoke_access(device, reason):
    stop_requests(device, reason)
    expire_pairings(device)
    grants = DeviceAccess.query.filter_by(device_id=device.id, revoked_at=None).all()
    affected = {device.owner_id}
    for grant in grants:
        affected.add(grant.user_id)
        grant.revoked_at = utcnow()
    db.session.commit()
    return affected


def disconnect(device, reason):
    stop_requests(device, reason)
    expire_pairings(device)
    device.online = False
    db.session.commit()
    close_device(device.device_id, reason)
    from .realtime import _broadcast_device
    _broadcast_device(device, {"type": "device_update", "device_id": device.id, "online": False})


def recover_runtime():
    with state_lock:
        Device.query.update({Device.online: False})
        QuestionRequest.query.filter(QuestionRequest.status.in_(ACTIVE_STATUSES)).update({
            QuestionRequest.status: "failed", QuestionRequest.error: "服务重启，请重新发起请求",
            QuestionRequest.finished_at: utcnow(), QuestionRequest.cancel_requested: True,
        }, synchronize_session=False)
        PairRequest.query.filter_by(status="pending").update({PairRequest.status: "expired"})
        db.session.commit()


def expire_captures(app):
    with app.app_context(), state_lock:
        deadline = utcnow() - timedelta(seconds=app.config["CAPTURE_TIMEOUT_SECONDS"])
        rows = QuestionRequest.query.filter(QuestionRequest.status.in_(CAPTURE_STATUSES),
                                            QuestionRequest.created_at < deadline).all()
        for row in rows:
            row.status = "failed"
            row.error = "客户端截屏或上传超时，请重试"
            row.finished_at = utcnow()
            send_device(row.device.device_id, {"type": "cancel_request", "request_id": row.id})
        db.session.commit()
        for row in rows:
            notify_request(row)


def start_runtime(app):
    if app.extensions.get("runtime_started"):
        return
    with app.app_context():
        recover_runtime()
    app.extensions["runtime_started"] = True

    def monitor():
        while True:
            threading.Event().wait(5)
            try:
                expire_captures(app)
            except Exception:
                app.logger.exception("检查截图超时失败")

    threading.Thread(target=monitor, name="截图超时检查", daemon=True).start()
