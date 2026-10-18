"""Regressions for evidence completeness and recoverable persistence failures.

Models are controlled here; SQLite, parsing, Chroma, HTTP and state transitions
remain real. These tests do not claim real-course retrieval or answer quality.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.llm import StructuredOutputError
from app.models import Chunk
from app.outlines import NO_CONTENT, clean_markdown
from conftest import make_docx
from test_app import blocks, chat, confirm_chapters, session, start_outline, upload


@pytest.mark.parametrize("reply,reason", [
    ('{"core_points": []}', "JSON"),
    ("### 只有标题\n\n#### 也没有正文", "仅返回标题"),
], ids=["json", "heading-only"])
def test_nonempty_material_cannot_silently_complete_empty_notes(client, course, llm, reply, reason):
    upload(client, course["id"], content=make_docx(["RAG material must appear in the notes."], include_table=False))
    confirm_chapters(client, course["id"])
    # One unusable reply is retried once; both attempts are rejected.
    llm.markdown_responses.extend([reply, reply])

    job, outline = start_outline(client, course["id"])

    assert job["status"] == "failed", "Unusable Markdown must not report the real chapter as processed."
    assert reason in job["error"]
    assert len(llm.markdown_calls) == 2
    assert outline["status"] == "failed"
    assert client.get(f"/outlines/{outline['id']}/export").status_code == 409

    retried = client.post(f"/jobs/{job['id']}/retry")
    assert retried.status_code in (200, 202), retried.text
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "completed"
    assert "RAG material must appear" in client.get(f"/outlines/{outline['id']}").json()["markdown"]


def test_knowledge_material_declared_empty_fails_instead_of_completing(client, course, llm):
    upload(client, course["id"], content=make_docx(["RAG material must appear in the notes."], include_table=False))
    confirm_chapters(client, course["id"])
    llm.markdown_responses.append(NO_CONTENT)

    job, outline = start_outline(client, course["id"])

    assert job["status"] == "failed"
    assert "没有可整理的知识正文" in job["error"]
    assert client.get(f"/outlines/{outline['id']}/export").status_code == 409


@pytest.mark.parametrize("text", [
    '{"core_points": [{"text": "RAG"}]}',
    '[{"text": "RAG"}]',
    '```markdown\n{"core_points": []}\n```',
    "### 只有标题\n\n#### 子标题",
    "<think>先列提纲</think>\n## 第一章 RAG\n### 检索增强生成（RAG）",
    "### 资料来源\n- lecture.docx",
], ids=["json-object", "json-array", "fenced-json", "headings", "chapter-title-only", "forbidden-section-only"])
def test_clean_markdown_rejects_json_and_heading_only_output(text):
    with pytest.raises(StructuredOutputError):
        clean_markdown(text, "第一章 RAG")


def test_clean_markdown_keeps_topic_body_and_drops_wrappers():
    raw = "```markdown\n## 第一章 RAG\n## 检索增强生成（RAG）\nRAG 先检索再生成。[S" + "a" * 32 + "]\n```"
    assert clean_markdown(raw, "第一章 RAG") == "### 检索增强生成（RAG）\nRAG 先检索再生成。"
    assert clean_markdown(NO_CONTENT, "第一章 RAG") == ""


def test_long_question_current_block_contains_its_final_condition(client, course, app, llm):
    question = "front_anchor " * 40 + "ordinary context " * 100 + "CRITICAL_FINAL_CONDITION: assume cache capacity equals 4."
    exam = upload(client, course["id"], "long-exam.docx", make_docx([question], include_table=False), kind="exam", year=2024)
    question_block = next(block for block in blocks(client, exam["id"]) if "CRITICAL_FINAL_CONDITION" in block["text"])
    services = app.state.services
    with services.sessions() as db:
        first_chunk = db.scalar(select(Chunk).where(Chunk.block_id == question_block["id"]).order_by(Chunk.position))
        assert "CRITICAL_FINAL_CONDITION" not in first_chunk.text
        source = services.retrieval.source(db, first_chunk.id, context=True)

    current = next(item for item in source["context"] if item["block_id"] == question_block["id"])
    assert current["text"] == question_block["text"]
    assert current["source_id"] == "S" + question_block["id"]
    assert current["locator"] == question_block["locator"]

    events = chat(client, session(client, course["id"]), "front_anchor 真题")
    assert "done" in [name for name, _ in events]
    answer_payload = llm.stream_calls[-1][-1]["content"]
    assert "CRITICAL_FINAL_CONDITION" in answer_payload


def test_oversized_current_block_declares_context_truncation(client, course, app):
    question = "front_anchor " * 700 + "TAIL_BEYOND_CONTEXT_LIMIT"
    exam = upload(client, course["id"], "oversized-exam.docx", make_docx([question], include_table=False), kind="exam")
    question_block = next(block for block in blocks(client, exam["id"]) if "TAIL_BEYOND_CONTEXT_LIMIT" in block["text"])
    services = app.state.services
    with services.sessions() as db:
        first_chunk = db.scalar(select(Chunk).where(Chunk.block_id == question_block["id"]).order_by(Chunk.position))
        source = services.retrieval.source(db, first_chunk.id, context=True)

    current = next(item for item in source["context"] if item["block_id"] == question_block["id"])
    assert 0 < len(current["text"]) <= 8000
    assert current["truncated"] is True
    assert current["locator"] == question_block["locator"]


def test_index_and_cleanup_failure_still_produces_retryable_document(client, course, app, monkeypatch):
    retrieval = app.state.services.retrieval

    def fail(*args, **kwargs):
        raise RuntimeError("Simulated persistent Chroma outage")

    with monkeypatch.context() as patch:
        patch.setattr(retrieval, "index_document", fail)
        patch.setattr(retrieval, "delete_document", fail)
        failed = upload(client, course["id"], "outage.docx")

    assert failed["status"] == "failed", "Cleanup failure must not leave the document stuck in processing."
    assert failed["error"]
    retry = client.post(f"/documents/{failed['id']}/retry")
    assert retry.status_code in (200, 202), retry.text
    ready = next(document for document in client.get(f"/courses/{course['id']}/documents").json() if document["id"] == failed["id"])
    assert ready["status"] == "ready", ready
    search = client.get(f"/courses/{course['id']}/search", params={"query": "RAG"})
    assert search.status_code == 200, search.text
    assert any(source["document_id"] == failed["id"] for source in search.json()["sources"])


def test_partial_chapter_index_failure_blocks_search_until_repair(client, course, app, monkeypatch):
    first = upload(client, course["id"], "first.docx", make_docx(["RAG first document source."], include_table=False))
    second = upload(client, course["id"], "second.docx", make_docx(["RAG second document source."], include_table=False))
    all_block_ids = [block["id"] for document in (first, second) for block in blocks(client, document["id"])]
    retrieval = app.state.services.retrieval
    original_update = retrieval.update_chapters
    updated = 0

    def fail_second_update(*args, **kwargs):
        nonlocal updated
        updated += 1
        if updated == 2:
            raise RuntimeError("Second document metadata write interrupted")
        return original_update(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(retrieval, "update_chapters", fail_second_update)
        edited = client.patch(f"/courses/{course['id']}/chapters", json={
            "chapters": [{"title": "合并后的 RAG 章节", "position": 0, "block_ids": all_block_ids}],
            "confirm": True,
        })

    assert edited.status_code in (200, 503), edited.text
    chapters = client.get(f"/courses/{course['id']}/chapters").json()
    assert len(chapters) == 1
    current = chapters[0]
    assert current["title"] == "合并后的 RAG 章节"
    assert set(current["block_ids"]) == set(all_block_ids)
    assert all(block["chapter_id"] == current["id"] for document in (first, second) for block in blocks(client, document["id"]))
    blocked = client.get(f"/courses/{course['id']}/search", params={"query": "RAG", "chapter_id": current["id"]})
    assert blocked.status_code == 503, blocked.text

    repaired = client.post(f"/courses/{course['id']}/index/repair")
    assert repaired.status_code in (200, 202), repaired.text
    search = client.get(f"/courses/{course['id']}/search", params={"query": "RAG", "chapter_id": current["id"], "limit": 20})
    assert search.status_code == 200, search.text
    sources = search.json()["sources"]
    assert sources
    assert {source["document_id"] for source in sources} == {first["id"], second["id"]}
    assert all(source["chapter_id"] == current["id"] for source in sources)
