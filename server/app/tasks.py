from .adapters import AdapterError, stream_profile
from .extensions import db
from .models import QuestionRequest, utcnow, as_utc
from .runtime import add_cancel_event, broadcast_user, get_cancel_event, remove_cancel_event


def _notify(row, event="request_update", delta=None, reasoning_delta=None):
    broadcast_user(row.user_id, {
        "type": event,
        "request": serialize_request(row),
        "delta": delta,
        "reasoning_delta": reasoning_delta,
    })


def serialize_request(row):
    return {
        "id": row.id,
        "device_id": row.device_id,
        "status": row.status,
        "model_name": row.model_name,
        "reasoning_effort": row.reasoning_effort,
        "question": row.question or "",
        "reasoning": row.reasoning or "",
        "answer": row.answer or "",
        "error": row.error,
        "created_at": as_utc(row.created_at).isoformat() if row.created_at else None,
        "started_at": as_utc(row.started_at).isoformat() if row.started_at else None,
        "finished_at": as_utc(row.finished_at).isoformat() if row.finished_at else None,
        "screenshot_available": bool(row.screenshot_path),
        "screenshot_url": f"/api/requests/{row.id}/image" if row.screenshot_path else None,
    }


def run_model_request(app, request_id):
    from .lifecycle import state_lock
    with app.app_context():
        with state_lock:
            row = db.session.get(QuestionRequest, request_id)
            if not row or row.cancel_requested or row.status != "processing":
                return
            event = get_cancel_event(request_id) or add_cancel_event(request_id)
            profile = row.model_profile
            model, prompt, question = row.model_name, row.prompt.content, row.question
            image_path, effort = row.screenshot_path, row.reasoning_effort
            for field in ("provider", "endpoint", "api_key_ciphertext", "timeout_seconds", "options_json"):
                getattr(profile, field)
            db.session.expunge(profile)
            db.session.commit()
            _notify(row)
        failure = None
        try:
            def on_event(item):
                with state_lock:
                    db.session.expire_all()
                    current = db.session.get(QuestionRequest, request_id)
                    if event.is_set() or not current or current.cancel_requested or current.status != "processing":
                        event.set()
                        return
                    if item.kind == "reasoning":
                        current.reasoning = (current.reasoning or "") + item.text
                    else:
                        current.answer = (current.answer or "") + item.text
                    db.session.commit()
                    _notify(current, delta=item.text if item.kind != "reasoning" else None,
                            reasoning_delta=item.text if item.kind == "reasoning" else None)

            stream_profile(profile, model, prompt, question, image_path, on_event, event, effort)
        except (AdapterError, OSError, ValueError) as exc:
            failure = str(exc)
        except Exception:
            app.logger.exception("模型处理失败")
            failure = "服务端处理失败，请联系管理员查看日志"
        finally:
            with state_lock:
                db.session.rollback()
                current = db.session.get(QuestionRequest, request_id)
                if current and current.status == "processing":
                    if current.cancel_requested or event.is_set():
                        current.status = "cancelled"
                    elif failure:
                        current.status = "failed"
                        current.error = failure
                    else:
                        current.status = "completed"
                    current.finished_at = utcnow()
                    db.session.commit()
                    _notify(current)
                remove_cancel_event(request_id)
