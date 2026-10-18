"""Failure windows across SQLite, Chroma and the upload filesystem."""
from pathlib import Path
from threading import Lock

import pytest

from app.models import AppMeta, Document
from test_app import session, upload


def test_document_sql_failure_keeps_records_and_requires_index_repair(client, course, app, monkeypatch):
    document = upload(client, course["id"])
    services = app.state.services

    def fail_directory(*args, **kwargs):
        raise RuntimeError("directory publication interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(services, "draft_chapters", fail_directory)
        result = client.delete(f"/documents/{document['id']}")
    assert result.status_code == 503
    with services.sessions() as db:
        assert db.get(Document, document["id"]).status == "ready"
        assert db.get(AppMeta, "index_repair:" + course["id"]).value == "rebuild_required"
    assert course["id"] in client.get("/health").json()["index_repair_courses"]
    assert client.get(f"/courses/{course['id']}/search", params={"query": "RAG"}).status_code == 503
    repaired = client.post(f"/courses/{course['id']}/index/repair")
    assert repaired.status_code in (200, 202), repaired.text
    sources = client.get(f"/courses/{course['id']}/search", params={"query": "RAG"}).json()["sources"]
    assert any(item["document_id"] == document["id"] for item in sources)


@pytest.mark.parametrize("target", ["document", "course"])
def test_partial_vector_deletion_is_marked_for_repair(client, course, app, monkeypatch, target):
    document = upload(client, course["id"])
    retrieval = app.state.services.retrieval
    method = "delete_document" if target == "document" else "delete_course"
    original = getattr(retrieval, method)

    def delete_then_fail(identity):
        original(identity)
        raise RuntimeError("vector persistence interrupted")

    monkeypatch.setattr(retrieval, method, delete_then_fail)
    url = f"/documents/{document['id']}" if target == "document" else f"/courses/{course['id']}"
    assert client.delete(url).status_code == 503
    with app.state.services.sessions() as db:
        assert db.get(Document, document["id"]) is not None
        assert db.get(AppMeta, "index_repair:" + course["id"]) is not None


@pytest.mark.parametrize("target", ["document", "course"])
def test_locked_upload_cleanup_does_not_report_committed_deletion_as_failure(client, course, app, monkeypatch, target):
    document = upload(client, course["id"])
    with app.state.services.sessions() as db:
        path = Path(db.get(Document, document["id"]).path)
    original = Path.unlink

    def locked_file(value, *args, **kwargs):
        if value == path:
            raise PermissionError("upload is open in another Windows process")
        return original(value, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked_file)
        url = f"/documents/{document['id']}" if target == "document" else f"/courses/{course['id']}"
        assert client.delete(url).status_code == 204
    with app.state.services.sessions() as db:
        assert db.get(Document, document["id"]) is None
    assert path.exists(), "Physical cleanup failure retains a file, not its deleted business record."


def test_document_delete_rejects_active_course_chat(client, course, app):
    document = upload(client, course["id"])
    identity = session(client, course["id"])
    lock = app.state.services.chat_locks.setdefault(identity, Lock())
    lock.acquire()
    try:
        assert client.delete(f"/documents/{document['id']}").status_code == 409
        with app.state.services.sessions() as db:
            assert db.get(Document, document["id"]) is not None
    finally:
        lock.release()
