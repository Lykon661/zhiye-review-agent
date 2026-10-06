import json

import pytest

from app.llm import ModelClient, parse_json
from app.config import Settings


def test_parse_json_accepts_reasoning_and_markdown_wrappers():
    value = parse_json('<think>整理资料</think>\n```json\n{"core_points": []}\n```\n')
    assert value == {"core_points": []}


def test_parse_json_rejects_truncated_json_without_fabricating_result():
    with pytest.raises(ValueError, match="JSON 无法解析"):
        parse_json('{"core_points": [{"text": "未完成')


def test_model_client_passes_json_mode_to_pi_runtime(monkeypatch, tmp_path):
    calls = []

    def events(request, timeout):
        calls.append(request)
        return iter([{"type": "done", "text": json.dumps({"ok": True}), "stop_reason": "stop"}])

    client = ModelClient(Settings(data_dir=tmp_path, llm_api_key="k", llm_base_url="https://example.com/v1", llm_model="m"))
    monkeypatch.setattr(client, "_events", events)
    assert client.complete([{"role": "user", "content": "json"}], json_mode=True) == '{"ok": true}'
    assert calls[0]["action"] == "complete"
    assert calls[0]["json_mode"] is True
    assert calls[0]["config"]["api"] == "openai-completions"
    assert calls[0]["config"]["api_key"] == "k"
