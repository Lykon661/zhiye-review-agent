"""Learning state contracts with controlled model output; no quality claims."""
from copy import deepcopy
import json
from threading import Lock

import pytest
from fastapi import BackgroundTasks

from app.models import Course, Document, PracticeQuestion, PracticeSet, ReviewSpace, WrongQuestion, new_id
from app.practice import PracticeService
from app.practice_progress import ANSWER_POLICY
from app.review_space import ReviewSpaceService
from test_app import upload


@pytest.fixture
def prepared_question(client, course, app):
    document = upload(client, course["id"], "paper.docx", kind="exam")
    practice = PracticeService(app.state.services)
    with app.state.services.sessions() as db:
        signature = practice._signature(practice._blocks(db, document["id"]))
        db.add(PracticeSet(document_id=document["id"], signature=signature, status="ready", progress=1))
        db.flush()
        q = PracticeQuestion(document_id=document["id"], position=0, number="1", section="RAG",
            question_type="single_choice", stem="Which option preserves source evidence?",
            options=[{"key": "A", "text": "Retrieval"}, {"key": "B", "text": "Guessing"}],
            locator={"label": "原题"},
            resolution={"answer": "选 A。", "choices": ["A"], "origin": "model", "resolved": True, "answer_policy": ANSWER_POLICY},
            resolution_version=db.get(Course, course["id"]).version)
        db.add(q)
        db.commit()
        snapshot = {"id": q.id, "document_id": q.document_id, "stem": q.stem, "options": deepcopy(q.options),
                    "number": q.number, "section": q.section, "question_type": q.question_type,
                    "resolution": deepcopy(q.resolution)}
    return practice, snapshot


def test_late_progress_put_cannot_overwrite_newer_answers(client, prepared_question):
    _, question = prepared_question
    identity = question["document_id"]
    initial = client.get(f"/documents/{identity}/practice/progress").json()
    newer = {"signature": initial["signature"], "revision": 20, "current_question_id": question["id"],
             "selections": {question["id"]: ["A"]}, "drafts": {question["id"]: "newer draft"}}
    assert client.put(f"/documents/{identity}/practice/progress", json=newer).status_code == 200
    older = {**newer, "revision": 19, "drafts": {question["id"]: "old response"}, "selections": {question["id"]: ["B"]}}
    assert client.put(f"/documents/{identity}/practice/progress", json=older).json()["revision"] == 20
    restored = client.get(f"/documents/{identity}/practice/progress").json()
    assert restored["state"]["drafts"] == newer["drafts"]
    assert restored["state"]["selections"] == newer["selections"]


def test_progress_rejects_stale_signature_and_foreign_question(client, prepared_question):
    _, question = prepared_question
    path = f"/documents/{question['document_id']}/practice/progress"
    initial = client.get(path).json()
    payload = {"signature": "outdated", "revision": 1, "current_question_id": question["id"],
               "selections": {}, "drafts": {}}
    assert client.put(path, json=payload).status_code == 409
    payload.update(signature=initial["signature"], current_question_id="other-paper-question")
    assert client.put(path, json=payload).status_code == 422
    assert client.get(path).json()["state"] == {}


def test_answer_reassociation_invalidates_reference_but_keeps_wrong_attempt(client, course, app, prepared_question):
    practice, snapshot = prepared_question
    answer = upload(client, course["id"], "answers.docx", kind="answer")
    with app.state.services.sessions() as db:
        q = db.get(PracticeQuestion, snapshot["id"])
        wrong = practice.wrong_questions.save_practice(db, q, db.get(Document, q.document_id),
            resolution=q.resolution, selected=["B"], incorrect=True)
        db.commit()
        wrong_id = wrong.id
    response = client.put(f"/documents/{snapshot['document_id']}/practice/answer-pairing",
                          json={"mode": "manual", "answer_document_id": answer["id"]})
    assert response.status_code == 200, response.text
    with app.state.services.sessions() as db:
        q, wrong = db.get(PracticeQuestion, snapshot["id"]), db.get(WrongQuestion, wrong_id)
        assert q.resolution == {} and q.resolution_version == -1
        assert wrong.resolution == {}
        assert wrong.selected == ["B"] and wrong.question["stem"] == snapshot["stem"]


def test_answer_pairing_rejects_other_courses(client, course, prepared_question):
    _, snapshot = prepared_question
    other = client.post("/courses", json={"name": "另一门课程"}).json()
    answer = upload(client, other["id"], "foreign-answers.docx", kind="answer")
    response = client.put(f"/documents/{snapshot['document_id']}/practice/answer-pairing",
                          json={"mode": "manual", "answer_document_id": answer["id"]})
    assert response.status_code == 422


def test_empty_model_extraction_is_retryable(client, course, app, monkeypatch):
    document = upload(client, course["id"], "unstructured-paper.docx", kind="exam")
    practice = PracticeService(app.state.services)
    with app.state.services.sessions() as db:
        pieces = practice._blocks(db, document["id"])
        db.add(PracticeSet(document_id=document["id"], signature=practice._signature(pieces), status="queued"))
        db.commit()
    monkeypatch.setattr(practice, "_json", lambda *args, **kwargs: {"questions": []})
    lock = Lock()
    lock.acquire()
    practice._extract(document["id"], pieces, lock)
    with app.state.services.sessions() as db:
        bank = db.get(PracticeSet, document["id"])
        assert bank.status == "failed" and "未识别到完整题目" in bank.error
    tasks = BackgroundTasks()
    assert practice.prepare(document["id"], tasks)["status"] == "queued"
    assert tasks.tasks
    # These queued tasks are deliberately not executed in this unit-level case.
    app.state.services.practice_locks[document["id"]].release()


def test_legacy_empty_ready_bank_can_be_reprepared(client, course, app):
    document = upload(client, course["id"], "empty-cache.docx", kind="exam")
    practice = PracticeService(app.state.services)
    with app.state.services.sessions() as db:
        db.add(PracticeSet(document_id=document["id"], signature=practice._signature(practice._blocks(db, document["id"])),
                           status="ready", progress=1))
        db.commit()
    tasks = BackgroundTasks()
    assert practice.prepare(document["id"], tasks)["status"] == "queued"
    assert tasks.tasks
    app.state.services.practice_locks[document["id"]].release()


@pytest.fixture
def question_space(course, app, prepared_question):
    _, question = prepared_question
    step_id, identity = new_id(), new_id()
    with app.state.services.sessions() as db:
        db.add(ReviewSpace(id=identity, course_id=course["id"], goal="复习 RAG", status="waiting_answer", revision=3,
            state={"course_version": db.get(Course, course["id"]).version, "attempts": [], "trace": [], "draft": {},
                   "messages": [], "pending_questions": [], "step": {"id": step_id, "kind": "question", "topic": "RAG",
                   "difficulty": "basic", "repeated": False, "question": question}}))
        db.commit()
    return identity, step_id


def test_space_draft_is_monotonic_and_private_answer_stays_hidden(client, question_space):
    identity, step_id = question_space
    path = f"/review-spaces/{identity}/draft"
    newer = {"step_id": step_id, "selected": ["A"], "own_answer": "新作答", "draft_revision": 50}
    assert client.put(path, json=newer).status_code == 200
    older = {**newer, "selected": ["B"], "own_answer": "旧作答", "draft_revision": 49}
    assert client.put(path, json=older).json()["draft"]["draft_revision"] == 50
    value = client.get(f"/review-spaces/{identity}").json()
    assert value["draft"]["own_answer"] == "新作答" and value["revision"] == 3
    assert "resolution" not in value["step"]["question"]
    assert client.put(path, json={**newer, "step_id": "old-step"}).status_code == 409


def test_space_answer_replay_records_one_attempt(client, question_space):
    identity, step_id = question_space
    payload = {"step_id": step_id, "selected": ["B"], "own_answer": "", "revision": 3}
    first = client.post(f"/review-spaces/{identity}/answer", json=payload)
    assert first.status_code == 200, first.text
    replay = client.post(f"/review-spaces/{identity}/answer", json=payload)
    assert replay.status_code == 200
    assert len(replay.json()["attempts"]) == 1
    assert replay.json()["revision"] == first.json()["revision"]


def test_space_answer_rejects_changed_course(client, course, app, question_space):
    identity, step_id = question_space
    with app.state.services.sessions() as db:
        app.state.services.changed(db, course["id"])
        db.commit()
    response = client.post(f"/review-spaces/{identity}/answer",
                           json={"step_id": step_id, "selected": ["A"], "revision": 3})
    assert response.status_code == 409
    assert client.get(f"/review-spaces/{identity}").json()["attempts"] == []


def test_assisted_success_does_not_clear_a_weak_topic(course, app, prepared_question):
    _, question = prepared_question
    memory = app.state.services.memory
    with app.state.services.sessions() as db:
        memory.record_attempt(db, course["id"], question, {"correct": False}, created_at="2026-01-01T00:00:00")
        assisted = memory.record_attempt(db, course["id"], question, {"correct": True}, created_at="2026-01-01T00:00:01")
        assisted.content = {**assisted.content, "independent": False}
        db.commit()
        row = memory._topics(db, course["id"])[0]
        assert row["status"] == "needs_review"
        assert row["incorrect_count"] == row["correct_count"] == 1
        independent = memory.record_attempt(db, course["id"], question, {"correct": True}, created_at="2026-01-01T00:00:02")
        independent.content = {**independent.content, "independent": True}
        db.commit()
        assert memory._topics(db, course["id"])[0]["status"] == "recent_correct"


@pytest.mark.parametrize("feedback,preserved", [("给我一点提示", True), ("这批题先不做，继续聊刚才的知识点。", False)])
def test_question_followup_preserves_draft_and_remaining_batch(course, app, prepared_question, question_space, monkeypatch, feedback, preserved):
    practice, _ = prepared_question
    review = ReviewSpaceService(practice)
    services = app.state.services
    identity, step_id = question_space
    with services.sessions() as db:
        db.add(Document(id=new_id(), course_id=course["id"], filename="lecture.txt", kind="lecture",
                        status="ready", file_hash="hint-lecture", path="lecture.txt"))
        value = db.get(ReviewSpace, identity)
        next_step = {**deepcopy(value.state["step"]), "id": new_id()}
        value.state = {**value.state, "pending_questions": [next_step],
                       "messages": [review._step_message(value.state["step"])],
                       "draft": {"step_id": step_id, "selected": ["B"], "own_answer": "未完成的草稿", "draft_revision": 5}}
        db.commit()
    monkeypatch.setattr(services.llm, "complete", lambda *args, **kwargs: json.dumps(
        {"action": "explain", "topic": "RAG", "knowledge_source": "model"}))
    explanations = []

    def explain(messages):
        explanations.append(json.loads(messages[-1]["content"]))
        return "先考虑检索证据如何帮助回答，再判断各选项。"

    monkeypatch.setattr(services.llm, "complete_markdown", explain, raising=False)
    monkeypatch.setattr(review, "_course_sources", lambda *args, **kwargs: ([], {"request": feedback, "topics": ["RAG"]}))
    tasks = BackgroundTasks()
    review.advance(identity, {"revision": 3, "feedback": feedback}, tasks)
    task = tasks.tasks[0]
    task.func(*task.args, **task.kwargs)
    value = review.get(identity)
    if not preserved:
        assert value["status"] == "reading"
        with services.sessions() as db:
            assert db.get(ReviewSpace, identity).state["pending_questions"] == []
        return
    assert value["status"] == "waiting_answer" and value["step"]["id"] == step_id
    assert value["draft"]["selected"] == ["B"] and value["draft"]["own_answer"] == "未完成的草稿"
    assert value["draft"]["used_hint"] is True
    assert value["messages"][-1]["kind"] == "explanation"
    assert "resolution" not in explanations[0]["active_question"]
    result = review.answer(identity, {"revision": value["revision"], "step_id": step_id,
        "selected": ["A"], "own_answer": "", "used_hint": False, "seen_answer": False})
    assert result["step"]["id"] == next_step["id"] and result["status"] == "waiting_answer"
    assert result["attempts"][-1]["used_hint"] is True


def test_equal_revision_with_different_progress_is_a_conflict(client, prepared_question):
    _, question = prepared_question
    path = f"/documents/{question['document_id']}/practice/progress"
    initial = client.get(path).json()
    payload = {"signature": initial["signature"], "revision": 20, "current_question_id": question["id"],
               "selections": {question["id"]: ["A"]}, "drafts": {question["id"]: "first tab"}}
    assert client.put(path, json=payload).status_code == 200
    assert client.put(path, json=payload).status_code == 200
    assert client.put(path, json={**payload, "drafts": {question["id"]: "second tab"}}).status_code == 409
    assert client.get(path).json()["state"]["drafts"] == payload["drafts"]
