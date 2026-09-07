import json
import threading
from concurrent.futures import ThreadPoolExecutor


_lock = threading.RLock()
_device_sockets = {}
_browser_sockets = {}
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="screen-request")
_cancel_events = {}


def register_device(device_id, websocket):
    with _lock:
        previous = _device_sockets.get(device_id)
        _device_sockets[device_id] = websocket
        return previous


def unregister_device(device_id, websocket):
    with _lock:
        if _device_sockets.get(device_id) is websocket:
            _device_sockets.pop(device_id, None)
            return True
        return False


def device_connected(device_id):
    with _lock:
        return device_id in _device_sockets


def is_current_device(device_id, websocket):
    with _lock:
        return _device_sockets.get(device_id) is websocket


def close_device(device_id, reason):
    with _lock:
        websocket = _device_sockets.get(device_id)
    if websocket:
        try:
            websocket.send(json.dumps({"type": "server_disconnect", "reason": reason}, ensure_ascii=False))
        except Exception:
            pass
        try:
            websocket.close()
        except Exception:
            pass


def close_browsers(user_id):
    with _lock:
        sockets = [ws for ws, (owner_id, _) in _browser_sockets.items() if owner_id == user_id]
        for ws in sockets:
            _browser_sockets.pop(ws, None)
    for ws in sockets:
        try:
            ws.close()
        except Exception:
            pass


def send_device(device_id, payload):
    with _lock:
        websocket = _device_sockets.get(device_id)
        if not websocket:
            return False
        try:
            websocket.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            return False


def register_browser(websocket, user_id, is_admin):
    with _lock:
        _browser_sockets[websocket] = (user_id, is_admin)


def unregister_browser(websocket):
    with _lock:
        _browser_sockets.pop(websocket, None)


def broadcast_user(user_id, payload):
    message = json.dumps(payload, ensure_ascii=False)
    dead = []
    with _lock:
        for websocket, (connected_user_id, is_admin) in list(_browser_sockets.items()):
            if connected_user_id != user_id and not is_admin:
                continue
            try:
                websocket.send(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            _browser_sockets.pop(websocket, None)


def add_cancel_event(request_id):
    event = threading.Event()
    with _lock:
        _cancel_events[request_id] = event
    return event


def get_cancel_event(request_id):
    with _lock:
        return _cancel_events.get(request_id)


def remove_cancel_event(request_id):
    with _lock:
        _cancel_events.pop(request_id, None)


def submit_request(app, request_id):
    _executor.submit(_run_request, app, request_id)


def _run_request(app, request_id):
    from .tasks import run_model_request
    run_model_request(app, request_id)
