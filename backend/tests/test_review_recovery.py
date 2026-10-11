"""Recovery contracts with controlled outputs; no live model quality claims."""
from contextlib import nullcontext
from copy import deepcopy
import json
from threading import Lock, RLock
from types import SimpleNamespace

import pytest

from app.db import create_database
from app.model_config import ModelConfig
from app.models import Course, ReviewSpace
from app.practice_generation import PracticeGenerationService
from app.review_space import GenerationFailed, ReviewSpaceService, materials_only_request


def config_store():
    return SimpleNamespace(snapshot=lambda: ModelConfig(context_window=32768, max_tokens=8192))


def material():
    return {"source_id": "Sunit", "chunk_id": "unit", "id": "unit", "kind": "lecture",
            "filename": "lecture.pptx", "locator": {"slide": 2}, "title": "Binary Heap",
            "text": "Binary Heap: the left child is at 2i, the right child at 2i+1.",
            "query_topics": ["二叉堆"]}


@pytest.mark.parametrize("missing", [False, True])
def test_current_request_evidence_survives_topic_rewording_and_empty_results_can_answer(tmp_path, missing):
    engine, sessions = create_database(tmp_path)
    request = "仅根据课程资料讲解二叉堆的孩子下标。"
    calls, explanations = [], []
    class Model:
        def complete(self, messages, json_mode=False):
            return json.dumps({"action": "search_materials" if not calls else "explain",
                               "topic": "二叉堆" if not calls else "下标从1开始的二叉堆数组表示",
                               "knowledge_source": "model"})
        def complete_markdown(self, messages):
            payload = json.loads(messages[-1]["content"])
            explanations.append(payload)
            return "左孩子下标是 2i，右孩子下标是 2i+1。"
    def retrieve(course, query, **kwargs):
        calls.append(query)
        return ([] if missing else [material()]), {"request": request, "query": query,
                                                 "topics": [query], "coverage": [], "rerank": {}}
    services = SimpleNamespace(sessions=sessions, lock=RLock(), model_config=config_store(),
                               llm=Model(), knowledge=SimpleNamespace(retrieve=retrieve))
    review = ReviewSpaceService(SimpleNamespace(s=services))
    lock = Lock()
    lock.acquire()
    try:
        with sessions() as db:
            db.add(Course(id="c", name="Course", version=0))
            db.flush()
            db.add(ReviewSpace(id="r", course_id="c", goal=request, status="processing", revision=0,
                state={"course_version": 0, "run_token": "token", "chapter_ids": [], "chapters": [],
                       "attempts": [], "trace": [], "messages": [{"id": "u", "role": "user", "content": request}]}))
            db.commit()
        review.run("r", "token", lock)
        with sessions() as db:
            value = db.get(ReviewSpace, "r")
            assert value.status == "reading" and len(calls) == 1
            step = value.state["step"]
            assert not step["model_knowledge"] and not step["external_knowledge"]
            if missing:
                assert "本轮未找到" in step["content"] and not explanations
            else:
                assert explanations[0]["materials"][0]["text"] == material()["text"]
                assert explanations[0]["knowledge_source"] == "materials"
    finally:
        if lock.locked():
            lock.release()
        engine.dispose()


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_question_id_repair_is_bounded_and_maps_only_supplied_sources(repair_succeeds):
    services = SimpleNamespace(sessions=lambda: nullcontext(None), model_config=config_store())
    practice = SimpleNamespace(s=services, _labels=lambda choices, labels, kind: [])
    practice.generated = PracticeGenerationService(practice)
    practice.generated._context = lambda *args: {"course": "Course", "chapters": [], "pools": []}
    review = ReviewSpaceService(practice)
    value = SimpleNamespace(course_id="c", goal="栈", state={"chapter_ids": [], "attempts": [], "messages": []})
    attempts = []
    def generated(rules, payload):
        attempts.append(deepcopy(payload))
        identity = "M0" if repair_succeeds and len(attempts) == 2 else "invented"
        return {"question_type": "other", "stem": "请说明栈的后进先出顺序。", "answer": "先入栈的元素后出栈。",
                "options": [], "choices": [], "source_ids": [identity]}
    review._json = generated
    args = (value, "栈", "medium", [material()], False, "other", "根据课件出一道新的简答题", [], 1)
    if repair_succeeds:
        question = review._generate(*args)
        assert question["resolution"]["sources"][0]["chunk_id"] == "unit"
    else:
        with pytest.raises(GenerationFailed, match="一次修复"):
            review._generate(*args)
    assert len(attempts) == 2
    assert attempts[0]["materials"][0]["id"] == "M0"
    assert "source_id" not in attempts[0]["materials"][0]
    assert attempts[1]["allowed_source_ids"] == ["M0"]
    assert attempts[1]["previous_output"]["source_ids"] == ["invented"]


def test_material_only_preference_persists_until_explicitly_relaxed():
    history = ["仅根据课程资料解释。"]
    assert materials_only_request("继续说下去", history)
    assert materials_only_request("仅根据课程资料，不允许模型知识补充", history)
    assert not materials_only_request("可以用模型知识补充", history)


def test_explanation_receives_complete_selected_scope():
    review = ReviewSpaceService(SimpleNamespace(s=SimpleNamespace(model_config=config_store())))
    value = SimpleNamespace(goal="汇总排序", state={"chapter_ids": ["one", "two"], "chapters": ["Sorting I", "Sorting V"]})
    payload = {"feedback": "汇总排序", "course_chapters": ["Sorting I", "Sorting V"], "dialogue": []}
    files = [{"filename": "Sorting I.pptx"}, {"filename": "Sorting V.pptx"}]
    explanation, _ = review._explanation_context(value, "排序", payload, "Course", files, [], {})
    assert explanation["search_scope"]["chapter_ids"] == ["one", "two"]
    assert explanation["search_scope"]["chapters"] == ["Sorting I", "Sorting V"]
    assert explanation["search_scope"]["documents"] == [f["filename"] for f in files]
