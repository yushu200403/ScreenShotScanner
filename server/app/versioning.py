import json
from pathlib import Path

from sqlalchemy import inspect, text

from .extensions import db


RELEASE = json.loads(Path(__file__).with_name("release.json").read_text(encoding="utf-8"))
APP_VERSION = RELEASE["version"]
DATABASE_VERSION = RELEASE["database_version"]


def prepare_database(app):
    from .models import DatabaseVersion
    tables = set(inspect(db.engine).get_table_names())
    owned = set(db.metadata.tables)
    if tables & owned:
        version = None
        if "database_version" in tables:
            with db.engine.connect() as connection:
                version = connection.execute(text("SELECT version FROM database_version WHERE id = 1")).scalar()
        if version is not None and version > DATABASE_VERSION:
            raise RuntimeError("数据库来自更新的服务端，请升级服务端后再启动")
        if version != DATABASE_VERSION:
            app.logger.warning("检测到旧版本数据库，正在清空屏幕问答器数据并重新初始化")
            db.drop_all()
    db.create_all()
    if not db.session.get(DatabaseVersion, 1):
        db.session.add(DatabaseVersion(id=1, version=DATABASE_VERSION))
        db.session.commit()
