"""Review-note reuse and English lexical retrieval; no live generation calls."""
from threading import RLock
from types import SimpleNamespace

import pytest

from app.course_vocabulary import CourseVocabulary, alias_groups, chapter_notes, note_terms
from app.db import create_database
from app.knowledge_index import KnowledgeIndex
from app.lexical import query_terms
from app.models import Course, Document, Outline, RetrievalUnit


NOTES = """### 排序（Sorting）
#### **堆排序**（Heap Sort）
堆排序（Heap Sort）使用堆结构。
| 算法（Algorithm） | 稳定性（Stability） |
| --- | --- |
| 堆排序（Heap Sort） | 不稳定 |
虚构术语（Invented Concept）不应成为原文证据。
"""


class ForbiddenModel:
    def complete(self, *args, **kwargs):
        raise AssertionError("Note reuse must not call the conversation model")


@pytest.fixture
def vocabulary_store(tmp_path):
    engine, sessions = create_database(tmp_path)
    with sessions() as db:
        db.add_all([Course(id="c", name="Sorting"), Course(id="other", name="Another course")])
        db.flush()
        db.add(Document(id="d", course_id="c", filename="English.pptx", kind="lecture",
                        status="ready", file_hash="hash", path="English.pptx"))
        db.flush()
        text = "Sorting includes Heap Sort. Algorithm Stability is discussed."
        db.add(RetrievalUnit(id="u", course_id="c", document_id="d", chapter_id="one", unit_key="page1",
            position=0, title="Sorting", text=text, index_text=text, block_ids=[], locator={"slide": 1}))
        db.add(Outline(id="notes", course_id="c", version=0, status="completed",
            snapshot={"chapters": [{"id": "one", "title": "Sorting"}]},
            chapter_results={"one": {"markdown": NOTES}}, markdown="# Review notes\n## Sorting\n" + NOTES))
        db.commit()
    retrieval = SimpleNamespace(sessions=sessions, llm=ForbiddenModel(), store_name="chroma", lock=RLock())
    try:
        yield retrieval
    finally:
        engine.dispose()


def test_note_alias_and_table_dimension_retrieve_english_without_generation(vocabulary_store):
    retrieval = vocabulary_store
    concepts, facets, incomplete = CourseVocabulary(retrieval).read("c", ["one"])
    groups, semantic = query_terms("请解释堆排序", alias_groups(concepts))
    assert any("heap sort" in group and "堆排序" in group for group in groups)
    assert semantic == "请解释堆排序" and incomplete == 0
    heap = next(c for c in concepts if c["name"] == "Heap Sort")
    assert heap["parents"] == ["Sorting"] and heap["unit_ids"] == ["u"]
    assert [f["name"] for f in facets] == ["Stability"]
    assert all(c["name"] != "Invented Concept" for c in concepts)

    class OfflineEmbedder:
        def embed(self, *args, **kwargs):
            raise RuntimeError("Vectors unavailable")
    retrieval.embedder = OfflineEmbedder()
    retrieval.settings = SimpleNamespace(retrieval_min_score=-1)
    retrieval.source = lambda db, identity: {"source_id": "S" + identity, "course_id": "c", "document_id": "d",
        "kind": "lecture", "chapter_id": "one", "text": db.get(RetrievalUnit, identity).text}
    index = KnowledgeIndex(retrieval)
    index.ensure = lambda course_id: None
    for question in ("请解释堆排序", "稳定性"):
        result = index.candidates("c", question, chapter_ids=["one"])
        assert result and result[0]["lexical_score"] > 0 and "Heap Sort" in result[0]["text"]
    with retrieval.sessions() as db:
        assert db.get(RetrievalUnit, "u").text == "Sorting includes Heap Sort. Algorithm Stability is discussed."


def test_course_chapter_and_course_version_isolation(vocabulary_store):
    vocabulary = CourseVocabulary(vocabulary_store)
    assert vocabulary.read("other") == ([], [], 1)
    assert vocabulary.read("c", ["two"])[0] == []
    with vocabulary_store.sessions() as db:
        db.get(Course, "c").version += 1
        db.commit()
    assert vocabulary.read("c") == ([], [], 1)  # Old notes cannot rename newly changed sources.


def test_new_notes_take_effect_only_after_completion(vocabulary_store):
    vocabulary = CourseVocabulary(vocabulary_store)
    with vocabulary_store.sessions() as db:
        db.add(Outline(id="new-notes", course_id="c", version=0, status="running", snapshot={},
            chapter_results={"one": {"markdown": "### 堆排（Heap Sort）"}},
            created_at="9999-01-01T00:00:00"))
        db.commit()
    assert "堆排序" in next(c for c in vocabulary.read("c")[0] if c["name"] == "Heap Sort")["aliases"]
    with vocabulary_store.sessions() as db:
        db.get(Outline, "new-notes").status = "completed"
        db.commit()
    heap = next(c for c in vocabulary.read("c")[0] if c["name"] == "Heap Sort")
    assert "堆排" in heap["aliases"] and "堆排序" not in heap["aliases"]


def test_source_reindex_uses_live_ids_and_missing_original_terms_are_ignored(vocabulary_store):
    vocabulary = CourseVocabulary(vocabulary_store)
    assert vocabulary.read("c")[0]
    with vocabulary_store.sessions() as db:
        db.get(RetrievalUnit, "u").id = "new-u"
        db.commit()
    assert all(c["unit_ids"] == ["new-u"] for c in vocabulary.read("c")[0])
    with vocabulary_store.sessions() as db:
        unit = db.get(RetrievalUnit, "new-u")
        unit.title = "Unrelated"
        unit.text = unit.index_text = "Different content without the note terms."
        db.commit()
    assert vocabulary.read("c") == ([], [], 1)


def test_bilingual_symbols_abbreviations_and_code_are_handled_locally():
    entries = note_terms("""### 树（Trees）
#### **B+树**（B+tree）
AVL 树（AVL Tree）及二叉搜索树（Binary Search Tree, BST）。
`代码名（CodeName）`
```python
忽略（IgnoredTerm）
```
""")
    assert any(e["chinese"] == "B+树" and e["names"] == ("B+tree",) for e in entries)
    assert any(e["chinese"] == "AVL 树" for e in entries)
    assert any(e["names"] == ("Binary Search Tree", "BST") for e in entries)
    assert all(e["names"] not in (("CodeName",), ("IgnoredTerm",)) for e in entries)


def test_legacy_rendered_notes_require_unambiguous_chapter_titles():
    outline = SimpleNamespace(chapter_results={}, markdown="# Notes\n## Trees\n### 树（Trees）\n",
                              snapshot={"chapters": [{"id": "one", "title": "Trees"}]})
    assert "树（Trees）" in chapter_notes(outline)["one"]
    outline.snapshot["chapters"].append({"id": "two", "title": "Trees"})
    assert chapter_notes(outline) == {}
