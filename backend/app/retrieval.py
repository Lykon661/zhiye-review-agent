import os
import json
import logging
from pathlib import Path
import re
from threading import RLock

from sqlalchemy import select

from .config import ROOT, Settings
from .models import AppMeta, Block, Chunk, Course, Document, Outline
from .lexical import query_terms, lexical_score

logger = logging.getLogger(__name__)
HNSW_FILES = {"header.bin", "data_level0.bin", "length.bin", "link_lists.bin"}
ONNX_BGE_REPO = "Xenova/bge-small-zh-v1.5"
ONNX_BGE_REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781"
ONNX_MODEL_FILES = [
    "onnx/model.onnx", "onnx/model.onnx_data", "onnx/model.onnx.data",
    "config.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "1_Pooling/config.json", "sentence_bert_config.json",
]


class _ChunkTokenizer:
    def __init__(self, tokenizer):
        self._tokenizer = tokenizer

    def encode(self, text, add_special_tokens=False):
        return self._tokenizer.encode(text, add_special_tokens=add_special_tokens).ids


def source_locator(locator):
    # Detailed OCR line boxes/scores stay in SQLite, not in every model prompt.
    # Otherwise a scanned page's metadata alone can exceed the RAG byte budget.
    result = {key: value for key, value in locator.items() if key != "ocr_lines"}
    lines = locator.get("ocr_lines", [])
    if lines:
        result["ocr_line_count"] = len(lines)
        result["ocr_min_score"] = round(min(line["score"] for line in lines), 3)
    return result


class LocalEmbedder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._tokenizer = None
        self._batch_tokenizer = None
        self._lock = RLock()

    @property
    def ready(self):
        return self._model is not None

    def _model_source(self):
        if self.settings.embedding_model in ("BAAI/bge-small-zh-v1.5", ONNX_BGE_REPO):
            return ONNX_BGE_REPO, ONNX_BGE_REVISION
        return self.settings.embedding_model, None

    def _cache_folder(self):
        cache_home = Path(os.getenv("HF_HOME") or ROOT / ".cache" / "huggingface").expanduser()
        if not cache_home.is_absolute():
            cache_home = ROOT / cache_home
        return cache_home.resolve() / "hub"

    @staticmethod
    def _model_file(folder):
        for path in (folder / "onnx" / "model.onnx", folder / "model.onnx"):
            if path.is_file():
                return path
        return None

    def _cached_snapshot(self, cache_folder):
        """Use a complete local ONNX snapshot without contacting the Hub."""
        local = Path(self.settings.embedding_model).expanduser()
        if not local.is_absolute():
            local = ROOT / local
        if local.is_dir():
            return local.resolve()
        repo, revision = self._model_source()
        model_dir = cache_folder / ("models--" + repo.replace("/", "--"))
        candidates = []
        if revision:
            candidates.append(model_dir / "snapshots" / revision)
        else:
            try:
                cached_revision = (model_dir / "refs" / "main").read_text(encoding="utf-8").strip()
                if re.fullmatch(r"[a-f0-9]{40}", cached_revision):
                    candidates.append(model_dir / "snapshots" / cached_revision)
            except OSError:
                pass
            snapshots = model_dir / "snapshots"
            if snapshots.is_dir():
                candidates.extend(sorted((path for path in snapshots.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True))
        for path in candidates:
            required = ("config.json", "tokenizer.json")
            if not all((path / name).is_file() for name in required):
                continue
            if self._model_file(path) is None:
                continue
            return path
        return None

    def prepare_model(self):
        cache_folder = self._cache_folder()
        cached = self._cached_snapshot(cache_folder)
        if cached:
            return cached
        from huggingface_hub import snapshot_download
        repo, revision = self._model_source()
        return Path(snapshot_download(
            repo_id=repo, revision=revision, cache_dir=str(cache_folder),
            allow_patterns=ONNX_MODEL_FILES,
            local_files_only=self.settings.embedding_local_files_only,
        ))

    def _load(self):
        with self._lock:
            if self._model is None:
                if self.settings.embedding_device != "cpu":
                    raise ValueError("当前 ONNX 向量模型使用 CPU，请设置 EMBEDDING_DEVICE=cpu。")
                from tokenizers import Tokenizer, normalizers
                import onnxruntime as ort
                folder = self.prepare_model()
                model_file = self._model_file(folder)
                if model_file is None:
                    raise ValueError("向量模型目录缺少非量化 ONNX 文件 onnx/model.onnx。")
                config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
                if config.get("model_type") != "bert" or config.get("hidden_size") != 512:
                    raise ValueError("当前向量运行器仅支持 512 维 BGE Small BERT 模型。")
                token_config_file = folder / "tokenizer_config.json"
                token_config = json.loads(token_config_file.read_text(encoding="utf-8")) if token_config_file.is_file() else {}
                sentence_config_file = folder / "sentence_bert_config.json"
                sentence_config = json.loads(sentence_config_file.read_text(encoding="utf-8")) if sentence_config_file.is_file() else {}
                lowercase = sentence_config.get("do_lower_case", self.settings.embedding_model in (
                    "BAAI/bge-small-zh-v1.5", ONNX_BGE_REPO,
                ))
                counter = Tokenizer.from_file(str(folder / "tokenizer.json"))
                counter.no_truncation()
                counter.no_padding()
                batch_tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
                if lowercase:
                    for tokenizer in (counter, batch_tokenizer):
                        sequence = [normalizers.Lowercase()]
                        if tokenizer.normalizer is not None:
                            sequence.append(tokenizer.normalizer)
                        tokenizer.normalizer = normalizers.Sequence(sequence)
                batch_tokenizer.enable_truncation(max_length=512, direction="right")
                if token_config.get("padding_side", "right") != "right":
                    raise ValueError("当前 BGE CLS 运行器要求右侧 padding。")
                batch_tokenizer.enable_padding(
                    direction="right",
                    pad_id=config.get("pad_token_id", 0), pad_token="[PAD]",
                )
                # Counting chunks must never inherit inference truncation or padding.
                model = ort.InferenceSession(str(model_file), providers=["CPUExecutionProvider"])
                if "last_hidden_state" not in {item.name for item in model.get_outputs()}:
                    raise ValueError("ONNX 模型缺少 last_hidden_state 输出。")
                self._tokenizer = _ChunkTokenizer(counter)
                self._batch_tokenizer = batch_tokenizer
                self._model = model
                logger.info("Embedding loaded with ONNX Runtime (CPU, FP32)")
        return self._model

    @property
    def tokenizer(self):
        with self._lock:
            self._load()
            return self._tokenizer

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        if query and "bge" in self.settings.embedding_model.lower():
            texts = ["为这个句子生成表示以用于检索相关文章：" + text for text in texts]
        with self._lock:
            import numpy as np
            model = self._load()
            vectors = []
            for offset in range(0, len(texts), 16):
                encoded = self._batch_tokenizer.encode_batch(texts[offset:offset + 16])
                values = {
                    "input_ids": [item.ids for item in encoded],
                    "attention_mask": [item.attention_mask for item in encoded],
                    "token_type_ids": [item.type_ids for item in encoded],
                }
                inputs = {}
                for item in model.get_inputs():
                    if item.name not in values or item.type not in ("tensor(int64)", "tensor(int32)"):
                        raise ValueError("ONNX 模型的输入格式不受支持。")
                    dtype = np.int64 if item.type == "tensor(int64)" else np.int32
                    inputs[item.name] = np.asarray(values[item.name], dtype=dtype)
                hidden = model.run(["last_hidden_state"], inputs)[0]
                if hidden.ndim != 3 or hidden.shape[0] != len(encoded) or hidden.shape[2] != 512:
                    raise ValueError("ONNX 模型输出维度不符合 512 维 BGE 格式。")
                # BGE Small uses the CLS token, not mean pooling over all tokens.
                batch = np.asarray(hidden[:, 0, :], dtype=np.float32)
                norms = np.linalg.norm(batch, axis=1, keepdims=True)
                batch = batch / np.maximum(norms, np.finfo(np.float32).tiny)
                vectors.extend(batch.tolist())
            return vectors


class RetrievalService:
    def __init__(self, settings: Settings, session_factory, embedder=None, store_name=None):
        self.settings = settings
        self.sessions = session_factory
        self.embedder = embedder if embedder is not None else LocalEmbedder(settings)
        self.lock = RLock()
        if store_name is None:
            with self.sessions() as db:
                marker = db.get(AppMeta, "vector_store_name")
                store_name = marker.value if marker else "chroma"
        if not re.fullmatch(r"chroma(?:-[a-f0-9]{32})?", store_name):
            raise ValueError("向量索引目录标记无效。")
        self.store_name = store_name
        self.store_dir = (settings.data_dir / store_name).resolve()
        self._open_store()

    def _open_store(self):
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        persist_path = str(self.store_dir)
        if os.name == "nt":
            # Native HNSW file IO uses narrow paths on Windows. A relative
            # ASCII path avoids passing the project's Chinese absolute path.
            try:
                relative = os.path.relpath(self.store_dir, Path.cwd())
                if relative.isascii():
                    persist_path = relative
            except ValueError:
                pass  # Different drives cannot have a relative path.
        self.client = chromadb.PersistentClient(
            path=persist_path, settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            "course_materials", metadata={"hnsw:space": "cosine", "hnsw:batch_size": 100,
                                          "hnsw:sync_threshold": 100}, embedding_function=None,
        )

    def close(self):
        self.client.close()

    def reopen(self):
        self.close()
        self._open_store()

    def disk_issue(self):
        if not self.store_dir.exists():
            return False
        for segment in self.store_dir.iterdir():
            if segment.is_dir() and (segment / "index_metadata.pickle").exists():
                if any(not (segment / filename).is_file() for filename in HNSW_FILES):
                    return True
        return False

    def mark_unavailable(self):
        # The collection is shared by courses; a damaged physical index can
        # affect them all. Store the condition before another paid LLM call.
        with self.sessions() as db:
            course_ids = set(db.scalars(select(Document.course_id).where(Document.status == "ready")))
            for course_id in course_ids:
                key = "index_repair:" + course_id
                marker = db.get(AppMeta, key)
                if marker is None:
                    db.add(AppMeta(key=key, value="rebuild_required"))
                else:
                    marker.value = "rebuild_required"
            db.commit()

    @property
    def ready(self):
        return bool(getattr(self.embedder, "ready", True))

    def index_document(self, document: Document, blocks: list[Block], chunks: list[Chunk]):
        by_id = {b.id: b for b in blocks}
        # Model downloads/inference must not hold the index lock: API mutations
        # sometimes take that lock while holding the business-data lock.
        batches = []
        for offset in range(0, len(chunks), 64):
            group = chunks[offset:offset + 64]
            batches.append((group, self.embedder.embed([c.text for c in group])))
        with self.lock:
            self.collection.delete(where={"document_id": document.id})
            for group, vectors in batches:
                self.collection.upsert(
                    ids=[c.id for c in group], embeddings=vectors,
                    documents=[c.text for c in group],
                    metadatas=[{
                        "course_id": document.course_id, "document_id": document.id,
                        "chapter_id": by_id[c.block_id].chapter_id or "",
                        "kind": document.kind, "year": document.year or 0,
                    } for c in group],
                )

    def delete_document(self, document_id: str):
        with self.lock:
            self.collection.delete(where={"document_id": document_id})

    def delete_course(self, course_id: str):
        with self.lock:
            self.collection.delete(where={"course_id": course_id})

    def update_chapters(self, document: Document, blocks: list[Block], chunks: list[Chunk]):
        by_id = {b.id: b for b in blocks}
        if not chunks:
            return
        with self.lock:
            self.collection.update(ids=[c.id for c in chunks], metadatas=[{
                "course_id": document.course_id, "document_id": document.id,
                "chapter_id": by_id[c.block_id].chapter_id or "",
                "kind": document.kind, "year": document.year or 0,
            } for c in chunks])

    def source(self, db, source_id: str, context: bool = False):
        chunk = db.get(Chunk, source_id)
        block = db.get(Block, chunk.block_id) if chunk else db.get(Block, source_id)
        if block is None:
            return None
        doc = db.get(Document, block.document_id)
        if doc is None or doc.status != "ready":
            return None
        source = {
            "source_id": "S" + source_id, "chunk_id": source_id, "block_id": block.id,
            "document_id": doc.id, "course_id": doc.course_id,
            "text": chunk.text if chunk else block.text,
            "filename": doc.filename, "kind": doc.kind, "year": doc.year,
            "chapter_id": block.chapter_id, "locator": source_locator(block.locator),
        }
        if context:
            # Each neighbor retains its own locator. Never attach its text to the primary page label.
            unit_key = next((key for key in ("slide", "page", "paragraph", "table") if key in block.locator), None)
            position = (Block.locator[unit_key].as_integer() == block.locator[unit_key]) if unit_key else (
                (Block.order >= max(0, block.order - 1)) & (Block.order <= block.order + 1))
            neighbors = db.scalars(select(Block).where(Block.document_id == doc.id, position).order_by(Block.order)).all()
            source["context"] = [{"block_id": b.id, "source_id": "S" + b.id,
                                   "locator": source_locator(b.locator), "text": b.text[:8000],
                                   "truncated": len(b.text) > 8000, "original_characters": len(b.text)}
                                  for b in neighbors if (b.id != block.id or (chunk and chunk.text != block.text))
                                  and (doc.kind == "exam" or b.chapter_id == block.chapter_id)]
        return source

    def search(self, course_id: str, query: str, kind: str = "material", chapter_id: str | None = None,
               year: int | None = None, limit: int = 5, expand: bool = True):
        if not query.strip():
            return []
        limit = max(1, min(limit, 20))
        pool_size = max(20, limit * 4)
        material_kinds = ["exam"] if kind == "exam" else ["lecture", "note"] if kind == "knowledge" else ["lecture", "note", "exercise", "answer"]
        with self.sessions() as db:
            if db.get(AppMeta, "index_repair:" + course_id):
                raise ValueError("课程索引需要重建，请点击“重新建立索引”。")
            course = db.get(Course, course_id)
            if course is None:
                return []
            notes = db.scalar(select(Outline.markdown).where(
                Outline.course_id == course_id, Outline.status == "completed", Outline.version == course.version,
            ).order_by(Outline.created_at.desc()).limit(1)) or ""
            groups, expanded_query = query_terms(query, notes)
            statement = select(Chunk.id, Chunk.text).join(Block, Chunk.block_id == Block.id).join(Document, Chunk.document_id == Document.id).where(
                Document.course_id == course_id, Document.status == "ready", Document.kind.in_(material_kinds),
            )
            if chapter_id:
                statement = statement.where(Block.chapter_id == chapter_id)
            if year is not None:
                statement = statement.where(Document.year == year)
            lexical = []
            if groups:
                for chunk_id, text in db.execute(statement):
                    score = lexical_score(text, groups)
                    if score:
                        lexical.append((chunk_id, score))
            lexical.sort(key=lambda item: item[1], reverse=True)
            lexical = lexical[:pool_size]
        clauses = [{"course_id": course_id}, {"kind": {"$eq": "exam"} if kind == "exam" else {"$in": material_kinds}}]
        if chapter_id:
            clauses.append({"chapter_id": chapter_id})
        if year is not None:
            clauses.append({"year": year})
        from chromadb.errors import ChromaError
        with self.lock:
            try:
                count = self.collection.count()
            except ChromaError as exc:
                self.mark_unavailable()
                raise ValueError("本地检索索引无法读取。请点击“重新建立索引”；资料原文和复习笔记仍已保存。") from exc
        vector_hits = []
        if count:
            try:
                vectors = self.embedder.embed([expanded_query], query=True)
            except Exception as exc:
                if not lexical:
                    raise ValueError("本地向量模型无法加载，当前也未匹配到关键词；请检查模型是否完整下载及后端日志。") from exc
                logger.warning("Embedding unavailable; using exact course term matches", exc_info=True)
            else:
                result = None
                with self.lock:
                    try:
                        # Documents may have changed while the model was running.
                        count = self.collection.count()
                        if count:
                            result = self.collection.query(
                                query_embeddings=vectors, n_results=min(pool_size, count), where={"$and": clauses},
                                include=["distances"],
                            )
                    except ChromaError as exc:
                        self.mark_unavailable()
                        raise ValueError("本地检索索引无法读取。请点击“重新建立索引”；资料原文和复习笔记仍已保存。") from exc
                if result is not None:
                    vector_hits = [(chunk_id, 1 - float(distance)) for chunk_id, distance in zip(result["ids"][0], result["distances"][0])
                                   if 1 - float(distance) >= self.settings.retrieval_min_score]
        # Reciprocal rank fusion avoids pretending term scores and cosine scores
        # share a scale. Exact terms get a modest priority over broad similarity.
        fused, semantic_scores, lexical_scores = {}, {}, {}
        for rank, (chunk_id, score) in enumerate(vector_hits, 1):
            fused[chunk_id] = fused.get(chunk_id, 0) + 0.8 / (60 + rank)
            semantic_scores[chunk_id] = score
        for rank, (chunk_id, score) in enumerate(lexical, 1):
            fused[chunk_id] = fused.get(chunk_id, 0) + 1.2 / (60 + rank)
            lexical_scores[chunk_id] = score
        sources = []
        priority = {chunk_id for chunk_id, score in lexical[:2] if score >= 0.85}
        ordered = sorted(fused.items(), key=lambda item: (item[0] in priority, item[1]), reverse=True)
        with self.sessions() as db:
            for chunk_id, score in ordered:
                source = self.source(db, chunk_id, context=expand)
                if source and source["course_id"] == course_id and (not chapter_id or source["chapter_id"] == chapter_id):
                    source["score"] = round(1 + lexical_scores[chunk_id] if chunk_id in priority else score / (2 / 61), 4)
                    source["semantic_score"] = round(semantic_scores[chunk_id], 4) if chunk_id in semantic_scores else None
                    source["lexical_score"] = round(lexical_scores[chunk_id], 4) if chunk_id in lexical_scores else None
                    sources.append(source)
                    if len(sources) == limit:
                        break
        return sources
