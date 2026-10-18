"""Merged chapters keep every source file's topics instead of one lecture's budget."""
import json
import re

from app.models import Job, Outline
from app.outlines import OutlineService

MARKER = re.compile(r"FILE_[A-Z]_POINT")


def run_merged_chapter(app, course_id, llm, monkeypatch, files):
    chapter = {"id": "sorting", "title": "修改lec7-5 Sorting V", "position": 0, "kind": "knowledge"}
    documents, blocks = [], []
    for number, (filename, slides) in enumerate(files):
        document_id = f"document-{number}"
        documents.append({"id": document_id, "filename": filename, "kind": "lecture"})
        blocks += [{"source_id": f"{document_id}-{index}", "document_id": document_id, "chapter_id": chapter["id"],
                    "text": text, "locator": {"slide": index + 1}, "kind": "lecture", "filename": filename, "year": None}
                   for index, text in enumerate(slides)]
    calls = []

    def complete_markdown(messages):
        task = re.match(r"\[TASK:REVIEW_NOTES_(\w+)\]", messages[0]["content"])[1]
        payload = json.loads(messages[-1]["content"])
        calls.append((task, payload))
        markers = sorted({marker for value in payload.get("materials") or payload["drafts"]
                          for marker in MARKER.findall(value["text"])})
        return "### 排序方法\n\n要点 " + " ".join(markers)

    monkeypatch.setattr(llm, "complete_markdown", complete_markdown, raising=False)
    snapshot = {"course_name": "数据结构", "version": 0, "documents": documents, "blocks": blocks, "chapters": [chapter]}
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


def test_large_merged_chapter_writes_each_file_with_its_own_budget(app, course, llm, monkeypatch):
    # Upload order differs from lecture order; each file needs more than one input batch.
    files = [(f"修改lec7-{number} Sorting {name}.pptx",
              [f"{marker} 排序原文"] + ["排序知识点原文" * 180 for _ in range(6)])
             for number, name, marker in ((5, "V", "FILE_C_POINT"), (1, "I", "FILE_A_POINT"), (2, "II", "FILE_B_POINT"))]
    status, error, markdown, calls = run_merged_chapter(app, course["id"], llm, monkeypatch, files)

    assert status == "completed", error
    for marker in ("FILE_A_POINT", "FILE_B_POINT", "FILE_C_POINT"):
        assert marker in markdown
    assert markdown.index("FILE_A_POINT") < markdown.index("FILE_B_POINT") < markdown.index("FILE_C_POINT")
    final_parts = [payload.get("part") for task, payload in calls if task == "CHAPTER"]
    assert final_parts == ["修改lec7-1 Sorting I", "修改lec7-2 Sorting II", "修改lec7-5 Sorting V"]
    for _, payload in calls:
        texts = [value["text"] for value in payload.get("materials") or payload["drafts"]]
        assert len({marker for text in texts for marker in MARKER.findall(text)}) <= 1, \
            "A note call must not squeeze several merged files into one budget."


def test_small_merged_chapter_still_uses_one_combined_call(app, course, llm, monkeypatch):
    files = [("修改lec7-1 Sorting I.pptx", ["FILE_A_POINT 冒泡排序"]), ("修改lec7-2 Sorting II.pptx", ["FILE_B_POINT 归并排序"])]
    status, error, markdown, calls = run_merged_chapter(app, course["id"], llm, monkeypatch, files)

    assert status == "completed", error
    assert [task for task, _ in calls] == ["CHAPTER"]
    assert "part" not in calls[0][1]
    assert "FILE_A_POINT" in markdown and "FILE_B_POINT" in markdown
