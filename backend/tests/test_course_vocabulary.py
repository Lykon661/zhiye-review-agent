"""Review-note reuse and English lexical retrieval; no live generation calls."""
from threading import RLock
from types import SimpleNamespace

import pytest

from app.course_vocabulary import VOCABULARY_CACHE_KEY, CourseVocabulary, alias_groups, chapter_notes, note_terms
from app.db import create_database
from app.knowledge_index import KnowledgeIndex
from app.lexical import query_terms
from app.models import AppMeta, Course, Document, Job, Outline, RetrievalUnit
from app.outlines import OutlineService


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
        # Production reindexing publishes a signature with its refreshed units.
        db.add(AppMeta(key="knowledge_units:d", value='{"signature":"reindexed"}'))
        db.commit()
    assert all(c["unit_ids"] == ["new-u"] for c in vocabulary.read("c")[0])
    with vocabulary_store.sessions() as db:
        unit = db.get(RetrievalUnit, "new-u")
        unit.title = "Unrelated"
        unit.text = unit.index_text = "Different content without the note terms."
        db.get(AppMeta, "knowledge_units:d").value = '{"signature":"changed-source"}'
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


def test_terms_inside_sentences_start_after_the_leading_clause():
    entries = note_terms("""### 动态等价问题与不相交集（Disjoint Sets）
### 各类探测（Probing）方法与聚集（Clustering）
树要么为空，要么由根（root）构成；边连接父节点（parent）和子节点（child）。
实现简单，但容易形成主聚集（Primary Clustering）。双向链表（doubly linked list）可计算前缀和（prefix sum）。
""")
    chinese = {e["names"][0]: e["chinese"] for e in entries}
    assert chinese["Clustering"] == "聚集" and chinese["Primary Clustering"] == "主聚集"
    assert chinese["root"] == "根" and chinese["parent"] == "父节点" and chinese["child"] == "子节点"
    # A heading names its subject, and words that merely contain 向 or end in 和 stay whole.
    assert chinese["Disjoint Sets"] == "动态等价问题与不相交集" and chinese["Probing"] == "各类探测"
    assert chinese["doubly linked list"] == "双向链表" and chinese["prefix sum"] == "可计算前缀和"


def test_names_written_across_most_chapters_are_marked_as_ordinary(vocabulary_store):
    with vocabulary_store.sessions() as db:
        outline = db.get(Outline, "notes")
        outline.chapter_results = {"one": {"markdown": NOTES + "查找（Find）先比较键。\n"},
                                   "two": {"markdown": "顺序查找要逐项比较。"}, "three": {"markdown": "二分查找。"}}
        db.get(RetrievalUnit, "u").text += " Find compares keys."
        db.commit()
    concepts, _, _ = CourseVocabulary(vocabulary_store).read("c")
    find = next(c for c in concepts if c["name"] == "Find")
    heap = next(c for c in concepts if c["name"] == "Heap Sort")
    assert "查找" in find["aliases"] and find["common"] == ["查找"] and heap["common"] == []


def test_legacy_rendered_notes_require_unambiguous_chapter_titles():
    outline = SimpleNamespace(chapter_results={}, markdown="# Notes\n## Trees\n### 树（Trees）\n",
                              snapshot={"chapters": [{"id": "one", "title": "Trees"}]})
    assert "树（Trees）" in chapter_notes(outline)["one"]
    outline.snapshot["chapters"].append({"id": "two", "title": "Trees"})
    assert chapter_notes(outline) == {}


def test_prepared_vocabulary_survives_restart_and_preserves_chapter_scope(vocabulary_store, monkeypatch):
    retrieval = vocabulary_store
    with retrieval.sessions() as db:
        outline = db.get(Outline, "notes")
        outline.status = "running"
        outline.chapter_results = {**outline.chapter_results, "two": {"markdown": "### 堆排（Heap Sort）"}}
        db.add(RetrievalUnit(id="u2", course_id="c", document_id="d", chapter_id="two", unit_key="page2",
            position=1, title="Heap Sort", text="Heap Sort uses a heap.", index_text="Heap Sort uses a heap.",
            block_ids=[], locator={"slide": 2}))
        db.commit()
    vocabulary = CourseVocabulary(retrieval)
    vocabulary.prepare("notes")
    assert vocabulary.read("c") == ([], [], 1)
    with retrieval.sessions() as db:
        outline = db.get(Outline, "notes")
        assert VOCABULARY_CACHE_KEY in outline.chapter_results
        outline.status = "completed"
        db.commit()

    def no_source_scan(*args, **kwargs):
        raise AssertionError("Prepared notes must not rescan source bodies")

    # A fresh service instance must use the durable cache, including empty scopes.
    monkeypatch.setattr(CourseVocabulary, "_build", no_source_scan)
    restarted = CourseVocabulary(retrieval)
    one, _, _ = restarted.read("c", ["one"])
    two, _, _ = restarted.read("c", ["two"])
    all_terms, _, _ = restarted.read("c")
    heap_one = next(c for c in one if c["name"] == "Heap Sort")
    heap_two = next(c for c in two if c["name"] == "Heap Sort")
    heap_all = next(c for c in all_terms if c["name"] == "Heap Sort")
    assert "堆排序" in heap_one["aliases"] and "堆排" not in heap_one["aliases"]
    assert "堆排" in heap_two["aliases"] and "堆排序" not in heap_two["aliases"]
    assert heap_one["unit_ids"] == ["u"] and heap_two["unit_ids"] == ["u2"]
    assert heap_all["unit_ids"] == ["u", "u2"]
    assert restarted.read("c", ["missing"]) == ([], [], 0)


def test_vocabulary_cache_refreshes_after_index_generation_and_source_status_changes(vocabulary_store):
    retrieval = vocabulary_store
    vocabulary = CourseVocabulary(retrieval)
    assert vocabulary.read("c")[0]
    with retrieval.sessions() as db:
        db.get(RetrievalUnit, "u").id = "rebuilt-u"
        db.commit()
    retrieval.store_name = "chroma-rebuilt"
    assert all(c["unit_ids"] == ["rebuilt-u"] for c in vocabulary.read("c")[0])
    with retrieval.sessions() as db:
        db.get(Document, "d").status = "failed"
        db.commit()
    assert vocabulary.read("c")[0] == []


def test_note_generation_prepares_vocabulary_before_completion(vocabulary_store, monkeypatch):
    retrieval = vocabulary_store
    vocabulary = CourseVocabulary(retrieval)
    retrieval.knowledge = SimpleNamespace(vocabulary=vocabulary)
    services = SimpleNamespace(sessions=retrieval.sessions, lock=RLock(), retrieval=retrieval)
    with retrieval.sessions() as db:
        outline = db.get(Outline, "notes")
        outline.status, outline.markdown, outline.chapter_results = "queued", "", {}
        outline.snapshot = {"course_name": "Sorting", "version": 0,
            "documents": [{"id": "d", "filename": "English.pptx", "kind": "lecture"}],
            "blocks": [{"source_id": "b", "document_id": "d", "chapter_id": "one", "kind": "lecture",
                        "text": "Sorting includes Heap Sort."}],
            "chapters": [{"id": "one", "title": "Sorting", "position": 0, "kind": "knowledge"}]}
        db.add(Job(id="job", outline_id=outline.id))
        db.commit()
    service = OutlineService(services)
    monkeypatch.setattr(service, "_chapter", lambda *args: NOTES)
    monkeypatch.setattr(service, "_guidance", lambda *args: {"text": "", "block_ids": [], "document_ids": []})
    original_prepare = vocabulary.prepare
    prepared = []

    def prepare_before_completion(outline_id):
        with retrieval.sessions() as db:
            assert db.get(Outline, outline_id).status == "running"
            assert db.get(Job, "job").progress == 99
        original_prepare(outline_id)
        prepared.append(outline_id)

    monkeypatch.setattr(vocabulary, "prepare", prepare_before_completion)
    service.run("job")
    with retrieval.sessions() as db:
        assert db.get(Job, "job").status == "completed"
        assert db.get(Outline, "notes").status == "completed"
        assert VOCABULARY_CACHE_KEY in db.get(Outline, "notes").chapter_results
    assert prepared == ["notes"]
    assert any(c["name"] == "Heap Sort" for c in vocabulary.read("c")[0])
