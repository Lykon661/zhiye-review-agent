"""Final prompt byte budgets preserve original map inputs and oversized merge points."""
import json

import pytest

from app.models import Job, Outline
from app.outlines import FIELDS, MAX_PROMPT_BYTES, OutlineService


def run_snapshot(app, course_id, texts, metadata_chars=500):
    chapter = {"id": "chapter", "title": "第一章 中文资料", "position": 0}
    source_blocks = [
        {"source_id": f"{number:032x}", "document_id": "document", "chapter_id": chapter["id"],
         "text": text, "locator": {"label": "定位" * metadata_chars + '"\\' * 50},
         "kind": "lecture", "filename": "中文文件" * 50 + ".docx", "year": None}
        for number, text in enumerate(texts, 1)
    ]
    snapshot = {"course_name": "中文课程", "version": 0, "documents": [],
                "blocks": source_blocks, "chapters": [chapter]}
    services = app.state.services
    with services.sessions() as db:
        outline = Outline(course_id=course_id, version=0, snapshot=snapshot)
        db.add(outline)
        db.flush()
        job = Job(outline_id=outline.id)
        db.add(job)
        db.flush()
        job_id, outline_id = job.id, outline.id
        db.commit()
    OutlineService(services).run(job_id)
    with services.sessions() as db:
        job, outline = db.get(Job, job_id), db.get(Outline, outline_id)
        return source_blocks, job.status, job.error, outline.chapter_results, outline.markdown


@pytest.mark.parametrize("metadata_chars", [500, 5000], ids=["all-long-chinese-inputs", "metadata-too-large"])
def test_map_budget_preserves_full_chinese_text_and_metadata(app, course, llm, metadata_chars):
    texts = ["中文知识点资料" * 1700 + "FIRST_FINAL_POINT", "另一个知识点" * 1500 + "SECOND_FINAL_POINT"]
    original, status, error, _, markdown = run_snapshot(app, course["id"], texts, metadata_chars)

    if metadata_chars == 5000:
        assert status == "failed"
        assert "元数据" in error and "预算" in error
        assert not llm.complete_calls, "Oversized metadata must fail explicitly before sending any prompt."
        assert not markdown
        return

    assert status == "completed", error
    reconstructed = {block["source_id"]: [] for block in original}
    metadata = {block["source_id"]: {key: value for key, value in block.items() if key != "text"} for block in original}
    for messages in llm.complete_calls:
        assert len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) <= MAX_PROMPT_BYTES
        if "[TASK:OUTLINE_MAP]" not in messages[0]["content"]:
            continue
        for part in json.loads(messages[-1]["content"])["blocks"]:
            reconstructed[part["source_id"]].append(part["text"])
            assert {key: value for key, value in part.items() if key != "text"} == metadata[part["source_id"]]
    assert {source_id: "".join(parts) for source_id, parts in reconstructed.items()} == {
        block["source_id"]: block["text"] for block in original
    }
    assert "FIRST_FINAL_POINT" in markdown and "SECOND_FINAL_POINT" in markdown


def test_oversized_partials_merge_locally_without_losing_points_or_sources(app, course, llm, monkeypatch):
    expected_points = []

    def oversized_map(messages, json_mode=False):
        llm.complete_calls.append(messages)
        assert len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) <= MAX_PROMPT_BYTES
        assert "[TASK:OUTLINE_MAP]" in messages[0]["content"], "Oversized partials must never be sent for LLM merging."
        source_ids = list(dict.fromkeys(block["source_id"] for block in json.loads(messages[-1]["content"])["blocks"]))
        unique = {"text": "长知识点" * 1000 + f"FINAL_POINT_{len(expected_points)}", "source_ids": source_ids}
        expected_points.append(unique)
        shared = {"text": "共同知识点", "source_ids": source_ids}
        result = {field: [] for field in FIELDS}
        result["core_points"] = [shared, unique]
        result["definitions"] = [shared]
        return json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr(llm, "complete", oversized_map)
    original, status, error, results, markdown = run_snapshot(app, course["id"], ["原文资料" * 2000 for _ in range(4)])

    assert status == "completed", error
    assert len(expected_points) > 3, "Exercise more than one hierarchy level."
    result = results["chapter"]
    assert result["core_points"][1:] == expected_points
    all_sources = [block["source_id"] for block in original]
    assert result["core_points"][0] == {"text": "共同知识点", "source_ids": all_sources}
    assert result["definitions"] == [{"text": "共同知识点", "source_ids": all_sources}]
    assert expected_points[-1]["text"] in markdown
    assert all(f"[S{source_id}]" in markdown for source_id in expected_points[-1]["source_ids"])
