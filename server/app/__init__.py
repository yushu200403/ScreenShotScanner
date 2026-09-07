import os
from flask import Flask, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

from .config import Config
from .extensions import db
from .api import api_bp
from .realtime import sock


def create_app():
    web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))
    app = Flask(__name__, static_folder=web_dir, static_url_path="/assets")
    app.config.from_object(Config)
    for key in ("SECRET_KEY", "ENCRYPTION_KEY"):
        value = app.config.get(key, "")
        if not value or str(value).startswith("replace-with"):
            raise RuntimeError(f"必须配置安全的 {key}")
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    sock.init_app(app)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.errorhandler(HTTPException)
    def http_error(exc):
        messages = {413: "截图不能超过配置的大小限制", 503: "限流服务暂时不可用，请稍后重试"}
        return {"error": {"code": "file_too_large" if exc.code == 413 else "http_error",
                          "message": messages.get(exc.code, "请求无效或资源不存在")}}, exc.code

    @app.get("/")
    def index():
        return send_from_directory(web_dir, "index.html")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:"
        )
        return response

    @app.cli.command("cleanup-retention")
    def cleanup_retention_command():
        from .maintenance import cleanup_retention
        print(cleanup_retention())

    with app.app_context():
        db.create_all()
        from .bootstrap import ensure_initial_admin
        ensure_initial_admin(app)

    return app
