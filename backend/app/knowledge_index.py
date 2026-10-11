"""Incremental contextual-unit index over the existing parsed originals."""
import json
import logging
from collections import OrderedDict
from threading import RLock

from sqlalchemy import delete, select

from .lexical import lexical_score, query_terms
from .course_vocabulary import CourseVocabulary, alias_groups
from .models import AppMeta, Block, Course, Document, RetrievalUnit
from .retrieval_lexical import ranked_unit_ids
from .retrieval_units import UNIT_POLICY, build_units, document_signature

logger = logging.getLogger(__name__)


class KnowledgeIndex:
    def __init__(self, retrieval):
        self.r = retrieval
        self.build_lock = RLock()
        self.attempted = {}
        self.ensured = {}
        self.vocabulary = CourseVocabulary(retrieval)

    def index_document(self, document, blocks, force=False):
        if not getattr(self.r, "index_compatible", True):
            raise ValueError("检索模型已更换，索引需要重建，请点击“重新建立索引”。")
        if document.kind not in ("lecture", "note") or not blocks:
            return
        signature = document_signature(blocks) + f":{document.kind}:{document.year}"
        key = "knowledge_units:" + document.id
        with self.build_lock:
            generation = self.r.store_name
            with self.r.sessions() as db:
                marker = db.get(AppMeta, key)
                try:
                    state = json.loads(marker.value) if marker else {}
                except (ValueError, TypeError):
                    state = {}
                if not isinstance(state, dict):
                    state = {}
                current = (state.get("policy") == UNIT_POLICY and state.get("signature") == signature
                           and state.get("store") == generation)
                if current and (state.get("vectors") or self.attempted.get(document.id) == (signature, self.r.ready)) and not force:
                    return
            # A missing vector runtime must not discard readable source text.
            try:
                tokenizer = getattr(self.r.embedder, "tokenizer", None)
            except Exception:
                tokenizer = None
                logger.warning("Embedding tokenizer unavailable; keeping unit lexical retrieval", exc_info=True)
            units, passages = build_units(document, blocks, tokenizer)
            with self.r.sessions() as db:
                live = db.get(Document, document.id)
                if live is None or live.status not in ("ready", "processing"):
                    return
                db.execute(delete(RetrievalUnit).where(RetrievalUnit.document_id == document.id))
                db.add_all(units)
                db.flush()
                db.add_all(passages)
                state = {"policy": UNIT_POLICY, "signature": signature, "store": generation,
                         "vectors": False, "units": len(units), "passages": len(passages)}
                marker = db.get(AppMeta, key)
                if marker is None:
                    marker = AppMeta(key=key, value="")
                    db.add(marker)
                marker.value = json.dumps(state)
                db.commit()
            by_id = {unit.id: unit for unit in units}
            self.attempted[document.id] = (signature, self.r.ready)
            try:
                # Never query obsolete passages after a failed refresh.
                with self.r.lock:
                    if self.r.store_name != generation:
                        return  # A concurrent rebuild published a fresh store.
                    self.r.knowledge_collection.delete(where={"document_id": document.id})
                batches = []
                for offset in range(0, len(passages), 48):
                    group = passages[offset:offset + 48]
                    batches.append((group, self.r.embedder.embed([p.text for p in group])))
                with self.r.lock:
                    if self.r.store_name != generation:
                        return
                    for group, vectors in batches:
                        self.r.knowledge_collection.upsert(ids=[p.id for p in group], embeddings=vectors,
                            documents=[p.text for p in group], metadatas=[{
                                "unit_id": p.unit_id, "document_id": document.id, "course_id": document.course_id,
                                "chapter_id": by_id[p.unit_id].chapter_id or "", "kind": document.kind,
                                "year": document.year or 0,
                            } for p in group])
            except Exception:
                logger.warning("Contextual vectors unavailable; BM25/source units retained for %s", document.id, exc_info=True)
                return
            with self.r.sessions() as db:
                marker = db.get(AppMeta, key)
                if marker:
                    state["vectors"] = True
                    marker.value = json.dumps(state)
                    db.commit()

    def ensure(self, course_id):
        # Serialize automatic upgrades and ingestion. No reparsing, OCR or paid
        # generation runs here; this only derives indexes from saved Blocks.
        if not getattr(self.r, "index_compatible", True):
            raise ValueError("检索模型已更换，索引需要重建，请点击“重新建立索引”。")
        with self.build_lock:
            with self.r.sessions() as db:
                course = db.get(Course, course_id)
                if course is None:
                    return
                cache_key = (course.version, self.r.store_name, self.r.ready)
                if self.ensured.get(course_id) == cache_key:
                    return
                documents = list(db.scalars(select(Document).where(Document.course_id == course_id,
                    Document.status == "ready", Document.kind.in_(["lecture", "note"]))))
                snapshots = [(doc, list(db.scalars(select(Block).where(
                    Block.document_id == doc.id).order_by(Block.order)))) for doc in documents]
            for document, blocks in snapshots:
                self.index_document(document, blocks)
            self.ensured[course_id] = cache_key

    def candidates(self, course_id, query, chapter_id=None, year=None, limit=60, chapter_ids=None):
        with self.r.sessions() as db:
            if db.get(AppMeta, "index_repair:" + course_id):
                raise ValueError("课程索引需要重建，请点击“重新建立索引”。")
        self.ensure(course_id)
        chapters = list(dict.fromkeys(chapter_ids or ([chapter_id] if chapter_id else [])))
        concepts, facets, _ = self.vocabulary.read(course_id, chapters)
        limit = max(12, min(limit, 160))
        with self.r.sessions() as db:
            course = db.get(Course, course_id)
            if course is None:
                return []
            groups, semantic_query = query_terms(query, alias_groups([*concepts, *facets]))
            statement = select(RetrievalUnit).join(Document, RetrievalUnit.document_id == Document.id).where(
                RetrievalUnit.course_id == course_id, Document.status == "ready", Document.kind.in_(["lecture", "note"]))
            if db.scalar(select(RetrievalUnit.id).join(Document).where(RetrievalUnit.course_id == course_id,
                    Document.status == "ready", Document.kind.in_(["lecture", "note"])).limit(1)) is None:
                return []
            if chapters:
                statement = statement.where(RetrievalUnit.chapter_id.in_(chapters))
            if year is not None:
                statement = statement.where(Document.year == year)
            ranked = ranked_unit_ids(db, course_id, groups, chapter_id, year, limit * 2, chapters) if groups else []
            lexical = []
            if ranked is None:
                lexical = [(u.id, lexical_score(u.index_text, groups)) for u in db.scalars(statement)]
                lexical = sorted((item for item in lexical if item[1]), key=lambda item: item[1], reverse=True)[:limit]
            elif ranked:
                units = {u.id: u for u in db.scalars(statement.where(RetrievalUnit.id.in_(ranked)))}
                lexical = [(identity, lexical_score(units[identity].index_text, groups)) for identity in ranked
                           if identity in units and lexical_score(units[identity].index_text, groups)]
                lexical = lexical[:limit]
        clauses = [{"course_id": course_id}, {"kind": {"$in": ["lecture", "note"]}}]
        if chapters:
            clauses.append({"chapter_id": {"$in": chapters}})
        if year is not None:
            clauses.append({"year": year})
        semantic = {}
        try:
            vectors = self.r.embedder.embed([semantic_query], query=True)
            with self.r.lock:
                count = self.r.knowledge_collection.count()
                result = self.r.knowledge_collection.query(query_embeddings=vectors,
                    n_results=min(count, limit * 4), where={"$and": clauses}, include=["distances", "metadatas"]) if count else None
            if result:
                for metadata, distance in zip(result["metadatas"][0], result["distances"][0]):
                    score = 1 - float(distance)
                    if score >= self.r.settings.retrieval_min_score:
                        identity = metadata["unit_id"]
                        semantic[identity] = max(score, semantic.get(identity, -1))
        except Exception:
            if not lexical:
                raise ValueError("当前知识索引未匹配到原文，向量检索也不可用；请检查模型文件或在资料库重新建立索引。")
            logger.warning("Contextual vector search unavailable; using course BM25", exc_info=True)
        fused = {}
        lexical_scores = dict(lexical)
        for rank, (identity, _) in enumerate(lexical, 1):
            fused[identity] = 1 / (60 + rank)
        for rank, (identity, _) in enumerate(sorted(semantic.items(), key=lambda item: item[1], reverse=True), 1):
            fused[identity] = fused.get(identity, 0) + 1 / (60 + rank)
        with self.r.sessions() as db:
            sources = []
            valid_documents = set()
            for marker in db.scalars(select(AppMeta).where(AppMeta.key.like("knowledge_units:%"))):
                try:
                    state = json.loads(marker.value)
                    if isinstance(state, dict) and state.get("vectors") and state.get("store") == self.r.store_name:
                        valid_documents.add(marker.key.removeprefix("knowledge_units:"))
                except (ValueError, TypeError):
                    continue
            for identity in sorted(fused, key=lambda value: fused[value], reverse=True):
                source = self.r.source(db, identity)
                if (source and source["course_id"] == course_id and source["kind"] in ("lecture", "note")
                        and (not chapters or source.get("chapter_id") in chapters)
                        and (year is None or source.get("year") == year)):
                    if identity not in lexical_scores and source["document_id"] not in valid_documents:
                        continue
                    source.update(score=round(fused[identity] / (2 / 61), 4),
                        lexical_score=lexical_scores.get(identity), semantic_score=semantic.get(identity),
                        retrieval_query=query, retrieval_method="contextual_bm25_vector")
                    sources.append(source)
                    if len(sources) >= limit:
                        break
            return sources

    def catalog(self, course_id, document_ids=None, limit=240, chapter_ids=None):
        self.ensure(course_id)
        with self.r.sessions() as db:
            statement = select(RetrievalUnit.id, RetrievalUnit.title, RetrievalUnit.locator, Document.filename, RetrievalUnit.text).join(
                Document, RetrievalUnit.document_id == Document.id).where(
                RetrievalUnit.course_id == course_id, Document.status == "ready", Document.kind.in_(["lecture", "note"]))
            if document_ids:
                statement = statement.where(RetrievalUnit.document_id.in_(document_ids))
            if chapter_ids:
                statement = statement.where(RetrievalUnit.chapter_id.in_(chapter_ids))
            documents = OrderedDict()
            for identity, title, locator, filename, body in db.execute(statement.order_by(Document.filename, RetrievalUnit.position)):
                documents.setdefault(filename, []).append({"id": identity, "title": title, "filename": filename,
                    "locator": locator, "text": body})
            # A long early file must not hide all titles from later lectures.
            items = [pool[offset] for offset in range(max((len(pool) for pool in documents.values()), default=0))
                     for pool in documents.values() if offset < len(pool)]
            return items if limit is None else items[:limit]
