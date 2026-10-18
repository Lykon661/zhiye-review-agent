"""Review-note prompts stay within byte budgets and MAP→MERGE→CHAPTER keeps every topic."""
import json
import re

from app.models import Job, Outline
from app.outlines import (BATCH_PROMPT_BYTES, MAX_PROMPT_BYTES, OutlineService, material_units,
                          outline_messages, packet_batches, prompt_bytes)

MARKER = re.compile(r"POINT_\d\d")
CHAPTER = {"id": "chapter", "title": "第一章 中文资料", "position": 0}


def long_texts():
    # Each ordinary paragraph needs a MAP batch of its own; the last one is larger than any batch.
    return [f"POINT_{number:02d} " + "原文资料" * 600 for number in range(8)] + ["POINT_08 " + "超长原文" * 2500]


def source_blocks(texts):
    return [{"source_id": f"block-{number}", "document_id": "document", "chapter_id": CHAPTER["id"],
             "text": text, "locator": {"paragraph": number}, "kind": "lecture",
             "filename": "lecture.docx", "year": None} for number, text in enumerate(texts)]


def run_notes(app, course_id, llm, monkeypatch, texts, chapter=CHAPTER):
    calls = []

    def complete_markdown(messages):
        task = re.match(r"\[TASK:REVIEW_NOTES_(\w+)\]", messages[0]["content"])[1]
        payload = json.loads(messages[-1]["content"])
        calls.append((task, payload, prompt_bytes(messages)))
        markers = " ".join(sorted({marker for value in payload.get("materials") or payload["drafts"]
                                   for marker in MARKER.findall(value["text"])}))
        # MAP drafts are long enough that the final edit needs a MERGE round first.
        padding = "\n\n" + "阶段整理" * 330 if task == "MAP" else ""
        return f"### 中文资料要点\n\n要点 {markers}{padding}"

    monkeypatch.setattr(llm, "complete_markdown", complete_markdown)
    snapshot = {"course_name": "中文课程", "version": 0,
                "documents": [{"id": "document", "filename": "lecture.docx", "kind": "lecture"}],
                "blocks": source_blocks(texts), "chapters": [chapter]}
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
        return job.status, job.error, outline.markdown, calls


def test_packet_batches_split_only_oversized_text_and_keep_every_character():
    values = material_units(source_blocks(long_texts()))
    batches = packet_batches(CHAPTER, values)

    assert all(prompt_bytes(outline_messages("MAP", CHAPTER, "materials", batch)) <= BATCH_PROMPT_BYTES
               for batch in batches)
    pieces = [piece for batch in batches for piece in batch]
    assert "".join(piece["text"] for piece in pieces) == "".join(value["text"] for value in values)
    assert [piece["text"] for piece in pieces[:8]] == [value["text"] for value in values[:8]], \
        "A paragraph that fits one batch must not be split."
    assert len(pieces) > len(values), "The oversized paragraph must be split across batches."
    assert all(set(piece) == {"kind", "text"} for piece in pieces), "Prompts carry no IDs, filenames or locators."


def test_map_merge_chapter_reduction_keeps_every_topic_within_budget(app, course, llm, monkeypatch):
    texts = long_texts()
    status, error, markdown, calls = run_notes(app, course["id"], llm, monkeypatch, texts)

    assert status == "completed", error
    assert all(size <= MAX_PROMPT_BYTES for _, _, size in calls)
    tasks = [task for task, _, _ in calls]
    assert set(tasks) == {"MAP", "MERGE", "CHAPTER"}
    assert tasks.count("CHAPTER") == 1 and tasks[-1] == "CHAPTER"
    assert any(len(payload["drafts"]) > 1 for task, payload, _ in calls if task == "MERGE"), \
        "MERGE must combine drafts instead of passing them through one by one."
    pieces = [value["text"] for task, payload, _ in calls if task == "MAP" for value in payload["materials"]]
    assert sum(map(len, pieces)) == sum(map(len, texts)), "MAP reads every character exactly once."
    for number in range(9):
        assert f"POINT_{number:02d}" in markdown


def test_oversized_chapter_title_fails_before_any_model_call(app, course, llm, monkeypatch):
    chapter = {**CHAPTER, "title": "很长的章节名称" * 1000}
    status, error, markdown, calls = run_notes(app, course["id"], llm, monkeypatch, ["POINT_00 原文"], chapter)

    assert status == "failed"
    assert "章节名称" in error and "预算" in error
    assert not calls, "An oversized prompt must fail explicitly before sending any request."
    assert not markdown
