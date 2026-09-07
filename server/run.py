import argparse

from app import create_app
from app.lifecycle import start_runtime
from app.development import WebSocketRequestHandler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动本地屏幕问答服务")
    parser.add_argument("--port", type=int, default=8000, help="本地监听端口")
    args = parser.parse_args()
    app = create_app()
    start_runtime(app)
    app.run(host="127.0.0.1", port=args.port, threaded=True, use_reloader=False,
            request_handler=WebSocketRequestHandler)
