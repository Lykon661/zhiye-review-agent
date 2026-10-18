"""Source/index/coverage contracts. Controlled rankings do not measure LLM quality."""
from copy import deepcopy
import json
from types import SimpleNamespace
from threading import RLock

import pytest
from sqlalchemy import delete, select

from app.db import create_database
from app.knowledge_retrieval import KnowledgeRetrieval
from app.knowledge_index import KnowledgeIndex
from app.lexical import query_terms
from app.model_config import ModelConfig
from app.models import Block, Chunk, Course, Document, RetrievalPassage, RetrievalUnit
from app.prompt_budget import InputBudget
from app.retrieval_lexical import ranked_unit_ids
from app.retrieval_ranking import KnowledgeReranker
from app.retrieval_units import build_units
from app.review_space import ReviewSpaceService


def block(identity, order, text, slide=1, chapter=None):
    return Block(id=identity, document_id="d", order=order, text=text, heading=None,
                 chapter_id=chapter, block_type="text", locator={"slide": slide, "shape": order + 1})


def document(identity="d", course="c"):
    return Document(id=identity, course_id=course, filename="lec7-5.pptx", kind="lecture", year=None,
                    status="ready", file_hash=identity, path=identity)


def config_store(window=32768, output=8192):
    return SimpleNamespace(snapshot=lambda: ModelConfig(context_window=window, max_tokens=output))


def source(identity, text="Heap Sort\n\nWorst-case time O(n log n)."):
    return {"source_id": "S" + identity, "chunk_id": identity, "retrieval_unit_id": identity,
            "filename": "lec7-5.pptx", "kind": "lecture", "title": "Heap Sort", "text": text,
            "locator": {"slide": 1}, "score": 0.8, "query_topics": ["堆排序"]}


def test_page_units_attach_custom_title_to_every_body_passage_without_crossing_pages():
    blocks = [block("title", 0, "Heap Sort"), block("body", 1, "Build a heap.\n" + "Sift down. " * 100),
              block("other", 2, "Quick Sort", slide=2)]
    originals = deepcopy([(b.text, b.locator) for b in blocks])
    units, passages = build_units(document(), blocks)
    assert len(units) == 2
    assert units[0].title == "Heap Sort"
    assert units[0].block_ids == ["title", "body"]
    assert "Build a heap." in units[0].text and "Quick Sort" not in units[0].text
    assert units[0].locator["slide"] == 1 and "shape" not in units[0].locator
    own = [p for p in passages if p.unit_id == units[0].id]
    assert len(own) > 1 and all("标题：Heap Sort" in p.text for p in own)
    assert all(len(p.text) <= 470 for p in own)  # Character fallback bound.
    assert [(b.text, b.locator) for b in blocks] == originals


def test_repeated_algorithm_titles_are_retained_and_chapter_boundaries_are_preserved():
    blocks = []
    for slide in range(1, 9):
        blocks.extend([block(f"t{slide}", len(blocks), "Heap Sort", slide),
                       block(f"f{slide}", len(blocks) + 1, "Copyright Example University", slide),
                       block(f"b{slide}", len(blocks) + 2, f"Heap operation {slide}", slide)])
    units, _ = build_units(document(), blocks)
    assert len(units) == 8 and all(u.title == "Heap Sort" for u in units)
    assert all("Copyright" not in u.text for u in units)
    separated, _ = build_units(document(), [block("a", 0, "Heap Sort", chapter="one"),
                                           block("b", 1, "Quick Sort", chapter="two")])
    assert len(separated) == 2 and {u.chapter_id for u in separated} == {"one", "two"}


def test_bm25_scoping_updates_and_document_cascade(tmp_path):
    engine, sessions = create_database(tmp_path)
    try:
        with sessions() as db:
            db.add_all([Course(id="c", name="one"), Course(id="other", name="two")])
            db.flush()
            docs = [document(), document("other-doc", "other")]
            db.add_all(docs)
            db.flush()
            for doc in docs:
                units, passages = build_units(doc, [block(doc.id + "-block", 0, "Heap Sort")])
                db.add_all(units)
                db.flush()
                db.add_all(passages)
            db.commit()
            ids = ranked_unit_ids(db, "c", [("heap sort",)])
            if ids is None:
                pytest.skip("SQLite runtime has no FTS5")
            assert len(ids) == 1
            own = db.get(RetrievalUnit, ids[0])
            own.index_text = "Unrelated content"
            db.commit()
            assert ranked_unit_ids(db, "c", [("heap sort",)]) == []
            db.execute(delete(Document).where(Document.id == "d"))
            db.commit()
            assert db.scalar(select(RetrievalUnit.id).where(RetrievalUnit.document_id == "d")) is None
            assert db.scalar(select(RetrievalPassage.id).where(RetrievalPassage.document_id == "d")) is None
            assert len(ranked_unit_ids(db, "other", [("heap sort",)])) == 1
    finally:
        engine.dispose()


def test_existing_parsed_blocks_upgrade_once_without_replacing_original_chunks(tmp_path):
    engine, sessions = create_database(tmp_path)
    class Collection:
        def __init__(self):
            self.records = {}
        def delete(self, where):
            self.records = {k: v for k, v in self.records.items() if v["document_id"] != where["document_id"]}
        def upsert(self, ids, embeddings, documents, metadatas):
            self.records.update(zip(ids, metadatas))
    class Embedder:
        tokenizer = None
        def __init__(self):
            self.calls = 0
        def embed(self, texts):
            self.calls += 1
            return [[1.0, 0.0] for _ in texts]
    try:
        with sessions() as db:
            db.add(Course(id="c", name="Sorting"))
            db.flush()
            db.add(document())
            db.flush()
            db.add_all([block("title", 0, "Heap Sort"), block("body", 1, "Build a heap and sift down.")])
            db.flush()
            db.add(Chunk(id="old-chunk", document_id="d", block_id="body", position=0, text="Build a heap"))
            db.commit()
        embedder = Embedder()
        retrieval = SimpleNamespace(sessions=sessions, store_name="chroma", ready=True, lock=RLock(),
                                    embedder=embedder, knowledge_collection=Collection())
        index = KnowledgeIndex(retrieval)
        index.ensure("c")
        first_calls = embedder.calls
        index.ensure("c")
        assert first_calls > 0 and embedder.calls == first_calls
        with sessions() as db:
            assert db.get(Chunk, "old-chunk").text == "Build a heap"
            assert db.get(Block, "body").text == "Build a heap and sift down."
            unit = db.scalar(select(RetrievalUnit).where(RetrievalUnit.document_id == "d"))
            assert unit.title == "Heap Sort" and "sift down" in unit.text
        catalog = index.catalog("c")
        assert catalog[0]["text"] == unit.text
    finally:
        engine.dispose()


class RankingLLM:
    configured = True

    def __init__(self, invalid=False):
        self.calls, self.invalid = [], invalid

    def complete(self, messages, json_mode=False):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        rankings = [{"id": c["id"], "score": 80, "topics": ["堆排序"]} for c in payload["candidates"]]
        if self.invalid:
            rankings[0]["id"] = "not-supplied"
        return json.dumps({"rankings": rankings, "coverage": [
            {"id": "C0", "topic": "堆排序", "facet": "时间复杂度", "quote": "Worst-case time O(n log n)."},
            {"id": "C0", "topic": "堆排序", "facet": "稳定性", "quote": "This algorithm is always stable."},
        ]}, ensure_ascii=False)


def test_reranking_batches_assess_all_candidates_and_reject_fabricated_quotes():
    llm = RankingLLM()
    sources = [source(str(i)) for i in range(73)]
    result, report = KnowledgeReranker(llm, config_store()).rank("对比堆排序", sources,
        ["堆排序"], ["时间复杂度", "稳定性"])
    assert len(result) == report["assessed_count"] == 73
    assert len(llm.calls) > 1 and all(len(p["candidates"]) <= 24 for p in llm.calls)
    assert report["assessed"] is True and report["omitted_candidates"] == 0
    assert report["coverage"] and all(e["facet"] == "时间复杂度" for e in report["coverage"])
    assert all(e["quote"] in next(s["text"] for s in sources if s["source_id"] == e["source_id"])
               for e in report["coverage"])


def test_invalid_ranking_preserves_original_evidence_and_marks_fallback():
    original = source("one")
    result, report = KnowledgeReranker(RankingLLM(invalid=True), config_store()).rank(
        "堆排序", [original], ["堆排序"], ["时间复杂度"])
    assert result[0]["text"] == original["text"] and not result[0]["rerank_assessed"]
    assert report["method"] == "hybrid_fallback" and report["coverage"] == []


def test_quote_supported_source_counts_even_if_model_omits_topic_assignment():
    material = {**source("one"), "matched_topics": [], "rerank_assessed": True}
    rows = KnowledgeRetrieval._coverage([{"name": "堆排序"}], ["时间复杂度", "稳定性"], [material],
        {"assessed": True, "coverage": [{"topic": "堆排序", "facet": "时间复杂度", "source_id": "Sone"}]})
    assert rows[0]["has_material"] and rows[0]["missing_facets"] == ["稳定性"]


def scope_service(concepts, facets=(), incomplete=0):
    vocabulary = SimpleNamespace(read=lambda *args: (concepts, list(facets), incomplete))
    return KnowledgeRetrieval(SimpleNamespace(retrieval=SimpleNamespace(knowledge=SimpleNamespace(vocabulary=vocabulary))))


def concept(name, aliases, parents=(), identity="p1"):
    return {"name": name, "aliases": aliases, "parents": list(parents), "unit_ids": [identity]}


def test_named_comparison_uses_course_aliases_without_model_or_unrelated_seeds():
    concepts = [concept("Heap Sort", ["堆排", "堆排序"]), concept("Quick Sort", ["快排", "快速排序"]),
                concept("Insertion Sort", ["插入排序"])]
    service = scope_service(concepts, [concept("Stability", ["稳定性"])])
    topics, facets, omitted = service._scope("c", "排序算法", "只比较快排和堆排的稳定性", [], ["插入排序"], [])
    assert {t["name"] for t in topics} == {"Heap Sort", "Quick Sort"}
    assert facets == ["Stability"] and omitted == 0


def test_prompt_coverage_does_not_claim_quotes_omitted_from_actual_input():
    store = config_store(8192, 2048)
    service = ReviewSpaceService(SimpleNamespace(s=SimpleNamespace(model_config=store)))
    original = source("one", "a" * 30000 + " Verified stable statement.")
    report = {"facets": ["稳定性"], "coverage": [{"topic": "堆排序", "source_ids": ["Sone"],
        "covered_facets": ["稳定性"], "missing_facets": [], "assessed": True}],
        "rerank": {"coverage": [{"topic": "堆排序", "facet": "稳定性", "source_id": "Sone",
                                 "quote": "Verified stable statement."}]}}
    payload = {"topic": "堆排序", "materials": []}
    used = service._pack_materials("Use supplied originals.", payload, [original], report)
    assert used and used[0]["excerpted"] and "Verified stable statement." not in used[0]["text"]
    assert report["supplied_coverage"][0]["missing_facets"] == ["稳定性"]
    assert payload["coverage_gaps"] and original["text"].endswith("Verified stable statement.")
    budget = InputBudget(store.snapshot())
    assert budget.fits("Use supplied originals.", payload)


def test_configured_window_expands_input_budget_with_output_reserved():
    small = InputBudget(ModelConfig(context_window=8192, max_tokens=2048))
    large = InputBudget(ModelConfig(context_window=32768, max_tokens=8192))
    assert 0 < small.limit < large.limit < 32768 - 8192
    payload = {"materials": "课程知识" * 2000}
    assert not small.fits("Answer from originals.", payload)
    assert large.fits("Answer from originals.", payload)


def test_scope_expands_course_relations_without_model_and_honors_exclusions():
    concepts = [concept("Metabolism", ["药物代谢", "代谢反应"]),
                concept("Oxidation", ["氧化反应"], ["Metabolism"]),
                concept("Reduction", ["还原反应"], ["Metabolism"])]
    service = scope_service(concepts)
    topics, _, omitted = service._scope("c", "药物代谢", "列举所有代谢反应，不包括还原反应", [], [], ["one"])
    assert {t["name"] for t in topics} == {"Metabolism", "Oxidation"} and omitted > 0


def test_scope_does_not_infer_taxonomy_from_shared_words():
    concepts = [concept("Sorting", ["排序"]), concept("Quick Sort", ["快排"], ["Sorting"]),
                concept("Topological Sort", ["拓扑排序"])]
    topics, _, _ = scope_service(concepts)._scope("c", "排序", "列举各种排序", [], [], [])
    assert {t["name"] for t in topics} == {"Sorting", "Quick Sort"}


def test_missing_vocabulary_preserves_query_and_discloses_incomplete_inventory():
    topics, _, omitted = scope_service([], incomplete=1)._scope("c", "树", "列举所有树类型", [], [], ["one"])
    assert topics == [{"name": "树", "queries": ["树"], "unit_ids": []}] and omitted > 0


def test_failed_scope_is_disclosed_to_the_explanation():
    service = ReviewSpaceService(SimpleNamespace(s=SimpleNamespace(model_config=config_store())))
    payload = {"topic": "药物代谢", "materials": []}
    service._pack_materials("Use supplied originals.", payload, [],
                            {"omitted_catalog_titles": 1, "coverage": []})
    assert payload["scope_incomplete"] is True


def test_literal_terms_preserve_symbols_and_numeric_examples_without_translation():
    question = "哈希碰撞：18 和 35 对表长 17 取模"
    groups, semantic_query = query_terms(question)
    assert {("18",), ("35",), ("17",)} <= set(groups)
    assert not any("collision" in group for group in groups)
    assert semantic_query == question
    assert query_terms("B+tree、2-3 tree")[0][-2:] == [("b+tree",), ("2-3 tree",)]
    assert query_terms("和弦")[0] == [("和弦",)]
    assert query_terms("foo_bar and heap_sort")[0] == [("foo_bar",), ("heap_sort",)]
