import json
from urllib.parse import urlsplit

from flask import Blueprint, jsonify
from sqlalchemy import or_

from .extensions import db
from .lifecycle import serialized
from .models import Device, DevicePreference, ModelProfile, Preset, PromptTemplate
from .security import csrf_required, encrypt_secret, error, json_body, login_required


presets_bp = Blueprint("presets", __name__)
PROVIDERS = {"deepseek", "openai_chat", "openai_responses", "gemini"}
EFFORTS = {"none", "low", "medium", "high", "xhigh"}


def visible(preset, user):
    return bool(preset and (preset.is_global or preset.owner_id == user.id))


def available(preset, user):
    return bool(visible(preset, user) and preset.enabled and preset.profile.enabled)


def summary(preset):
    return {"id": preset.id, "name": preset.name, "description": preset.description,
            "is_global": preset.is_global, "enabled": preset.enabled and preset.profile.enabled}


def preset_json(preset, user):
    result = summary(preset)
    result["editable"] = preset.owner_id == user.id or user.role == "admin"
    if result["editable"]:
        result.update(prompt=preset.prompt.content, provider=preset.profile.provider,
                      endpoint=preset.profile.endpoint, model_name=preset.model_name,
                      reasoning_effort=preset.reasoning_effort,
                      timeout_seconds=preset.profile.timeout_seconds,
                      options=json.loads(preset.profile.options_json), api_key_configured=True)
    return result


def selected_preset(device):
    preference = db.session.get(DevicePreference, device.id)
    return preference.preset if preference else None


def notify_preset(preset):
    from .realtime import _broadcast_device
    for preference in DevicePreference.query.filter_by(preset_id=preset.id).all():
        device = db.session.get(Device, preference.device_id)
        _broadcast_device(device, {"type": "device_update", "device_id": device.id})


def _text(payload, key, limit, required=False):
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ValueError("请检查填写的内容")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError({"name": "请填写预设名称，最多 120 字", "prompt": "请填写提示词，最多 20000 字",
                          "model_name": "请填写模型名称，最多 160 字", "endpoint": "请填写模型端点，最多 1000 字",
                          "api_key": "请填写 API 密钥，最多 4000 字"}.get(key, "用途说明最多 500 字"))
    return value


def save_preset(user, payload, preset=None):
    name = _text(payload, "name", 120, True)
    description = _text(payload, "description", 500)
    prompt = _text(payload, "prompt", 20000, True)
    model = _text(payload, "model_name", 160, True)
    endpoint = _text(payload, "endpoint", 1000, True)
    key = _text(payload, "api_key", 4000, not preset)
    provider = payload.get("provider")
    effort = payload.get("reasoning_effort", "none")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("请选择请求格式")
    if not isinstance(effort, str) or effort not in EFFORTS:
        raise ValueError("请选择推理强度")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("模型端点格式不正确，请填写完整的 HTTP/HTTPS API URL")
    try:
        parsed.port
        timeout = int(payload.get("timeout_seconds", 120))
    except (TypeError, ValueError):
        raise ValueError("请检查模型端点和请求超时")
    if not 10 <= timeout <= 600:
        raise ValueError("请求超时需为 10 至 600 秒")
    public = payload.get("is_global", False)
    if not isinstance(public, bool):
        raise ValueError("请选择是否设为公共预设")
    if public and user.role != "admin":
        raise ValueError("只有管理员可以发布公共预设")
    if preset and public != preset.is_global:
        raise ValueError("预设保存后不能更改使用范围，请新建预设")
    options = payload.get("options", {})
    if not isinstance(options, dict) or len(json.dumps(options)) > 20000:
        raise ValueError("高级参数无效")
    allowed = {"temperature", "top_p"}
    allowed.update({"max_tokens", "seed"} if provider in {"deepseek", "openai_chat"} else {"max_output_tokens"})
    if set(options) - allowed:
        raise ValueError("高级参数中包含不支持的选项")
    for option, value in options.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not float("-inf") < value < float("inf"):
            raise ValueError("高级参数需要填写有效数字")
        if option == "temperature" and not 0 <= value <= 2:
            raise ValueError("采样温度（temperature）需为 0 至 2")
        if option == "top_p" and not 0 <= value <= 1:
            raise ValueError("核采样概率（top_p）需为 0 至 1")
        if option in {"max_tokens", "max_output_tokens"} and (not isinstance(value, int) or value < 1):
            raise ValueError("输出 Token 上限需要填写正整数")
        if option == "seed" and not isinstance(value, int):
            raise ValueError("随机种子（seed）需要填写整数")
    owner_id = preset.owner_id if preset else user.id
    template = PromptTemplate(owner_id=owner_id, name=name, content=prompt, is_global=public)
    profile = ModelProfile(owner_id=owner_id, name=name, provider=provider, endpoint=endpoint,
                           api_key_ciphertext=encrypt_secret(key) if key else preset.profile.api_key_ciphertext,
                           models_json=json.dumps([model]), timeout_seconds=timeout,
                           options_json=json.dumps(options), is_global=public)
    db.session.add_all([template, profile])
    db.session.flush()
    if not preset:
        preset = Preset(owner_id=user.id)
        db.session.add(preset)
    preset.name, preset.description = name, description
    preset.is_global = public
    preset.prompt_id, preset.model_profile_id = template.id, profile.id
    preset.model_name, preset.reasoning_effort = model, effort
    db.session.commit()
    db.session.refresh(preset)
    return preset


@presets_bp.get("/presets")
@login_required
def list_presets(user):
    rows = Preset.query.filter(or_(Preset.is_global.is_(True), Preset.owner_id == user.id)).order_by(Preset.created_at.desc()).all()
    return jsonify(items=[preset_json(row, user) for row in rows])


@presets_bp.post("/presets")
@serialized
@login_required
def create_preset(user):
    denied = csrf_required()
    if denied:
        return denied
    try:
        row = save_preset(user, json_body())
    except ValueError as exc:
        return error(str(exc))
    return jsonify(preset=preset_json(row, user)), 201


@presets_bp.put("/presets/<int:preset_id>")
@serialized
@login_required
def update_preset(user, preset_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(Preset, preset_id)
    if not row or not visible(row, user) or (row.owner_id != user.id and user.role != "admin"):
        return error("预设不存在或无法修改", 404, "not_found")
    try:
        row = save_preset(user, json_body(), row)
    except ValueError as exc:
        return error(str(exc))
    notify_preset(row)
    return jsonify(preset=preset_json(row, user))


@presets_bp.patch("/presets/<int:preset_id>")
@serialized
@login_required
def set_preset_enabled(user, preset_id):
    denied = csrf_required()
    if denied:
        return denied
    row = db.session.get(Preset, preset_id)
    if not row or not visible(row, user) or (row.owner_id != user.id and user.role != "admin"):
        return error("预设不存在或无法修改", 404, "not_found")
    enabled = json_body().get("enabled")
    if not isinstance(enabled, bool):
        return error("请选择启用或停用")
    row.enabled = enabled
    db.session.commit()
    notify_preset(row)
    return jsonify(preset=preset_json(row, user))
