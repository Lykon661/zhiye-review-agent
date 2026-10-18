"""Evaluation bookkeeping contracts; these do not measure real model quality."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_data_structures.py"
spec = importlib.util.spec_from_file_location("course_evaluation", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def sample_case():
    return {"id": "q1", "category": "knowledge", "question": "Explain the algorithm.",
            "criteria": [{"id": "rule", "description": "Explain the supported rule.", "evidence": [
                {"document": "lecture", "filename": "lecture.pptx", "slide": 3, "quote": "Only comparisons require the stated lower bound."}]}]}


def test_evidence_excludes_unsupplied_context_wrong_pages_and_web_content():
    case = sample_case()
    quote = case["criteria"][0]["evidence"][0]["quote"]
    page = {"filename": "lecture.pptx", "locator": {"slide": 3}, "kind": "lecture"}
    selected = [page]
    sources = [{**page, "text": "A short excerpt", "context": [{"text": quote}]},
               {**page, "locator": {"slide": 4}, "text": quote}, {**page, "kind": "web", "text": quote}]
    result = evaluation.score_evidence(case, sources, selected)
    assert result["selected_hits"] == 1 and result["supplied_hits"] == 0
    sources.append({**page, "text": quote.replace(" ", "\n")})
    assert evaluation.score_evidence(case, sources, selected)["supplied_hits"] == 1


def test_unknown_usage_is_not_zero_or_a_complete_total():
    known = {"status": "complete", "model_calls": 1, "reported_calls": 1, "unreported_calls": 0,
             "input_tokens": 60, "output_tokens": 40, "total_tokens": 100}
    partial = evaluation.aggregate_usage([known, None])
    assert partial["status"] == "partial" and partial["total_tokens"] == 100
    assert partial["model_calls"] is None and partial["missing_replies"] == 1
    unavailable = evaluation.aggregate_usage([None])
    assert unavailable["total_tokens"] is None and unavailable["status"] == "unavailable"


def test_absence_terms_use_word_boundaries_for_english_acronyms():
    assert not evaluation.contains_term("used as DS", "SDS")
    assert evaluation.contains_term("Redis SDS structures", "SDS")
    assert evaluation.contains_term("React\nFiber scheduling", "React Fiber")


def test_judge_rejects_invented_answer_and_reference_quotes():
    case = sample_case()
    rating = {"id": "rule", "score": 2, "reason": "The rule is explained.",
              "answer_quote": "The answer never said this", "reference_quote": case["criteria"][0]["evidence"][0]["quote"]}
    answer = "The method uses only comparisons."
    with pytest.raises(ValueError, match="回答引用不存在"):
        evaluation.validate_judgment(case, answer, {"ratings": [rating]})
    rating.update(answer_quote=answer, reference_quote="invented textbook support")
    with pytest.raises(ValueError, match="课程引用"):
        evaluation.validate_judgment(case, answer, {"ratings": [rating]})


def test_stale_gold_fails_before_any_evaluation_calls():
    case = sample_case()
    corpus = {"documents": [{"id": "doc", "filename": "lecture.pptx", "file_hash": "hash"}],
              "pages": {("doc", 3): "The source text was replaced."}, "chapters": {}, "text": ""}
    with pytest.raises(ValueError, match="原文已变化"):
        evaluation.bind_cases([case], corpus)


def test_database_snapshot_does_not_write_back_to_original(tmp_path):
    source, scratch = tmp_path / "source", tmp_path / "scratch"
    source.mkdir()
    scratch.mkdir()
    with sqlite3.connect(source / "review.db") as connection:
        connection.execute("CREATE TABLE courses (name TEXT)")
        connection.execute("INSERT INTO courses VALUES ('original')")
    evaluation.snapshot_data(source, scratch)
    with sqlite3.connect(scratch / "review.db") as connection:
        connection.execute("UPDATE courses SET name='evaluation'")
    with sqlite3.connect(source / "review.db") as connection:
        assert connection.execute("SELECT name FROM courses").fetchone() == ("original",)


def test_pending_manual_grades_are_not_reported_as_verified_accuracy(tmp_path):
    case = sample_case()
    row = {"id": "q1", "category": "knowledge", "case": case, "turns": [], "answer": "=UNTRUSTED()",
           "status": "failed", "functional_success": False, "error": "No answer",
           "evidence": evaluation.score_evidence(case, [], [])}
    manifest = {"course": {"name": "Course", "version": 1}, "label": "test", "planned_cases": 1,
                **{key: "hash" for key in ("questions_sha256", "corpus_sha256", "gold_sha256", "code_sha256", "memory_sha256")}}
    evaluation.write_csv(tmp_path, [row])
    evaluation.write_report(tmp_path, [row], manifest)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "已判 0/1 题" in text and "不冒充准确率" in text
    assert "'=UNTRUSTED()" in (tmp_path / "results.csv").read_text(encoding="utf-8-sig")


def test_judge_accepts_equivalent_supplied_original_on_a_different_page():
    case = sample_case()
    source = {"source_id": "Sother", "filename": "lecture.pptx", "kind": "lecture",
              "locator": {"slide": 7}, "text": "Comparison-only sorting has this lower bound."}
    answer = "The method uses **only comparisons**."
    rating = {"id": "rule", "score": 2, "reason": "An equivalent original supports the same rule.",
              "answer_quote": "only comparisons", "reference_quote": source["text"], "reference_source_id": "Sother"}
    assert evaluation.validate_judgment(case, answer, {"ratings": [rating]}, [source])["score"] == 2
    with pytest.raises(ValueError, match="课程引用"):
        evaluation.validate_judgment(case, answer, {"ratings": [rating]}, [{**source, "kind": "web"}])
    with pytest.raises(ValueError, match="课程引用"):
        evaluation.validate_judgment(case, answer, {"ratings": [rating]}, [])


def test_judge_does_not_reward_labeled_model_supplements_against_material_only_request():
    case = sample_case()
    answer = "含模型已有知识的补充。 The method uses only comparisons."
    rating = {"id": "rule", "score": 2, "reason": "Content is plausible.", "answer_quote": "only comparisons",
              "reference_quote": case["criteria"][0]["evidence"][0]["quote"]}
    judgment = {"ratings": [rating], "instruction_following": {"passed": False,
        "reason": "The user allowed only course originals.", "answer_quote": "含模型已有知识的补充"}}
    result = evaluation.validate_judgment(case, answer, judgment, require_instruction=True)
    assert result["content_score"] == 2 and result["score"] == 0


def test_short_exact_answer_quotes_are_valid():
    case = sample_case()
    case["criteria"][0]["evidence"] = []
    rating = {"id": "rule", "score": 2, "reason": "The requested order is stated.", "answer_quote": "LIFO"}
    assert evaluation.validate_judgment(case, "**LIFO**", {"ratings": [rating]})["score"] == 2
