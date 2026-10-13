from app.models import Block, Chapter, Course, Document


def test_course_block_references_are_scoped_metadata_and_preview_keeps_text(app, client, course):
    with app.state.services.sessions() as db:
        db.add(Course(id="other-course", name="Other course"))
        db.add(Chapter(id="chapter", course_id=course["id"], title="Chapter"))
        db.flush()
        for document_id, course_id, status, kind in [
            ("ready", course["id"], "ready", "lecture"),
            ("failed", course["id"], "failed", "lecture"),
            ("generated", course["id"], "ready", "generated"),
            ("other", "other-course", "ready", "lecture"),
        ]:
            db.add(Document(id=document_id, course_id=course_id, filename=document_id + ".txt",
                            kind=kind, status=status, file_hash=document_id, path=""))
        db.flush()
        for document_id in ["ready", "failed", "generated", "other"]:
            db.add(Block(id=document_id + "-block", document_id=document_id,
                         chapter_id="chapter" if document_id == "ready" else None,
                         order=1, text="Original lecture text", locator={"label": "Page 1"}))
        db.add(Block(id="unassigned", document_id="ready", chapter_id=None,
                     order=0, text="Unassigned original text", locator={"label": "Page 0"}))
        db.commit()

    response = client.get(f"/courses/{course['id']}/block-references")
    assert response.status_code == 200
    assert response.json() == [
        {"id": "unassigned", "document_id": "ready", "chapter_id": None},
        {"id": "ready-block", "document_id": "ready", "chapter_id": "chapter"},
    ]
    assert client.get("/courses/missing/block-references").status_code == 404

    preview = client.get("/documents/ready/blocks")
    assert preview.status_code == 200
    assert [block["text"] for block in preview.json()] == ["Unassigned original text", "Original lecture text"]
