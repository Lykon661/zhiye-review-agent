"""Cache and planner contracts with controlled output; no live token/quality claims."""
from copy import deepcopy
from dataclasses import replace
import json
from threading import Lock, RLock
from types import SimpleNamespace

import pytest

from app.db import create_database
from app.model_config import ModelConfig
from app.models import Course, ReviewSpace
from app.retrieval_ranking import KnowledgeReranker
from app.review_space import ReviewSpaceService


QUOTE = "Worst-case time O(n log n)."
REQUEST = "对比堆排序的时间复杂度"


def material(identity="one", text=None):
    return {"source_id": "S" + identity, "chunk_id": identity, "course_id": "c", "document_id": "d",
        "filename": "lec7-5.pptx", "kind": "lecture", "title": "Heap Sort", "locator": {"slide": 1},
        "text": text or "Heap Sort\n\n" + QUOTE, "score": 0.8, "query_topics": ["堆排序"]}


def store():
    value = SimpleNamespace(value=ModelConfig())
    value.snapshot = lambda: value.value
    return value


class RankingModel:
    configured = True

    def __init__(self):
        self.calls, self.invalid = [], False

    def complete(self, messages, json_mode=False):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        ranks = [{"id": c["id"], "score": 0 if "Only homework" in c["text"] else 80,
                  "topics": payload["requested_topics"]} for c in payload["candidates"]]
        if self.invalid:
            ranks[0]["id"] = "not-supplied"
        return json.dumps({"rankings": ranks, "coverage": [
            {"id": c["id"], "topic": t, "facet": "时间复杂度", "quote": QUOTE}
            for c in payload["candidates"] if QUOTE in c["text"]
            for t in payload["requested_topics"]]}, ensure_ascii=False)


def rank(reranker, sources, *, request=REQUEST, topics=None, facets=None, context=None):
    return reranker.rank(request, sources, topics if topics is not None else ["堆排序"],
        facets if facets is not None else ["时间复杂度"], context=context or {"course_version": 0, "chapter_ids": []})


def complete_report(request=REQUEST):
    return {"request": request, "query": "堆排序", "mode": "comparison", "topics": ["堆排序"],
        "facets": ["时间复杂度"], "course_version": 0, "chapter_ids": [], "retrieval_errors": [],
        "coverage": [{"topic": "堆排序", "source_ids": ["Sone"], "has_material": True,
                      "assessed": True, "covered_facets": ["时间复杂度"], "missing_facets": []}],
        "rerank": {"assessed": True, "scores": {"Sone": 80}, "coverage": [
            {"source_id": "Sone", "topic": "堆排序", "facet": "时间复杂度", "quote": QUOTE}]}}


def test_cache_reuses_scores_and_rejected_candidates_but_returns_fresh_sources():
    model = RankingModel()
    reranker = KnowledgeReranker(model, store())
    sources = [material(), material("homework", "Only homework: compare sorts.")]
    first, _ = rank(reranker, sources)
    fresh = {**sources[0], "score": 0.25, "query_topics": ["当前检索"], "fresh_metadata": "current"}
    result, report = rank(reranker, [fresh, sources[1], material("new")])
    assert len(model.calls) == 2 and len(model.calls[-1]["candidates"]) == 1
    assert report["cache_hits"] == 2 and report["model_calls"] == 1 and report["assessed"]
    assert report["scores"]["Shomework"] == 0
    assert {s["source_id"] for s in result} == {"Sone", "Snew"}
    returned = next(s for s in result if s["source_id"] == "Sone")
    assert returned["text"] == first[0]["text"] and returned["hybrid_score"] == 0.25
    assert returned["query_topics"] == ["当前检索"] and returned["fresh_metadata"] == "current"
    _, repeated = rank(reranker, [fresh, sources[1], material("new")])
    assert len(model.calls) == 2 and repeated["cache_hits"] == 3 and repeated["model_calls"] == 0


@pytest.mark.parametrize("change", ["request", "topics", "facets", "version", "chapters", "course",
                                  "model", "window", "text", "filename", "locator"])
def test_cache_invalidates_when_evidence_scope_request_or_model_changes(change):
    config, model = store(), RankingModel()
    reranker = KnowledgeReranker(model, config)
    original = material()
    rank(reranker, [original])
    source, kwargs = deepcopy(original), {}
    if change == "request":
        kwargs["request"] = "只比较堆排序的时间复杂度"
    elif change == "topics":
        kwargs["topics"] = ["Heap Sort"]
    elif change == "facets":
        kwargs["facets"] = ["稳定性"]
    elif change in ("version", "chapters", "course"):
        kwargs["context"] = {"course_version": 1 if change == "version" else 0,
            "chapter_ids": ["new-chapter"] if change == "chapters" else [],
            **({"course_id": "other"} if change == "course" else {})}
    elif change == "model":
        config.value = replace(config.value, model="different-model")
    elif change == "window":
        config.value = replace(config.value, context_window=16384, max_tokens=4096)
    elif change == "text":
        source["text"] += "\n\nUpdated course analysis."
    elif change == "filename":
        source["filename"] = "renamed.pptx"
    elif change == "locator":
        source["locator"] = {"slide": 2}
    _, report = rank(reranker, [source], **kwargs)
    assert len(model.calls) == 2 and report["cache_hits"] == 0 and report["model_calls"] == 1


def test_invalid_output_is_not_cached_and_later_valid_result_can_be_reused():
    model = RankingModel()
    reranker = KnowledgeReranker(model, store())
    model.invalid = True
    _, failed = rank(reranker, [material()])
    assert not failed["assessed"] and failed["model_calls"] == 1
    model.invalid = False
    _, recovered = rank(reranker, [material()])
    _, cached = rank(reranker, [material()])
    assert len(model.calls) == 2 and recovered["assessed"] and cached["model_calls"] == 0


def test_fuller_excerpt_can_reassess_a_previously_cached_page():
    model = RankingModel()
    reranker = KnowledgeReranker(model, store())
    long = material(text=QUOTE + "\n\n" + "\n\n".join("Heap step " + str(i) + " " + "x" * 260 for i in range(35)))
    rank(reranker, [long, *(material(str(i)) for i in range(23))])
    _, report = rank(reranker, [long])
    assert report["cache_hits"] == 0 and len(model.calls) == 2
    assert len(model.calls[1]["candidates"][0]["text"]) > len(model.calls[0]["candidates"][0]["text"])


def test_compact_planner_preserves_request_gaps_and_question_candidates_without_mutating_explanation():
    service = ReviewSpaceService(SimpleNamespace(s=SimpleNamespace()))
    row = complete_report()["coverage"][0]
    row["missing_facets"] = ["稳定性"]
    payload = {"feedback": REQUEST, "available_materials": [{"text": "PRIVATE_ORIGINAL"}],
        "material_coverage": [row], "last_step": {"kind": "explanation", "topic": "堆排序", "content": "OLD_EXPLANATION"},
        "last_answer": {"question": "stem", "reference_analysis": "REFERENCE_ANALYSIS"},
        "observations": [{"action": "search_materials", "found": True, "coverage": [row],
                          "materials": [{"text": "PRIVATE_ORIGINAL"}]},
                         {"action": "list_questions", "questions": [{"id": "q1", "stem": "Select a heap."}]}],
        "course_materials": [{"filename": str(i) + ".pptx", "status": "ready"} for i in range(100)],
        "course_material_status": {"count": 120, "statuses": {"ready": 120}}}
    original = deepcopy(payload)
    compact = service._planner_payload(payload, [material()], {"coverage": [row]})
    assert payload == original and compact["feedback"] == REQUEST
    assert all(marker not in json.dumps(compact) for marker in ("PRIVATE_ORIGINAL", "OLD_EXPLANATION", "REFERENCE_ANALYSIS"))
    assert compact["material_summary"]["source_count"] == 1
    assert compact["material_summary"]["coverage"][0]["missing_facets"] == ["稳定性"]
    assert compact["observations"][1] == original["observations"][1]
    assert len(compact["course_materials"]) == 16 and compact["course_material_status"]["omitted_files"] == 104


@pytest.mark.parametrize("case", ["complete", "gap", "unassessed", "bad_quote", "omitted_source",
                                "practice", "finish", "ambiguous", "different_request"])
def test_direct_explanation_requires_explicit_intent_and_verified_current_evidence(case):
    report, sources, request = complete_report(), [material()], REQUEST
    if case == "gap":
        report["coverage"][0]["missing_facets"] = ["稳定性"]
    elif case == "unassessed":
        report["rerank"]["assessed"] = False
    elif case == "bad_quote":
        report["rerank"]["coverage"][0]["quote"] = "Invented evidence."
    elif case == "omitted_source":
        sources = []
    elif case in ("practice", "finish", "ambiguous"):
        request = {"practice": "先解释堆排序，再出题", "finish": "总结堆排序并结束复习", "ambiguous": "继续"}[case]
        report["request"] = request
    elif case == "different_request":
        report["request"] = "比较所有算法"
    assert ReviewSpaceService._ready_for_explanation(request, sources, report) is (case == "complete")


@pytest.mark.parametrize("has_gap", [False, True])
def test_review_run_skips_one_planner_call_only_when_evidence_is_complete(tmp_path, has_gap):
    engine, sessions = create_database(tmp_path)
    report, evidence = complete_report(), material()
    if has_gap:
        report["facets"].append("稳定性")
        report["coverage"][0]["missing_facets"] = ["稳定性"]
    class ReviewModel:
        def __init__(self):
            self.plans, self.explanations = [], []
        def complete(self, messages, json_mode=False):
            self.plans.append(json.loads(messages[-1]["content"]))
            return json.dumps({"action": "search_materials" if len(self.plans) == 1 else "explain",
                "topic": "堆排序", "query": "堆排序", "knowledge_source": "materials"})
        def complete_markdown(self, messages):
            self.explanations.append(json.loads(messages[-1]["content"]))
            return "课程说明堆排序最坏时间为 O(n log n)。"
    model = ReviewModel()
    services = SimpleNamespace(sessions=sessions, lock=RLock(), model_config=store(), llm=model,
        knowledge=SimpleNamespace(retrieve=lambda *args, **kwargs: ([deepcopy(evidence)], deepcopy(report))))
    review = ReviewSpaceService(SimpleNamespace(s=services))
    lock = Lock()
    lock.acquire()
    try:
        with sessions() as db:
            db.add(Course(id="c", name="Sorting", version=0))
            db.flush()
            db.add(ReviewSpace(id="r", course_id="c", goal=REQUEST, status="processing", revision=0,
                state={"course_version": 0, "run_token": "token", "chapter_ids": [], "chapters": [],
                       "attempts": [], "trace": [], "messages": [{"id": "u", "role": "user", "content": REQUEST}]}))
            db.commit()
        review.run("r", "token", lock)
        with sessions() as db:
            value = db.get(ReviewSpace, "r")
            assert value.status == "reading" and value.state["run_token"] is None
            assert [t["action"] for t in value.state["trace"]] == ["search_materials", "explain"]
            assert value.state["action_count"] == 2
        # Diagnostics are stored with the reply message and read through the report endpoint.
        diagnostics = review.retrieval_report("r")["report"]["planner"]
        assert diagnostics["model_calls"] == (2 if has_gap else 1)
        assert diagnostics["skipped_calls"] == (0 if has_gap else 1)
        assert len(model.plans) == (2 if has_gap else 1) and len(model.explanations) == 1
        assert model.explanations[0]["materials"][0]["text"] == evidence["text"]
        assert bool(model.explanations[0]["coverage_gaps"]) is has_gap
        assert all("available_materials" not in p for p in model.plans)
    finally:
        if lock.locked():
            lock.release()
        engine.dispose()
