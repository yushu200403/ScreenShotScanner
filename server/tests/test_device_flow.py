from app.extensions import db
from app.models import Device, QuestionRequest
from test_lifecycle import flow, ask


def test_preset_and_force_cancel(client, app, flow):
    first = ask(client, flow, model_name="不允许覆盖的模型", reasoning_effort="high")
    assert first.status_code == 202
    assert first.json["request"]["model_name"] == "vision"
    assert first.json["request"]["reasoning_effort"] == "none"
    assert first.json["request"]["preset_name"] == "测试预设"
    first_id = first.json["request"]["id"]
    assert ask(client, flow).status_code == 409
    assert ask(client, flow, force=True).status_code == 202
    with app.app_context():
        assert db.session.get(QuestionRequest, first_id).status == "cancelled"
        assert db.session.get(Device, flow["device_id"]).default_model_name == "vision"
