import base64
import json
import mimetypes
import threading
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

import requests

from .security import decrypt_secret


class AdapterError(Exception):
    pass


@dataclass
class StreamEvent:
    kind: str
    text: str


def _instruction(prompt, question):
    question = (question or "").strip()
    if question:
        return f"{prompt.strip()}\n\n用户补充问题：{question}"
    return prompt.strip()


def _image_data(image_path):
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    with open(image_path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return mime, encoded, f"data:{mime};base64,{encoded}"


def _options(profile):
    try:
        value = json.loads(profile.options_json or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _models(profile):
    try:
        value = json.loads(profile.models_json or "[]")
        return [str(item) for item in value if str(item).strip()]
    except json.JSONDecodeError:
        return []


def _request_options(profile, model, reasoning_effort=None):
    options = _options(profile)
    result = {"model": model, "stream": True}
    accepted = {"temperature", "top_p", "reasoning_effort"}
    if profile.provider in {"deepseek", "openai_chat"}:
        accepted.update({"max_tokens", "stop", "presence_penalty", "frequency_penalty", "seed"})
    elif profile.provider == "openai_responses":
        accepted.update({"max_output_tokens", "reasoning_summary"})
    elif profile.provider == "gemini":
        accepted.update({"max_output_tokens", "thinking_budget", "include_thoughts", "candidate_count", "stop"})
    for key in accepted:
        if key in options and options[key] not in (None, ""):
            result[key] = options[key]
    if reasoning_effort == "none":
        result.pop("reasoning_effort", None)
    elif reasoning_effort:
        result["reasoning_effort"] = reasoning_effort
    return result


def _response_error(response):
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("error", payload)
            if isinstance(detail, dict):
                return str(detail.get("message") or detail.get("code") or detail)
            return str(detail)
    except ValueError:
        pass
    return response.text[:500] or f"HTTP {response.status_code}"


def _decode_sse_data(data):
    if not data or data == "[DONE]":
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise AdapterError("模型返回了非标准 SSE JSON 数据") from exc


def _iter_sse(response):
    response.encoding = "utf-8"
    data_lines = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8", errors="strict")
        line = (raw_line or "").rstrip("\r")
        if not line:
            if data_lines:
                payload = _decode_sse_data("\n".join(data_lines))
                data_lines.clear()
                if payload is not None:
                    yield payload
            continue
        if line.startswith(":") or line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if not data_lines and line.lstrip().startswith(("{", "[")):
            payload = _decode_sse_data(line.strip())
            if payload is not None:
                yield payload
    if data_lines:
        payload = _decode_sse_data("\n".join(data_lines))
        if payload is not None:
            yield payload


def _watch_cancellation(response, cancel_event):
    done = threading.Event()

    def monitor():
        while not done.wait(0.2):
            if cancel_event.is_set():
                response.close()
                return

    threading.Thread(target=monitor, name="model-stream-cancel", daemon=True).start()
    return done


def _checked_stream(response, provider, cancel_event):
    completed = False
    for payload in _iter_sse(response):
        if cancel_event.is_set():
            return
        if isinstance(payload, dict):
            if provider == "openai_responses":
                completed = completed or payload.get("type") == "response.completed"
            elif provider == "gemini":
                completed = completed or any(c.get("finishReason") == "STOP" for c in payload.get("candidates", []) if isinstance(c, dict))
            else:
                completed = completed or any(c.get("finish_reason") == "stop" for c in payload.get("choices", []) if isinstance(c, dict))
        yield payload
    if not completed and not cancel_event.is_set():
        raise AdapterError("模型数据流意外结束，回答可能不完整，请重试")


def _post(url, api_key, body, timeout, headers=None, params=None):
    request_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        response = requests.post(url, headers=request_headers, params=params, json=body,
                                 stream=True, timeout=(10, timeout))
    except requests.RequestException as exc:
        raise AdapterError("模型网络连接失败，请检查端点和网络") from exc
    if response.status_code >= 400:
        detail = _response_error(response)
        response.close()
        raise AdapterError(f"模型接口返回 HTTP {response.status_code}：{detail}")
    return response


def _emit_chat(payload, on_event):
    if not isinstance(payload, dict):
        raise AdapterError("Chat Completions 响应不是标准 JSON 对象")
    _raise_payload_error(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return
    choice = choices[0]
    if not isinstance(choice, dict):
        raise AdapterError("Chat Completions choices 格式无效")
    if choice.get("finish_reason") in {"length", "content_filter"}:
        raise AdapterError("模型输出未完整结束：" + choice["finish_reason"])
    delta = choice.get("delta")
    message = choice.get("message")
    data = delta if isinstance(delta, dict) else message if isinstance(message, dict) else None
    if data is None:
        return
    reasoning = data.get("reasoning_content")
    content = data.get("content")
    if isinstance(reasoning, str) and reasoning:
        on_event(StreamEvent("reasoning", reasoning))
    if isinstance(content, str) and content:
        on_event(StreamEvent("answer", content))


def _raise_payload_error(payload):
    detail = payload.get("error")
    if detail or payload.get("type") == "error":
        detail = detail or payload
        message = detail.get("message", "模型接口返回错误") if isinstance(detail, dict) else str(detail)
        raise AdapterError(str(message))


def stream_chat(profile, model, prompt, question, image_path, on_event, cancel_event, reasoning_effort=None):
    _, _, image_url = _image_data(image_path)
    options = _request_options(profile, model, reasoning_effort)
    body = {
        **options,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _instruction(prompt, question)},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]}],
    }
    response = _post(profile.endpoint, decrypt_secret(profile.api_key_ciphertext), body,
                     profile.timeout_seconds)
    done = _watch_cancellation(response, cancel_event)
    emitted = False

    def emit(item):
        nonlocal emitted
        emitted = emitted or (item.kind == "answer" and bool(item.text))
        on_event(item)

    try:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for payload in _checked_stream(response, profile.provider, cancel_event):
                if cancel_event.is_set():
                    break
                _emit_chat(payload, emit)
        else:
            try:
                _emit_chat(response.json(), emit)
            except ValueError as exc:
                raise AdapterError("Chat Completions 响应不是标准 JSON") from exc
        if not emitted and not cancel_event.is_set():
            raise AdapterError("Chat Completions 未返回标准文本内容")
    finally:
        done.set()
        response.close()


def _emit_responses(payload, on_event):
    if not isinstance(payload, dict):
        raise AdapterError("Responses 响应不是标准 JSON 对象")
    _raise_payload_error(payload)
    event_type = payload.get("type")
    if event_type in {"response.failed", "response.incomplete"} or payload.get("status") in {"failed", "incomplete", "cancelled"}:
        details = payload.get("response", payload)
        reason = details.get("error") or details.get("incomplete_details") or {}
        raise AdapterError("模型输出失败或未完成：" + str(reason.get("message") or reason.get("reason") or "响应中断"))
    if event_type == "response.refusal.delta":
        if payload.get("delta"):
            on_event(StreamEvent("answer", payload["delta"]))
        return
    if event_type == "response.output_text.delta":
        text = payload.get("delta")
        if isinstance(text, str) and text:
            on_event(StreamEvent("answer", text))
        return
    if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
        text = payload.get("delta")
        if isinstance(text, str) and text:
            on_event(StreamEvent("reasoning", text))
        return
    if event_type:
        return
    output = payload.get("output")
    if not isinstance(output, list):
        return
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "reasoning":
            summary = item.get("summary")
            if isinstance(summary, list):
                for part in summary:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        on_event(StreamEvent("reasoning", part["text"]))
        elif item_type == "message":
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    on_event(StreamEvent("answer", part["text"]))
                elif isinstance(part, dict) and part.get("type") == "refusal":
                    on_event(StreamEvent("answer", part.get("refusal", "模型拒绝回答")))


def stream_responses(profile, model, prompt, question, image_path, on_event, cancel_event, reasoning_effort=None):
    _, _, image_url = _image_data(image_path)
    options = _request_options(profile, model, reasoning_effort)
    reasoning_effort = options.pop("reasoning_effort", None)
    reasoning_summary = options.pop("reasoning_summary", "auto")
    body = {
        **options,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": _instruction(prompt, question)},
            {"type": "input_image", "image_url": image_url, "detail": "high"},
        ]}],
    }
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort, "summary": reasoning_summary}
    response = _post(profile.endpoint, decrypt_secret(profile.api_key_ciphertext), body,
                     profile.timeout_seconds)
    done = _watch_cancellation(response, cancel_event)
    emitted = False

    def emit(item):
        nonlocal emitted
        emitted = emitted or (item.kind == "answer" and bool(item.text))
        on_event(item)

    try:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for payload in _checked_stream(response, profile.provider, cancel_event):
                if cancel_event.is_set():
                    break
                _emit_responses(payload, emit)
        else:
            try:
                _emit_responses(response.json(), emit)
            except ValueError as exc:
                raise AdapterError("Responses 响应不是标准 JSON") from exc
        if not emitted and not cancel_event.is_set():
            raise AdapterError("Responses 未返回标准文本内容")
    finally:
        done.set()
        response.close()


def _gemini_url(endpoint, model):
    url = endpoint.replace("{model}", model)
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("key", None)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _emit_gemini(payload, on_event):
    if not isinstance(payload, dict):
        raise AdapterError("Gemini 响应不是标准 JSON 对象")
    _raise_payload_error(payload)
    if payload.get("promptFeedback", {}).get("blockReason"):
        raise AdapterError("Gemini 拒绝处理输入：" + payload["promptFeedback"]["blockReason"])
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("finishReason") not in {None, "STOP"}:
            raise AdapterError("Gemini 输出未完整结束：" + str(candidate["finishReason"]))
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not isinstance(text, str) or not text:
                continue
            on_event(StreamEvent("reasoning" if part.get("thought") is True else "answer", text))


def stream_gemini(profile, model, prompt, question, image_path, on_event, cancel_event, reasoning_effort=None):
    mime, encoded, _ = _image_data(image_path)
    options = _request_options(profile, model, reasoning_effort)
    options.pop("reasoning_effort", None)
    effort_budgets = {"none": 0, "low": 1024, "medium": 4096, "high": 8192, "xhigh": 16384}
    reasoning_budget = effort_budgets.get(reasoning_effort, options.pop("thinking_budget", None))
    include_thoughts = options.pop("include_thoughts", True)
    generation = {}
    generation_keys = {
        "temperature": "temperature",
        "top_p": "topP",
        "max_output_tokens": "maxOutputTokens",
        "candidate_count": "candidateCount",
        "stop": "stopSequences",
    }
    for source_key, target_key in generation_keys.items():
        if source_key in options:
            generation[target_key] = options[source_key]
    thinking = {}
    if reasoning_budget not in (None, ""):
        thinking["thinkingBudget"] = int(reasoning_budget)
    if include_thoughts is not None:
        thinking["includeThoughts"] = bool(include_thoughts)
    if thinking:
        generation["thinkingConfig"] = thinking
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": _instruction(prompt, question)},
            {"inline_data": {"mime_type": mime, "data": encoded}},
        ]}],
    }
    if generation:
        body["generationConfig"] = generation
    api_key = decrypt_secret(profile.api_key_ciphertext)
    url = _gemini_url(profile.endpoint, model)
    try:
        response = requests.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, json=body,
                                 stream=True, timeout=(10, profile.timeout_seconds))
    except requests.RequestException as exc:
        raise AdapterError("Gemini 网络连接失败，请检查端点和网络") from exc
    if response.status_code >= 400:
        detail = _response_error(response)
        response.close()
        raise AdapterError(f"Gemini 返回 HTTP {response.status_code}：{detail}")
    done = _watch_cancellation(response, cancel_event)
    emitted = False

    def emit(item):
        nonlocal emitted
        emitted = emitted or (item.kind == "answer" and bool(item.text))
        on_event(item)

    try:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            payloads = _checked_stream(response, profile.provider, cancel_event)
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                raise AdapterError("Gemini 响应不是标准 JSON") from exc
            payloads = payload if isinstance(payload, list) else [payload]
        for payload in payloads:
            if cancel_event.is_set():
                break
            _emit_gemini(payload, emit)
        if not emitted and not cancel_event.is_set():
            raise AdapterError("Gemini 未返回标准文本内容")
    finally:
        done.set()
        response.close()


def stream_profile(profile, model, prompt, question, image_path, on_event, cancel_event, reasoning_effort=None):
    provider = profile.provider
    if provider in {"deepseek", "openai_chat"}:
        return stream_chat(profile, model, prompt, question, image_path, on_event, cancel_event, reasoning_effort)
    if provider == "openai_responses":
        return stream_responses(profile, model, prompt, question, image_path, on_event, cancel_event, reasoning_effort)
    if provider == "gemini":
        return stream_gemini(profile, model, prompt, question, image_path, on_event, cancel_event, reasoning_effort)
    raise AdapterError(f"不支持的模型协议：{provider}")


def model_names(profile):
    return _models(profile)
