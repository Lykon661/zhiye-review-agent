"""Regressions for evidence completeness and recoverable persistence failures.

Models are controlled here; SQLite, parsing, Chroma, HTTP and state transitions
remain real. These tests do not claim real-course retrieval or answer quality.
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import Chunk
from app.outlines import FIELDS, OutlineService, validate_result
from conftest import make_docx
from test_app import blocks, chat, confirm_chapters, session, start_outline, upload


@pytest.mark.parametrize("empty_result", [{}, {field: [] for field in FIELDS}], ids=["missing-fields", "empty-fields"])
def test_nonempty_material_cannot_silently_complete_an_empty_outline(
    client, course, llm, empty_result,
):
    upload(client, course["id"], content=make_docx(["RAG material must appear in the outline."], include_table=False))
    confirm_chapters(client, course["id"])
    llm.complete_responses.append(deepcopy(empty_result))

    job, outline = start_outline(client, course["id"])

    assert job["status"] == "failed", "An empty map response must not report the real chapter as processed."
    assert job["error"]
    assert outline["status"] == "failed"
    assert client.get(f"/outlines/{outline['id']}/export").status_code == 409

    retried = client.post(f"/jobs/{job['id']}/retry")
    assert retried.status_code in (200, 202), retried.text
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "completed"
    assert "RAG material must appear" in client.get(f"/outlines/{outline['id']}").json()["markdown"]


def test_empty_missing_chapter_result_is_still_legal_without_sources():
    result = {field: [] for field in FIELDS}
    assert validate_result(result, set()) == result


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


def test_snapshot_exam_associations_survive_live_deletion_and_new_upload(client, course, app, monkeypatch):
    lecture = upload(client, course["id"], "rag.docx", make_docx(["RAG retrieval source evidence generation."], include_table=False))
    exam = upload(client, course["id"], "past2024.docx", make_docx(["RAG retrieval question: explain source evidence."], include_table=False), kind="exam", year=2024)
    confirm_chapters(client, course["id"])
    service = OutlineService(app.state.services)
    with app.state.services.sessions() as db:
        snapshot = service.snapshot(db, course["id"])
    results = {
        chapter["id"]: {"core_points": [{"text": "RAG retrieval source evidence generation", "source_ids": []}]}
        for chapter in snapshot["chapters"]
    }

    def forbid_live_search(*args, **kwargs):
        raise AssertionError("Snapshot associations must not query the mutable live index.")

    monkeypatch.setattr(app.state.services.retrieval, "search", forbid_live_search)
    baseline = service._associate_exams(snapshot, results)
    associated = [source for values in baseline.values() for source in values]
    original_exam_blocks = {block["id"] for block in blocks(client, exam["id"])}
    assert associated
    assert {source["block_id"] for source in associated}.issubset(original_exam_blocks)

    assert client.delete(f"/documents/{exam['id']}").status_code == 204
    assert client.delete(f"/documents/{lecture['id']}").status_code == 204
    new_exam = upload(client, course["id"], "past2025.docx", make_docx(["RAG retrieval NEW_QUESTION_NOT_IN_SNAPSHOT"], include_table=False), kind="exam", year=2025)
    after_changes = service._associate_exams(snapshot, results)

    assert after_changes == baseline
    new_blocks = {block["id"] for block in blocks(client, new_exam["id"])}
    assert not any(source["block_id"] in new_blocks for values in after_changes.values() for source in values)
