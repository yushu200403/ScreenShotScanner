import os
from datetime import timedelta

from flask import current_app

from .extensions import db
from .models import QuestionRequest, utcnow


def cleanup_retention():
    screenshot_cutoff = utcnow() - timedelta(days=current_app.config["SCREENSHOT_RETENTION_DAYS"])
    result_cutoff = utcnow() - timedelta(days=current_app.config["RESULT_RETENTION_DAYS"])
    removed_files = 0
    cleared_results = 0
    for row in QuestionRequest.query.filter(QuestionRequest.created_at < screenshot_cutoff,
                                             QuestionRequest.screenshot_path.is_not(None)).all():
        try:
            os.remove(row.screenshot_path)
            removed_files += 1
        except FileNotFoundError:
            pass
        row.screenshot_path = None
        row.screenshot_size = None
    for row in QuestionRequest.query.filter(QuestionRequest.created_at < result_cutoff).all():
        if row.reasoning or row.answer or row.question:
            row.reasoning = ""
            row.answer = ""
            row.question = ""
            row.error = "历史内容已按保留策略清理"
            cleared_results += 1
    db.session.commit()
    return {"removed_screenshots": removed_files, "cleared_results": cleared_results}
