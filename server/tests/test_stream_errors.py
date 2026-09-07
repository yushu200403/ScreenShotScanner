import json
from threading import Event
from types import SimpleNamespace

import pytest

from app.adapters import (AdapterError, _checked_stream, _emit_chat, _emit_responses,
                          _emit_gemini, _gemini_url, _request_options)


@pytest.mark.parametrize("emitter,payload", [
    (_emit_chat, {"error": {"message": "模型额度不足"}}),
    (_emit_chat, {"choices": [{"finish_reason": "length"}]}),
    (_emit_responses, {"type": "response.failed", "response": {"error": {"message": "模型失败"}}}),
    (_emit_responses, {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}),
    (_emit_responses, {"type": "error", "message": "模型失败"}),
    (_emit_gemini, {"error": {"message": "模型失败"}}),
    (_emit_gemini, {"candidates": [{"finishReason": "MAX_TOKENS"}]}),
])
def test_errors_are_not_reported_as_success(emitter, payload):
    with pytest.raises(AdapterError):
        emitter(payload, lambda event: None)


def test_truncated_stream_is_rejected():
    response = SimpleNamespace(iter_lines=lambda **kwargs: iter([
        'data: {"choices":[{"delta":{"content":"部分回答"}}]}', ""
    ]))
    with pytest.raises(AdapterError, match="意外结束"):
        list(_checked_stream(response, "openai_chat", Event()))


@pytest.mark.parametrize("provider,terminal", [
    ("openai_chat", {"choices": [{"finish_reason": "stop"}]}),
    ("openai_responses", {"type": "response.completed"}),
    ("gemini", {"candidates": [{"finishReason": "STOP"}]}),
])
def test_standard_stream_completion(provider, terminal):
    response = SimpleNamespace(iter_lines=lambda **kwargs: iter(["data: " + json.dumps(terminal), ""]))
    assert list(_checked_stream(response, provider, Event())) == [terminal]


def test_provider_options_are_separate():
    options = json.dumps({"max_tokens": 100, "max_output_tokens": 200, "include_thoughts": True})
    chat = _request_options(SimpleNamespace(provider="openai_chat", options_json=options), "vision")
    responses = _request_options(SimpleNamespace(provider="openai_responses", options_json=options), "vision")
    assert chat == {"model": "vision", "stream": True, "max_tokens": 100}
    assert responses == {"model": "vision", "stream": True, "max_output_tokens": 200}


def test_gemini_url_does_not_contain_key():
    assert _gemini_url("https://example.com/{model}?alt=sse&key=secret", "vision") == "https://example.com/vision?alt=sse"
