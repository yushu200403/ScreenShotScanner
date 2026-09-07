import os

from werkzeug.security import generate_password_hash

from .extensions import db
from .models import User, PromptTemplate, utcnow


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
    if not PromptTemplate.query.filter_by(is_global=True).first():
        db.session.add(PromptTemplate(
            owner_id=user.id,
            name="屏幕内容解释",
            description="解释当前显示器上的主要内容并给出清晰结论",
            content="请分析这张屏幕截图中的主要内容。识别关键文字、界面状态或题目，并用中文给出准确、结构化的解释。对于无法确认的内容，请明确说明不确定性。",
            is_global=True,
        ))
        db.session.commit()
