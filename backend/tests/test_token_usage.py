"""Usage contracts with synthetic provider events; no live model calls."""
from contextvars import copy_context
import json
import sqlite3

import pytest

from app.config import Settings
from app.db import create_database
from app.llm import ModelClient, StructuredOutputError
from app.models import Message
from app.model_config import ModelConfig
from app.token_usage import TokenUsage, collect_usage, record_usage


def provider_usage(input_tokens=100, output_tokens=40, cache_read=30, reasoning=10):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens, "cache_read_tokens": cache_read,
        "cache_write_tokens": 0, "reasoning_tokens": reasoning}


def model_client(tmp_path, monkeypatch, events):
    client = ModelClient(Settings(data_dir=tmp_path, llm_api_key="k", llm_base_url="https://example.com/v1", llm_model="m"))
    monkeypatch.setattr(client, "_events", lambda request, timeout: iter(events))
    return client


def test_provider_usage_is_aggregated_without_double_counting_cache_or_reasoning(tmp_path, monkeypatch):
    client = model_client(tmp_path, monkeypatch, [
        {"type": "usage", "usage": provider_usage()},
        {"type": "done", "text": "OK", "stop_reason": "stop"}])
    with collect_usage() as usage:
        client.complete([{"role": "system", "content": "[TASK:REVIEW_SPACE_PLAN]"}])
        client.complete_markdown([{"role": "system", "content": "[TASK:REVIEW_SPACE_EXPLAIN]"}])
    result = usage.snapshot()
    assert result["input_tokens"] == 200 and result["output_tokens"] == 80 and result["total_tokens"] == 280
    assert result["cache_read_tokens"] == 60 and result["reasoning_tokens"] == 20
    assert result["status"] == "complete" and result["model_calls"] == result["reported_calls"] == 2
    assert [s["stage"] for s in result["stages"]] == ["planning", "answer"]


def test_stream_usage_is_recorded_after_deltas_before_a_completed_answer(tmp_path, monkeypatch):
    client = model_client(tmp_path, monkeypatch, [
        {"type": "delta", "text": "answer"}, {"type": "usage", "usage": provider_usage()},
        {"type": "done", "text": "answer", "stop_reason": "stop"}])
    with collect_usage() as usage:
        assert list(client.stream([{"role": "user", "content": "question"}])) == ["answer"]
    assert usage.snapshot()["total_tokens"] == 140 and usage.snapshot()["model_calls"] == 1


def test_missing_usage_is_unknown_and_mixed_usage_reports_only_known_counts(tmp_path, monkeypatch):
    client = model_client(tmp_path, monkeypatch, [{"type": "done", "text": "OK", "stop_reason": "stop"}])
    with collect_usage() as usage:
        client.complete([{"role": "user", "content": "question"}])
        assert usage.snapshot()["status"] == "unavailable"
        assert usage.snapshot()["total_tokens"] is None
        record_usage(provider_usage(), "retrieval")
    result = usage.snapshot()
    assert result["status"] == "partial" and result["model_calls"] == 2 and result["unreported_calls"] == 1
    assert result["total_tokens"] == 140


def test_each_runtime_retry_is_counted_once(tmp_path, monkeypatch):
    client = model_client(tmp_path, monkeypatch, [
        {"type": "usage", "usage": None}, {"type": "usage", "usage": provider_usage()},
        {"type": "done", "text": "OK", "stop_reason": "stop"}])
    with collect_usage() as usage:
        client.complete([{"role": "system", "content": "[TASK:KNOWLEDGE_RERANK]"}])
    assert usage.snapshot()["model_calls"] == 2 and usage.snapshot()["reported_calls"] == 1
    assert usage.snapshot()["stages"][0]["stage"] == "retrieval"


def test_paid_usage_is_retained_when_provider_output_is_truncated(tmp_path, monkeypatch):
    client = model_client(tmp_path, monkeypatch, [
        {"type": "usage", "usage": provider_usage()},
        {"type": "done", "text": "partial answer", "stop_reason": "length"}])
    with collect_usage() as usage:
        with pytest.raises(StructuredOutputError):
            client.complete([{"role": "user", "content": "question"}])
    assert usage.snapshot()["status"] == "complete" and usage.snapshot()["total_tokens"] == 140


def test_reply_scopes_are_isolated_and_do_not_leak_to_background_work():
    first, second = TokenUsage(), TokenUsage()
    with collect_usage(first):
        record_usage(provider_usage(), "planning")
        with collect_usage(second):
            record_usage(provider_usage(50, 20, 0, 0), "answer")
        record_usage(provider_usage(), "retrieval")
    record_usage(provider_usage(), "other")
    assert first.snapshot()["total_tokens"] == 280 and first.snapshot()["model_calls"] == 2
    assert second.snapshot()["total_tokens"] == 70 and second.snapshot()["model_calls"] == 1


def test_no_model_calls_can_show_zero_without_claiming_missing_usage_is_zero():
    result = TokenUsage().snapshot()
    assert result["status"] == "none" and result["total_tokens"] == 0 and result["stages"] == []


@pytest.mark.parametrize("field,value", [("input_tokens", -1), ("total_tokens", 999),
                                        ("output_tokens", True), ("cache_read_tokens", 101),
                                        ("reasoning_tokens", 41)])
def test_invalid_usage_is_not_presented_as_confirmed_zero_or_a_total(field, value):
    usage = TokenUsage()
    usage.record({**provider_usage(), field: value}, "answer")
    assert usage.snapshot()["status"] == "unavailable" and usage.snapshot()["total_tokens"] is None


def test_existing_chat_history_gets_nullable_usage_column_without_losing_replies(tmp_path):
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("CREATE TABLE messages (id VARCHAR PRIMARY KEY, session_id VARCHAR NOT NULL, role VARCHAR NOT NULL, "
                   "content TEXT NOT NULL, citations JSON NOT NULL, created_at VARCHAR NOT NULL)")
        db.execute("INSERT INTO messages VALUES ('old', 'session', 'assistant', 'old answer', '[]', '2026-10-08')")
    engine, sessions = create_database(tmp_path)
    try:
        with sessions() as db:
            old = db.get(Message, "old")
            assert old.content == "old answer" and old.citations == [] and old.token_usage is None
            with collect_usage() as usage:
                record_usage(provider_usage(), "answer")
            old.token_usage = usage.snapshot()
            db.commit()
        with sessions() as db:
            assert db.get(Message, "old").token_usage["total_tokens"] == 140
    finally:
        engine.dispose()


def test_chat_stream_scope_survives_worker_context_changes_without_usage_leakage():
    from app.agent import ReviewAgentService
    from types import SimpleNamespace
    class ControlledChat(ReviewAgentService):
        def _chat(self, session_id, question, practice_context, note_context, usage):
            record_usage(provider_usage(), "planning")
            yield "status"
            record_usage(provider_usage(), "answer")
            yield usage.snapshot()
    reply = ControlledChat(SimpleNamespace()).chat("s", "q")
    assert copy_context().run(next, reply) == "status"
    result = copy_context().run(next, reply)
    assert result["total_tokens"] == 280 and result["model_calls"] == 2
    with pytest.raises(StopIteration):
        copy_context().run(next, reply)
    separate = TokenUsage()
    with collect_usage(separate):
        record_usage(provider_usage(), "answer")
    assert separate.snapshot()["model_calls"] == 1


def test_chat_sse_usage_matches_reloaded_message_and_keeps_evidence_separate(client, course, app, monkeypatch):
    from test_app import chat, event_payload, session, upload
    upload(client, course["id"])
    services = app.state.services
    monkeypatch.setattr(services.model_config, "value", ModelConfig(
        base_url="https://example.com/v1", api_key="k", model="m"))
    model = ModelClient(services.settings, services.model_config)
    def events(request, timeout):
        payload = json.loads(request["messages"][-1]["content"])
        if request["action"] == "stream":
            yield {"type": "delta", "text": "RAG uses source evidence."}
            text = "RAG uses source evidence."
        elif "[TASK:KNOWLEDGE_RERANK]" in request["messages"][0]["content"]:
            text = json.dumps({"rankings": [{"id": c["id"], "score": 80, "topics": payload["requested_topics"]}
                for c in payload["candidates"]], "coverage": []})
        else:
            text = json.dumps({"tools": [], "done": True})
        yield {"type": "usage", "usage": provider_usage()}
        yield {"type": "done", "text": text, "stop_reason": "stop"}
    monkeypatch.setattr(model, "_events", events)
    monkeypatch.setattr(services, "llm", model)
    monkeypatch.setattr(services.retrieval.reranker, "llm", model)
    session_id = session(client, course["id"])
    done = event_payload(chat(client, session_id, "RAG"), "done")
    saved = client.get(f"/sessions/{session_id}/messages").json()[-1]
    assert done["token_usage"] == saved["token_usage"]
    assert done["token_usage"]["total_tokens"] >= 280 and done["token_usage"]["status"] == "complete"
    with services.sessions() as db:
        message = db.get(Message, done["message_id"])
        assert message.token_usage == done["token_usage"]
        assert all("token_usage" not in source for source in message.citations)


def test_review_reply_persists_usage_for_planning_retrieval_and_explanation(course, app, monkeypatch):
    from threading import Lock
    from types import SimpleNamespace
    from app.models import Course, ReviewSpace
    from app.review_space import ReviewSpaceService
    from test_retrieval_token_savings import REQUEST, complete_report, material

    services = app.state.services
    monkeypatch.setattr(services.model_config, "value", ModelConfig(
        base_url="https://example.com/v1", api_key="k", model="m"))
    model = ModelClient(services.settings, services.model_config)
    def events(request, timeout):
        rules = request["messages"][0]["content"]
        if "[TASK:REVIEW_SPACE_PLAN]" in rules:
            text = json.dumps({"action": "search_materials", "topic": "堆排序", "knowledge_source": "materials"})
        elif "[TASK:KNOWLEDGE_RERANK]" in rules:
            text = "{}"
        else:
            text = "堆排序的最坏时间复杂度为 O(n log n)。"
        yield {"type": "usage", "usage": provider_usage()}
        yield {"type": "done", "text": text, "stop_reason": "stop"}
    monkeypatch.setattr(model, "_events", events)
    monkeypatch.setattr(services, "llm", model)
    review = ReviewSpaceService(SimpleNamespace(s=services))
    def course_sources(value, *args, **kwargs):
        model.complete([{"role": "system", "content": "[TASK:KNOWLEDGE_RERANK]"}])
        report = complete_report()
        report["course_version"] = value.state["course_version"]
        return ([material()], report)
    monkeypatch.setattr(review, "_course_sources", course_sources)
    with services.sessions() as db:
        version = db.get(Course, course["id"]).version
        db.add(ReviewSpace(id="usage-review", course_id=course["id"], goal=REQUEST, status="processing", revision=0,
            state={"course_version": version, "run_token": "usage-token", "chapter_ids": [], "chapters": [],
                   "attempts": [], "trace": [], "messages": [{"id": "u", "role": "user", "content": REQUEST}]}))
        db.commit()
    lock = Lock()
    lock.acquire()
    review.run("usage-review", "usage-token", lock)
    with services.sessions() as db:
        value = db.get(ReviewSpace, "usage-review")
        assert value.status == "reading"
        saved = value.state["messages"][-1]["token_usage"]
        assert saved["total_tokens"] == 420 and saved["model_calls"] == 3 and saved["status"] == "complete"
        assert {s["stage"] for s in saved["stages"]} == {"planning", "retrieval", "answer"}
        assert value.state["step"]["token_usage"] == saved
    assert review.get("usage-review")["messages"][-1]["token_usage"] == saved
