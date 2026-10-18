"""Application integration tests with real parsers/storage and controlled models.

These checks validate behavior and lifecycle. They do not establish real-model
answer quality or the acceptance target on a real course question set.
"""
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient
import pytest
from app.models import Chapter, Message

from conftest import make_docx, make_pdf, make_pptx


def items(value, key):
    return value if isinstance(value, list) else value[key]


def upload(client, course_id, filename="lecture.docx", content=None, kind="lecture", year=None):
    data = {"kind": kind}
    if year is not None:
        data["year"] = str(year)
    response = client.post(
        f"/courses/{course_id}/documents", data=data,
        files={"file": (filename, make_docx() if content is None else content, "application/octet-stream")},
    )
    assert response.status_code in (200, 201, 202), response.text
    document = response.json()
    if "document" in document:
        document = document["document"]
    documents = items(client.get(f"/courses/{course_id}/documents").json(), "documents")
    return next(item for item in documents if item["id"] == document["id"])


def blocks(client, document_id):
    response = client.get(f"/documents/{document_id}/blocks")
    assert response.status_code == 200, response.text
    return items(response.json(), "blocks")


def confirm_chapters(client, course_id, extra=None):
    chapters = items(client.get(f"/courses/{course_id}/chapters").json(), "chapters")
    documents = items(client.get(f"/courses/{course_id}/documents").json(), "documents")
    all_blocks = [block for document in documents for block in blocks(client, document["id"])]
    payload = []
    for chapter in chapters:
        payload.append({
            "id": chapter["id"], "title": chapter["title"], "position": chapter["position"],
            "block_ids": chapter.get("block_ids", [block["id"] for block in all_blocks if block.get("chapter_id") == chapter["id"]]),
        })
    if extra:
        payload.append(extra)
    response = client.patch(f"/courses/{course_id}/chapters", json={"chapters": payload, "confirm": True})
    assert response.status_code == 200, response.text
    return items(response.json(), "chapters")


def start_outline(client, course_id):
    response = client.post(f"/courses/{course_id}/outlines")
    assert response.status_code in (200, 201, 202), response.text
    value = response.json()
    job_id = value.get("job_id", value.get("id"))
    job = client.get(f"/jobs/{job_id}").json()
    outline_id = value.get("outline_id", job.get("outline_id"))
    assert outline_id, value
    return job, client.get(f"/outlines/{outline_id}").json()


def session(client, course_id):
    response = client.post(f"/courses/{course_id}/sessions", json={"title": "集成验收"})
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def chat(client, session_id, question):
    response = client.post(f"/sessions/{session_id}/chat", json={"question": question})
    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers.get("content-type", "")
    events, event, data = [], "message", []
    for line in response.text.splitlines() + [""]:
        if not line:
            if data:
                events.append((event, json.loads("\n".join(data))))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    return events


def event_payload(events, name):
    return next(value for event, value in events if event == name)


def stored_evidence(app, message_id):
    with app.state.services.sessions() as db:
        return db.get(Message, message_id).citations


@pytest.mark.parametrize("filename,content,location_key", [
    ("reading.pdf", make_pdf(), "page"),
    ("slides.pptx", make_pptx(), "slide"),
    ("lecture.docx", make_docx(), "paragraph"),
], ids=["pdf", "pptx", "docx"])
def test_parse_real_formats_with_source_locations(client, course, filename, content, location_key):
    document = upload(client, course["id"], filename, content)
    assert document["status"] == "ready", document
    parsed = blocks(client, document["id"])
    assert parsed
    assert all(block["document_id"] == document["id"] for block in parsed)
    assert any(location_key in block["locator"] for block in parsed), parsed
    combined = "\n".join(block["text"] for block in parsed)
    assert "RAG" in combined
    if filename.endswith(".pptx"):
        assert "Grouped text" in combined
        assert "Original source" in combined
        assert any(block["locator"].get("slide") == 2 for block in parsed)
    if filename.endswith(".docx"):
        assert "Find relevant evidence" in combined
        assert any("table" in block["locator"] for block in parsed)
        assert all("page" not in block["locator"] for block in parsed)
    if filename.endswith(".pdf"):
        assert any(block["locator"].get("page") == 2 for block in parsed)


def test_upload_duplicate_reuses_document_and_preserves_version(client, course):
    content = make_docx()
    first = upload(client, course["id"], content=content)
    version = next(row["version"] for row in items(client.get("/courses").json(), "courses") if row["id"] == course["id"])
    second = upload(client, course["id"], filename="same-content-renamed.docx", content=content)
    assert second["id"] == first["id"]
    assert len(items(client.get(f"/courses/{course['id']}/documents").json(), "documents")) == 1
    assert next(row["version"] for row in items(client.get("/courses").json(), "courses") if row["id"] == course["id"]) == version


def test_invalid_and_empty_files_do_not_become_ready(client, course):
    broken = upload(client, course["id"], "broken.pdf", b"not a PDF")
    assert broken["status"] == "failed"
    assert broken.get("error")
    assert blocks(client, broken["id"]) == []
    empty = upload(client, course["id"], "scanned.pdf", make_pdf([""]))
    assert empty["status"] in ("failed", "needs_ocr")
    assert empty.get("error") or empty.get("warnings")
    unsupported = client.post(f"/courses/{course['id']}/documents", data={"kind": "lecture"}, files={"file": ("archive.zip", b"archive")})
    assert unsupported.status_code == 415
    assert "支持" in unsupported.text


def test_outline_requires_confirmed_chapters(client, course):
    upload(client, course["id"])
    response = client.post(f"/courses/{course['id']}/outlines")
    assert response.status_code == 409, response.text


def test_outline_traverses_all_blocks_and_marks_missing_chapter(client, course, llm, app):
    long_text = " ".join(f"unique_tail_{index} RAG evidence." for index in range(200))
    document = upload(client, course["id"], content=make_docx([long_text, "END_OF_COURSE_MATERIAL_9127"], include_table=False))
    confirm_chapters(client, course["id"])
    # The API rejects new empty chapters; a saved chapter whose file later failed has no material.
    with app.state.services.sessions() as db:
        db.add(Chapter(course_id=course["id"], title="第二章 尚缺资料", position=99, confirmed=True))
        db.commit()
    job, outline = start_outline(client, course["id"])
    assert job["status"] == "completed", job
    assert "END_OF_COURSE_MATERIAL_9127" in outline["markdown"]
    missing = next(chapter for chapter in outline["coverage"]["chapters"] if chapter["title"] == "第二章 尚缺资料")
    assert missing["missing_material"] is True and missing["processed"] is False
    sent = "\n".join(value["text"] for messages in llm.markdown_calls if "[TASK:REVIEW_NOTES_" in messages[0]["content"]
                     for value in json.loads(messages[-1]["content"]).get("materials", []))
    expected = [block["text"] for block in blocks(client, document["id"]) if block["text"].strip()]
    assert expected and all(text in sent for text in expected)
    exported = client.get(f"/outlines/{outline['id']}/export")
    assert exported.status_code == 200
    assert exported.text == outline["markdown"]


def test_new_upload_invalidates_outline_and_confirmation(client, course):
    upload(client, course["id"])
    confirm_chapters(client, course["id"])
    _, outline = start_outline(client, course["id"])
    upload(client, course["id"], "additional.docx", make_docx(["Additional RAG material."], include_table=False))
    stored = client.get(f"/outlines/{outline['id']}").json()
    assert stored["stale"] is True
    assert stored["markdown"] == outline["markdown"]
    assert client.post(f"/courses/{course['id']}/outlines").status_code == 409


def test_chat_hides_public_citations_but_preserves_internal_evidence(client, course, llm, app):
    document = upload(client, course["id"])
    session_id = session(client, course["id"])
    events = chat(client, session_id, "RAG")
    names = [event for event, _ in events]
    assert {"status", "delta", "done"}.issubset(names)
    assert "sources" not in names
    assert "error" not in names
    final = event_payload(events, "done")
    returned = stored_evidence(app, final["message_id"])
    assert returned and all(row["document_id"] == document["id"] for row in returned)
    assert final["citations"] == []
    for citation in returned:
        source = client.get(f"/sources/{citation['source_id']}")
        assert source.status_code == 200, source.text
        assert source.json()["document_id"] == document["id"]
    followup = chat(client, session_id, "它有什么用？")
    assert "done" in [name for name, _ in followup]
    plan_calls = [messages for messages in llm.complete_calls if "[TASK:PLAN]" in messages[0]["content"]]
    payload = json.loads(plan_calls[-1][-1]["content"])
    assert any("RAG" in message["content"] for message in payload["history"])
    saved = items(client.get(f"/sessions/{session_id}/messages").json(), "messages")
    assert [message["role"] for message in saved] == ["user", "assistant", "user", "assistant"]
    assert all(message["citations"] == [] for message in saved)
    assert stored_evidence(app, saved[1]["id"]) == returned


def test_answer_without_evidence_reports_insufficiency(client, course, llm):
    session_id = session(client, course["id"])
    events = chat(client, session_id, "Unrelated alien spacecraft details")
    assert "sources" not in [name for name, _ in events]
    done = event_payload(events, "done")
    answer = done.get("answer", done.get("content", ""))
    assert "不足" in answer or "没有" in answer or "未找到" in answer
    assert not done["citations"]


def test_generated_reference_markers_cannot_become_internal_evidence(client, course, llm, app):
    upload(client, course["id"])
    session_id = session(client, course["id"])
    fabricated = "S" + "0" * 32
    llm.stream_responses.append(f"编造引用 [{fabricated}]")
    events = chat(client, session_id, "RAG")
    assert "error" not in [name for name, _ in events]
    done = event_payload(events, "done")
    assert fabricated not in done["content"]
    evidence = stored_evidence(app, done["message_id"])
    assert evidence
    assert all(item["source_id"] != fabricated for item in evidence)
    assert all(item["course_id"] == course["id"] for item in evidence)
    saved = items(client.get(f"/sessions/{session_id}/messages").json(), "messages")
    assert all(fabricated not in message["content"] for message in saved if message["role"] == "assistant")


def test_cross_course_filter_and_document_delete(client, course, llm, app):
    own = upload(client, course["id"])
    other = client.post("/courses", json={"name": "另一门课程"}).json()
    alien = upload(client, other["id"], "alien.docx", make_docx(["RAG OTHER_COURSE_SECRET"], include_table=False))
    llm.complete_responses.append({"query": "RAG", "tools": [{"name": "search_materials", "query": "RAG", "course_id": other["id"]}]})
    events = chat(client, session(client, course["id"]), "RAG")
    sources = stored_evidence(app, event_payload(events, "done")["message_id"])
    assert sources
    assert all(source["document_id"] != alien["id"] for source in sources)
    source_id = sources[0]["source_id"]
    deleted = client.delete(f"/documents/{own['id']}")
    assert deleted.status_code in (200, 204)
    search = client.get(f"/courses/{course['id']}/search", params={"query": "RAG", "kind": "material", "limit": 5})
    assert search.status_code == 200
    assert not items(search.json(), "sources")
    assert client.get(f"/sources/{source_id}").status_code == 404


def test_past_question_preserves_neighboring_page_and_missing_answer(client, course, llm, app):
    exam = upload(client, course["id"], "past2024.pdf", make_pdf(["RAG question: explain retrieval", "CONTINUED_QUESTION_CONTEXT: and how generation uses evidence"]), kind="exam", year=2024)
    session_id = session(client, course["id"])
    events = chat(client, session_id, "RAG 真题")
    sources = stored_evidence(app, event_payload(events, "done")["message_id"])
    assert any(source["document_id"] == exam["id"] and source.get("year") == 2024 for source in sources)
    # Question figures turn the user message into text and image content blocks.
    answer_prompt = "\n".join(message["content"] if isinstance(message["content"], str) else
                              "\n".join(part.get("text", "") for part in message["content"])
                              for message in llm.stream_calls[-1])
    assert "CONTINUED_QUESTION_CONTEXT" in answer_prompt
    done = event_payload(events, "done")
    answer = done.get("answer", done.get("content", ""))
    assert "尚未核验到对应的原文答案" in answer


def test_model_failure_ends_stream_and_task_can_retry(client, course, llm):
    upload(client, course["id"])
    session_id = session(client, course["id"])
    llm.fail_stream = True
    events = chat(client, session_id, "RAG")
    assert "error" in [name for name, _ in events]
    llm.fail_stream = False
    confirm_chapters(client, course["id"])
    llm.fail_complete = True
    job, outline = start_outline(client, course["id"])
    assert job["status"] == "failed"
    assert job.get("error")
    llm.fail_complete = False
    retried = client.post(f"/jobs/{job['id']}/retry")
    assert retried.status_code in (200, 202), retried.text
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "completed"
    assert client.get(f"/outlines/{outline['id']}").json()["markdown"]


def test_restart_preserves_index_history_and_interrupts_running_task(client, course, settings, embedder, llm):
    from app.main import create_app
    upload(client, course["id"])
    session_id = session(client, course["id"])
    chat(client, session_id, "RAG")
    confirm_chapters(client, course["id"])
    job, outline = start_outline(client, course["id"])
    with sqlite3.connect(settings.data_dir / "review.db") as connection:
        connection.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
        connection.execute("UPDATE outlines SET status='running' WHERE id=?", (outline["id"],))
    restarted_app = create_app(settings=settings, embedder=embedder, llm=llm)
    with TestClient(restarted_app) as restarted:
        history = items(restarted.get(f"/sessions/{session_id}/messages").json(), "messages")
        assert len(history) == 2
        search = restarted.get(f"/courses/{course['id']}/search", params={"query": "RAG", "kind": "material", "limit": 5})
        assert items(search.json(), "sources")
        assert restarted.get(f"/jobs/{job['id']}").json()["status"] == "interrupted"


def test_chapter_mapping_rejects_foreign_course_blocks_without_mutation(client, course):
    own = upload(client, course["id"])
    before = blocks(client, own["id"])
    other = client.post("/courses", json={"name": "第三门课程"}).json()
    alien = upload(client, other["id"], "other.docx", make_docx(["PRIVATE_OTHER_CHAPTER"], include_table=False))
    own_chapters = items(client.get(f"/courses/{course['id']}/chapters").json(), "chapters")
    response = client.patch(f"/courses/{course['id']}/chapters", json={"chapters": [{
        "id": own_chapters[0]["id"], "title": "错误归属", "position": 0,
        "block_ids": [block["id"] for block in before] + [blocks(client, alien["id"])[0]["id"]],
    }], "confirm": True})
    assert response.status_code in (400, 422), response.text
    assert blocks(client, own["id"]) == before


def test_history_context_is_limited_to_six_rounds(client, course, llm):
    upload(client, course["id"])
    session_id = session(client, course["id"])
    for number in range(8):
        events = chat(client, session_id, f"RAG 追问 {number}")
        assert "done" in [name for name, _ in events]
    planner = [messages for messages in llm.complete_calls if "[TASK:PLAN]" in messages[0]["content"]]
    history = json.loads(planner[-1][-1]["content"])["history"]
    assert len(history) == 12
    assert not any(message["content"] == "RAG 追问 0" for message in history)
    saved = items(client.get(f"/sessions/{session_id}/messages").json(), "messages")
    assert len(saved) == 16


def test_agent_limits_model_requested_tool_calls(client, course, llm, embedder):
    upload(client, course["id"])
    llm.complete_responses.append({"query": "RAG", "tools": [
        {"name": "search_materials", "query": f"RAG request {number}"} for number in range(10)
    ]})
    events = chat(client, session(client, course["id"]), "RAG")
    assert "done" in [name for name, _ in events]
    assert 1 <= len(embedder.query_calls) <= 3


def test_unconfigured_real_llm_returns_503_without_fabricated_answers(settings, embedder):
    from app.main import create_app
    unconfigured = create_app(settings=settings, embedder=embedder)
    with TestClient(unconfigured) as client:
        health = client.get("/health").json()
        assert health["llm_configured"] is False
        course = client.post("/courses", json={"name": "尚未配置真实模型"}).json()
        upload(client, course["id"])
        session_id = session(client, course["id"])
        reply = client.post(f"/sessions/{session_id}/chat", json={"question": "RAG"})
        assert reply.status_code == 503
        assert not items(client.get(f"/sessions/{session_id}/messages").json(), "messages")
        confirm_chapters(client, course["id"])
        outline = client.post(f"/courses/{course['id']}/outlines")
        assert outline.status_code == 503
        assert not items(client.get(f"/courses/{course['id']}/outlines").json(), "outlines")
