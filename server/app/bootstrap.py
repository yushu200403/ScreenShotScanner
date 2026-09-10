import os

from werkzeug.security import generate_password_hash

from .extensions import db
from .models import User, utcnow


def ensure_initial_admin(app):
    username = os.getenv("INITIAL_ADMIN_USERNAME")
    password = os.getenv("INITIAL_ADMIN_PASSWORD")
    if not username or not password:
        return
    user = User.query.filter_by(username=username).first()
    if user:
        return
    user = User(username=username, password_hash=generate_password_hash(password, method="scrypt"),
                display_name=os.getenv("INITIAL_ADMIN_DISPLAY_NAME", "管理员"),
                role="admin", status="active", created_at=utcnow())
    db.session.add(user)
    db.session.commit()
