"""Controlled migration checks; no real model inference or quality measurements."""
from dataclasses import replace

import pytest

from app.config import Settings
from app.lexical import query_terms
from app.models import AppMeta, Block, Chunk, Course, Document
from app.retrieval import EMBEDDING_RUNTIME, LocalEmbedder
from app.store import Services


class FixedEmbedder:
    tokenizer = None

    def __init__(self, dimension):
        self.dimension = dimension

    def embed(self, texts, query=False):
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]


@pytest.fixture
def legacy_index(tmp_path):
    settings = Settings(data_dir=tmp_path)
    legacy = Services(replace(settings, embedding_model="BAAI/bge-small-zh-v1.5"), embedder=FixedEmbedder(512))
    try:
        with legacy.sessions() as db:
            db.add(Course(id="course", name="Existing course"))
            db.flush()
            document = Document(id="document", course_id="course", filename="lecture.txt", kind="lecture",
                                status="ready", file_hash="hash", path=str(tmp_path / "lecture.txt"))
            db.add(document)
            db.flush()
            block = Block(id="block", document_id=document.id, order=0, text="Deadlock conditions",
                          locator={"page": 1})
            db.add(block)
            db.flush()
            chunk = Chunk(id="chunk", document_id=document.id, block_id=block.id, position=0, text=block.text)
            db.add(chunk)
            db.add(AppMeta(key="embedding_runtime", value="onnx-cls-fp32-v1"))
            db.commit()
            legacy.retrieval.index_document(document, [block], [chunk])
    finally:
        legacy.retrieval.close()
        legacy.engine.dispose()
    return settings


def test_migration_blocks_old_vectors_and_failure_keeps_published_identity(legacy_index, monkeypatch):
    service = Services(legacy_index)

    def unavailable_model(_self):
        raise ValueError("Model download unavailable")

    monkeypatch.setattr(LocalEmbedder, "_load", unavailable_model)
    try:
        assert service.retrieval.index_compatible is False
        with pytest.raises(ValueError, match="索引需要重建"):
            service.retrieval.search("course", "死锁")
        with pytest.raises(ValueError, match="索引需要重建"):
            service.retrieval.knowledge.catalog("course")
        with pytest.raises(ValueError):
            service.rebuild_index()
        with service.sessions() as db:
            assert db.get(AppMeta, "embedding_model").value == "BAAI/bge-small-zh-v1.5"
            assert db.get(AppMeta, "embedding_runtime").value == "onnx-cls-fp32-v1"
            assert db.get(AppMeta, "vector_store_name") is None
            assert db.get(AppMeta, "index_repair:course") is not None
            assert db.get(Block, "block").text == "Deadlock conditions"
            assert db.get(Chunk, "chunk").text == "Deadlock conditions"
        assert service.retrieval.index_compatible is False
        assert service.retrieval.collection.count() == 1
    finally:
        service.retrieval.close()
        service.engine.dispose()


def test_migration_publishes_new_model_and_generation_together(legacy_index):
    service = Services(legacy_index)
    service.retrieval.embedder = FixedEmbedder(768)
    try:
        previous_store = service.retrieval.store_dir
        result = service.rebuild_index()
        assert result["documents"] == 1 and result["chunks"] == 1
        assert result["knowledge_passages"] > 0
        assert service.retrieval.index_compatible is True
        assert service.retrieval.store_dir != previous_store and previous_store.is_dir()
        with service.sessions() as db:
            assert db.get(AppMeta, "embedding_model").value == legacy_index.embedding_model
            assert db.get(AppMeta, "embedding_runtime").value == EMBEDDING_RUNTIME
            assert db.get(AppMeta, "vector_store_name").value == service.retrieval.store_name
            assert db.get(AppMeta, "index_repair:course") is None
            assert db.get(Block, "block").text == "Deadlock conditions"
            assert db.get(Chunk, "chunk").text == "Deadlock conditions"
    finally:
        service.retrieval.close()
        service.engine.dispose()


def test_semantic_query_preserves_original_wording_and_case():
    question = "  请解释二叉搜索树 BinarySearchTree 的查找过程  "
    groups, semantic_query = query_terms(question)
    assert groups
    assert semantic_query == question.strip()
    assert "检索术语" not in semantic_query
