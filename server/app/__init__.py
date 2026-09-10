import os
from flask import Flask, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

from .config import Config
from .extensions import db
from .api import api_bp
from .realtime import sock
from .presets import presets_bp
from .desktop import desktop_bp
from .computers import computers_bp
from .versioning import APP_VERSION, prepare_database


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
    app.register_blueprint(computers_bp, url_prefix="/api")
    app.register_blueprint(presets_bp, url_prefix="/api")
    app.register_blueprint(desktop_bp, url_prefix="/api/desktop")

    if app.config["MAX_DEVICES_PER_USER"] < 1 or app.config["CLIENT_SESSION_DAYS"] < 1:
        raise RuntimeError("设备数量上限和登录有效天数必须大于零")

    @app.before_request
    def check_client_version():
        from .security import error
        if request.path.startswith(("/api/desktop/", "/api/device/")):
            if request.headers.get("X-Client-Version") != APP_VERSION:
                return error(f"客户端与服务器版本不同，请安装 {APP_VERSION} 版客户端后重试", 426, "version_mismatch")
        if request.path.startswith(("/api/credentials", "/api/prompts", "/api/models")):
            return error("这个功能已更新，请刷新网页并使用预设页面", 410, "upgrade_required")

    @app.get("/api/version")
    def version():
        return {"version": APP_VERSION}

    @app.errorhandler(HTTPException)
    def http_error(exc):
        messages = {413: "截图不能超过配置的大小限制", 503: "服务器暂时繁忙，请稍后再试"}
        return {"error": {"code": "file_too_large" if exc.code == 413 else "http_error",
                          "message": messages.get(exc.code, "请求无效或资源不存在")}}, exc.code

    @app.get("/")
    def index():
        return send_from_directory(web_dir, "index.html")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": APP_VERSION}

    @app.after_request
    def security_headers(response):
        response.headers["X-Server-Version"] = APP_VERSION
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "font-src 'self' https://cdn.bootcdn.net; script-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:"
        )
        return response

    @app.cli.command("cleanup-retention")
    def cleanup_retention_command():
        from .maintenance import cleanup_retention
        print(cleanup_retention())

    with app.app_context():
        prepare_database(app)
        from .bootstrap import ensure_initial_admin
        ensure_initial_admin(app)

    return app
