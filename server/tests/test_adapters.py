from app.adapters import _emit_chat, _emit_gemini, _emit_responses, _iter_sse


def collect(emitter, payload):
    result = []
    emitter(payload, result.append)
    return [(item.kind, item.text) for item in result]


def test_deepseek_reasoning_and_answer_are_separate():
    payload = {"choices": [{"delta": {"reasoning_content": "分析", "content": "答案"}}]}
    assert collect(_emit_chat, payload) == [("reasoning", "分析"), ("answer", "答案")]


def test_openai_responses_standard_events():
    reasoning = {"type": "response.reasoning_summary_text.delta", "delta": "先检查"}
    answer = {"type": "response.output_text.delta", "delta": "最终答案"}
    assert collect(_emit_responses, reasoning) == [("reasoning", "先检查")]
    assert collect(_emit_responses, answer) == [("answer", "最终答案")]


def test_gemini_thought_parts_are_separate():
    payload = {"candidates": [{"content": {"parts": [
        {"text": "推理", "thought": True}, {"text": "结论"}
    ]}}]}
    assert collect(_emit_gemini, payload) == [("reasoning", "推理"), ("answer", "结论")]


def test_multiline_sse_data_is_parsed_as_one_event():
    class Response:
        @staticmethod
        def iter_lines(decode_unicode=True):
            return iter(["event: response.output_text.delta", "data: {\"type\":", "data: \"response.output_text.delta\", \"delta\": \"A\"}", ""])

    assert list(_iter_sse(Response())) == [{"type": "response.output_text.delta", "delta": "A"}]
