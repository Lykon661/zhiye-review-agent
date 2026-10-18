"""Durable question checkpoints with controlled output, without live model calls."""
from copy import deepcopy
import json
from threading import Lock, RLock
from types import SimpleNamespace

from fastapi import BackgroundTasks
import pytest
from sqlalchemy import select

from app.db import create_database
from app.model_config import ModelConfig
from app.models import AppMeta, Course, Document, PracticeQuestion, PracticeSet, ReviewSpace
from app.practice import PracticeService
from app.review_space import GenerationFailed, ReviewSpaceService
from app.token_usage import record_usage


@pytest.fixture
def services(tmp_path):
    engine, sessions = create_database(tmp_path)
    value = SimpleNamespace(sessions=sessions, lock=RLock(), upload_dir=tmp_path,
        practice_locks={}, review_space_locks={}, explanation_locks={},
        llm=SimpleNamespace(configured=True),
        model_config=SimpleNamespace(snapshot=lambda: ModelConfig(context_window=32768, max_tokens=8192)))
    with sessions() as db:
        db.add(Course(id="course", name="数据结构", version=0))
        db.commit()
    yield value
    engine.dispose()


def request():
    return {"mode": "topics", "chapter_ids": [], "topic": "栈", "weak_topic_ids": [],
            "count": 3, "question_type": "other", "difficulty": "medium"}


def raw_question(number):
    return {"knowledge_point": "栈", "question_type": "other", "stem": f"请说明栈的第{number}种应用场景。",
            "options": [], "choices": [], "answer": f"第{number}种应用遵循后进先出。", "source_ids": []}


def run_queued(tasks):
    for task in tasks.tasks:
        task.func(*task.args, **task.kwargs)


@pytest.mark.parametrize("missing_request_file", [False, True])
def test_practice_retry_preserves_validated_questions_context_and_progress(services, monkeypatch, missing_request_file):
    practice = PracticeService(services)
    calls = []
    def generate(rules, payload):
        calls.append(deepcopy(payload))
        if len(calls) == 3:
            raise RuntimeError("model interrupted")
        return raw_question(len(calls))
    monkeypatch.setattr(practice, "_json", generate)
    tasks = BackgroundTasks()
    document = practice.generated.start("course", request(), tasks)
    run_queued(tasks)
    identity = document["id"]
    with services.sessions() as db:
        bank = db.get(PracticeSet, identity)
        assert bank.status == "failed" and bank.progress == 2 / 3
        saved = json.loads(db.get(AppMeta, "practice_generation:" + identity).value)
        assert len(saved["questions"]) == 2
        assert not list(db.scalars(select(PracticeQuestion)))

    # A new service must use the durable snapshot rather than recomputing targets.
    recovered = PracticeService(services)
    if missing_request_file:
        (services.upload_dir / (identity + ".json")).unlink()
    def unexpected_context(*args):
        raise AssertionError("retry recomputed the original generation context")
    monkeypatch.setattr(recovered.generated, "_context", unexpected_context)
    remaining = []
    monkeypatch.setattr(recovered, "_json", lambda rules, payload: remaining.append(deepcopy(payload)) or raw_question(3))
    tasks = BackgroundTasks()
    assert recovered.generated.retry(identity, tasks)["status"] == "queued"
    with services.sessions() as db:
        assert db.get(PracticeSet, identity).progress == 2 / 3
    run_queued(tasks)
    assert len(remaining) == 1
    assert remaining[0]["previous"] == [raw_question(i)["stem"] for i in (1, 2)]
    with services.sessions() as db:
        questions = list(db.scalars(select(PracticeQuestion).order_by(PracticeQuestion.position)))
        assert [q.stem for q in questions] == [raw_question(i)["stem"] for i in (1, 2, 3)]
        assert db.get(PracticeSet, identity).status == "ready"
        assert db.get(AppMeta, "practice_generation:" + identity) is None


@pytest.mark.parametrize("change", ["course_version", "answer_policy", "request"])
def test_practice_does_not_reuse_incompatible_checkpoints(services, monkeypatch, change):
    practice = PracticeService(services)
    calls = []
    def generate(rules, payload):
        calls.append(deepcopy(payload))
        if len(calls) == 2:
            raise RuntimeError("interrupted")
        return raw_question(len(calls))
    monkeypatch.setattr(practice, "_json", generate)
    settings = {**request(), "count": 2}
    tasks = BackgroundTasks()
    identity = practice.generated.start("course", settings, tasks)["id"]
    run_queued(tasks)
    with services.sessions() as db:
        if change == "course_version":
            db.get(Course, "course").version += 1
        elif change == "answer_policy":
            row = db.get(AppMeta, "practice_generation:" + identity)
            saved = json.loads(row.value)
            row.value = json.dumps({**saved, "answer_policy": "old"})
        else:
            settings["difficulty"] = "advanced"
            (services.upload_dir / (identity + ".json")).write_text(json.dumps(settings), encoding="utf-8")
        db.commit()
    tasks = BackgroundTasks()
    practice.generated.retry(identity, tasks)
    with services.sessions() as db:
        assert db.get(PracticeSet, identity).progress == 0
    run_queued(tasks)
    assert calls[2]["previous"] == []
    with services.sessions() as db:
        assert [q.stem for q in db.scalars(select(PracticeQuestion).order_by(PracticeQuestion.position))] == [raw_question(i)["stem"] for i in (3, 4)]


def test_complete_checkpoint_can_publish_without_generating_again(services, monkeypatch):
    practice = PracticeService(services)
    settings = request()
    identity = practice.generated.start("course", settings, BackgroundTasks())["id"]
    with services.sessions() as db:
        saved = json.loads(db.get(AppMeta, "practice_generation:" + identity).value)
        questions = [practice.generated._question(raw_question(i), settings, "栈", [], []) for i in (1, 2, 3)]
        practice.generated._save_checkpoint(db, identity, settings, saved["context"], questions)
        bank = db.get(PracticeSet, identity)
        bank.status, bank.progress = "failed", 1
        db.commit()
    services.practice_locks[identity].release()
    def unexpected_model_call(*args):
        raise AssertionError("all questions were already durably saved")
    monkeypatch.setattr(practice, "_json", unexpected_model_call)
    tasks = BackgroundTasks()
    practice.generated.retry(identity, tasks)
    run_queued(tasks)
    with services.sessions() as db:
        assert db.get(PracticeSet, identity).status == "ready"
        assert len(list(db.scalars(select(PracticeQuestion)))) == 3
        assert db.get(AppMeta, "practice_generation:" + identity) is None


@pytest.mark.parametrize("restart", [False, True])
def test_review_batch_retry_resumes_without_replanning_or_duplicate_publication(services, monkeypatch, restart):
    practice = PracticeService(services)
    review = ReviewSpaceService(practice)
    plan = {"action": "generate_question", "topic": "栈", "question_type": "other", "count": 3,
            "difficulty": "medium", "knowledge_source": "model", "user_requested_practice": True}
    planner_calls, questions = [], []
    services.llm.complete = lambda messages, **kwargs: planner_calls.append(messages) or json.dumps(plan)
    original = "请出三道栈的中等难度简答题，使用生活中的例子。"
    with services.sessions() as db:
        db.add(Document(id="lecture", course_id="course", filename="栈课件", kind="lecture", status="ready",
                        file_hash="lecture", path=str(services.upload_dir / "lecture")))
        db.add(ReviewSpace(id="review", course_id="course", goal=original, status="processing", revision=0,
            state={"course_version": 0, "run_token": "token", "chapter_ids": [], "chapters": [],
                   "attempts": [], "trace": [], "messages": [{"id": "user", "role": "user", "content": original}]}))
        db.commit()
    def generate(value, topic, difficulty, sources, allow_model, kind, requirements, previous, total):
        questions.append((requirements, deepcopy(previous)))
        record_usage({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                      "cache_read_tokens": 0, "cache_write_tokens": 0}, "answer")
        if len(questions) == 3:
            raise GenerationFailed("third question interrupted")
        raw = raw_question(len(previous) + 1)
        return practice.generated._question(raw, request(), topic, previous, [])
    monkeypatch.setattr(review, "_generate", generate)
    lock = Lock()
    lock.acquire()
    review.run("review", "token", lock)
    with services.sessions() as db:
        value = db.get(ReviewSpace, "review")
        assert value.status == "failed"
        assert len(value.state["generation_checkpoint"]["questions"]) == 2
        visible = review.public(value)
        assert visible["generation_progress"] == {"completed": 2, "total": 3}
        assert "generation_checkpoint" not in visible and not visible["step"]
    if restart:
        review = ReviewSpaceService(practice)
        monkeypatch.setattr(review, "_generate", generate)
        review.recover()
        assert review.get("review")["status"] == "paused"
    tasks = BackgroundTasks()
    review.advance("review", {"feedback": "", "revision": review.get("review")["revision"]}, tasks)
    run_queued(tasks)
    assert len(planner_calls) == 1 and len(questions) == 4
    assert questions[-1] == (original, [raw_question(i)["stem"] for i in (1, 2)])
    with services.sessions() as db:
        value = db.get(ReviewSpace, "review")
        assert value.status == "waiting_answer"
        assert value.state["generation_checkpoint"] is None
        batch = [value.state["step"], *value.state["pending_questions"]]
        assert [step["question"]["stem"] for step in batch] == [raw_question(i)["stem"] for i in (1, 2, 3)]
        assert value.state["step"]["token_usage"]["total_tokens"] == 60
        assert len(list(db.scalars(select(PracticeQuestion)))) == 3


def test_new_review_request_discards_unfinished_batch(services):
    review = ReviewSpaceService(PracticeService(services))
    with services.sessions() as db:
        db.add(Document(id="lecture", course_id="course", filename="课件", kind="lecture", status="ready",
                        file_hash="lecture", path=str(services.upload_dir / "lecture")))
        db.add(ReviewSpace(id="review", course_id="course", goal="栈", status="failed", revision=1,
            state={"course_version": 0, "chapter_ids": [], "chapters": [], "trace": [], "messages": [],
                   "generation_checkpoint": {"plan": {"count": 3}, "questions": [raw_question(1)]}}))
        db.commit()
    tasks = BackgroundTasks()
    result = review.advance("review", {"feedback": "先不做题，解释队列。", "revision": 1}, tasks)
    assert result["generation_progress"] is None
    services.review_space_locks["review"].release()
