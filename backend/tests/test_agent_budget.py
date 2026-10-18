"""Prompt limits and citations for evidence actually supplied to the model."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest
from sqlalchemy import select

from app.agent import INPUT_BYTE_BUDGET, answer_messages, plan_messages
from app.models import Chunk, Message
from app.prompt_budget import InputBudget
from conftest import make_docx
from test_app import blocks, chat, event_payload, session, upload


def message_bytes(messages):
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


def test_both_phases_budget_large_history_and_deduplicate_context():
    history = [{"role": "user" if index % 2 == 0 else "assistant", "content": "最近讨论的主题" * 300}
               for index in range(12)]
    sources = [{
        "source_id": f"Schunk{index}", "chunk_id": f"chunk{index}", "block_id": "shared",
        "document_id": "document", "course_id": "course", "filename": "lecture.docx",
        "kind": "note" if index == 14 else "lecture", "year": None,
        "chapter_id": "chapter", "locator": {"paragraph": index + 1},
        "text": "完整直接检索证据" * 10, "score": 1 - index / 100,
        "context": [{"block_id": "shared", "source_id": "Sshared", "locator": {"paragraph": 1},
                     "text": "长原文相邻内容" * 2000, "truncated": True, "original_characters": 20000}],
    } for index in range(15)]
    original = deepcopy(sources)
    question = "它与笔记中的说法有什么区别？"
    observations = [{"tool": "search_materials", "query": question,
                     "sources": [{"source_id": source["source_id"], "kind": source["kind"],
                                  "text": "长检索观察" * 1000} for source in sources[:5]]}
                    for _ in range(3)]

    planned = plan_messages(question, history, observations)
    answered, visible, allowed = answer_messages(question, history, sources)
    assert message_bytes(planned) <= INPUT_BYTE_BUDGET
    assert message_bytes(answered) <= INPUT_BYTE_BUDGET
    plan = json.loads(planned[-1]["content"])
    answer = json.loads(answered[-1]["content"])
    assert plan["question"] == answer["question"] == question
    assert plan["history"] and answer["history"]
    assert any(item["truncated"] for observation in plan["observations"] for item in observation["sources"])
    assert {source["kind"] for source in visible} == {"lecture", "note"}
    by_id = {source["source_id"]: source for source in original}
    assert all(source["text"] == by_id[source["source_id"]]["text"] for source in visible)
    contexts = [context for source in visible for context in source["context"]]
    assert len(contexts) == 1
    assert contexts[0]["truncated"] is True
    assert len(contexts[0]["text"]) < len(original[0]["context"][0]["text"])
    assert set(allowed) == {source["source_id"] for source in visible} | {context["source_id"] for context in contexts}
    assert sources == original, "Packing must not change retrieval or persisted originals."


def test_complete_question_is_never_silently_truncated():
    question = "问题" * INPUT_BYTE_BUDGET
    with pytest.raises(ValueError, match="问题或当前题目过长"):
        plan_messages(question, [], [])
    with pytest.raises(ValueError, match="问题或当前题目过长"):
        answer_messages(question, [], [])


def long_source(client, course, app):
    document = upload(client, course["id"], content=make_docx([
        "RAG CURRENT_BLOCK " * 1000,
        "RAG OMITTED_NEIGHBOR " * 1000,
    ], include_table=False))
    parsed = blocks(client, document["id"])
    current = next(block for block in parsed if "CURRENT_BLOCK" in block["text"])
    neighbor = next(block for block in parsed if "OMITTED_NEIGHBOR" in block["text"])
    with app.state.services.sessions() as db:
        chunk = db.scalar(select(Chunk).where(Chunk.block_id == current["id"]).order_by(Chunk.position))
        source = app.state.services.retrieval.source(db, chunk.id, context=True)
    return source, current, neighbor


def test_truncated_neighbor_citation_preserves_model_visible_copy(client, course, app, llm, monkeypatch):
    app.state.services.model_config.value = replace(app.state.services.model_config.snapshot(), context_window=12288)
    source, current, _ = long_source(client, course, app)
    monkeypatch.setattr(app.state.services.retrieval, "search", lambda *args, **kwargs: [source])
    llm.stream_responses.append(f"根据所提供的原文片段解释。[S{current['id']}]")
    events = chat(client, session(client, course["id"]), "RAG")

    done = event_payload(events, "done")
    assert done["citations"] == []
    with app.state.services.sessions() as db:
        persisted = db.get(Message, done["message_id"]).citations
    citation = next(item for item in persisted if item["source_id"] == "S" + current["id"])
    payload = json.loads(llm.stream_calls[-1][-1]["content"])
    supplied = next(context for item in payload["sources"] for context in item["context"]
                    if context["block_id"] == current["id"])
    budget = InputBudget(app.state.services.model_config.snapshot())
    assert budget.messages_size(llm.stream_calls[-1]) <= budget.limit
    assert "sources" not in [name for name, _ in events]
    assert citation["text"] == supplied["text"]
    assert citation["truncated"] is True
    assert citation["original_characters"] == len(current["text"])
    assert citation["locator"] == current["locator"]
    assert len(citation["text"]) < len(current["text"])


def test_budget_omitted_neighbor_cannot_become_persisted_evidence(client, course, app, llm, monkeypatch):
    app.state.services.model_config.value = replace(app.state.services.model_config.snapshot(), context_window=12288)
    source, _, neighbor = long_source(client, course, app)
    monkeypatch.setattr(app.state.services.retrieval, "search", lambda *args, **kwargs: [source])
    omitted_id = "S" + neighbor["id"]
    llm.stream_responses.append(f"不应通过的未提供引用。[{omitted_id}]")
    session_id = session(client, course["id"])
    events = chat(client, session_id, "RAG")

    payload = json.loads(llm.stream_calls[-1][-1]["content"])
    visible_ids = {item["source_id"] for item in payload["sources"]}
    visible_ids.update(context["source_id"] for item in payload["sources"] for context in item["context"])
    assert omitted_id not in visible_ids
    done = event_payload(events, "done")
    assert omitted_id not in done["content"]
    assert done["citations"] == []
    with app.state.services.sessions() as db:
        persisted = db.get(Message, done["message_id"]).citations
    assert all(item["source_id"] != omitted_id for item in persisted)
    saved = client.get(f"/sessions/{session_id}/messages").json()
    assert any(message["role"] == "assistant" for message in saved)
    assert all(message["citations"] == [] for message in saved)
