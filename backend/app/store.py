import json
import logging
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import delete, select

from .chunking import split_text
from .config import Settings
from .db import create_database
from .file_chapters import document_role, file_order, file_title
from .llm import ModelClient
from .model_config import ModelConfigStore
from .memory import MemoryService
from .models import AppMeta, Block, Chapter, ChatSession, Chunk, Course, Document, Job, Message, Outline, PracticeAnswerIndex, PracticeSet, ReviewSpace, WrongQuestion, new_id
from .parsing import parse_document
from .practice_progress import answer_policy_current
from .retrieval import EMBEDDING_RUNTIME, RetrievalService
from .retrieval_ranking import KnowledgeReranker
from .knowledge_retrieval import KnowledgeRetrieval
from .visual_recognition import VisualRecognizer

logger = logging.getLogger(__name__)


class Services:
    def __init__(self, settings: Settings, embedder=None, llm=None):
        self.settings = settings
        self.engine, self.sessions = create_database(settings.data_dir)
        self.lock = RLock()
        # OCR uses shared workers; serialize ingestion without blocking API mutations.
        self.ingest_lock = RLock()
        self.upload_dir = settings.data_dir / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.retrieval = RetrievalService(settings, self.sessions, embedder)
        if self.retrieval.disk_issue():
            logger.error("Vector index binary files are missing; rebuild required")
            self.retrieval.mark_unavailable()
        self.model_config = ModelConfigStore(settings)
        self.llm = llm if llm is not None else ModelClient(settings, self.model_config)
        self.retrieval.reranker = KnowledgeReranker(self.llm, self.model_config)
        self.knowledge = KnowledgeRetrieval(self)
        self.visual = VisualRecognizer(settings)
        self.chat_locks = {}
        self.review_space_locks = {}
        self.practice_locks = {}
        self.answer_preparation_locks = {}
        self.explanation_locks = {}
        self.memory = MemoryService(self)
        with self.sessions() as db:
            # Retire the separate model-extracted glossary; review notes now supply names.
            db.execute(delete(AppMeta).where(AppMeta.key.like("course_vocabulary:%")))
            model = db.get(AppMeta, "embedding_model")
            runtime = db.get(AppMeta, "embedding_runtime")
            changed = (model is not None and model.value != settings.embedding_model) or (
                embedder is None and (runtime is None or runtime.value != EMBEDDING_RUNTIME))
            existing = model is not None or db.scalar(select(Chunk.id).limit(1)) is not None
            if changed and existing:
                # Keep the published model identity until a full replacement succeeds.
                self.retrieval.index_compatible = False
                for course_id in set(db.scalars(select(Document.course_id).where(Document.status == "ready"))):
                    key = "index_repair:" + course_id
                    flag = db.get(AppMeta, key)
                    if flag is None:
                        db.add(AppMeta(key=key, value="rebuild_required"))
                    else:
                        flag.value = "rebuild_required"
                logger.warning("Embedding model/runtime changed; rebuild required before retrieval or ingestion")
            else:
                if model is None:
                    db.add(AppMeta(key="embedding_model", value=settings.embedding_model))
                if embedder is None:
                    if runtime is None:
                        db.add(AppMeta(key="embedding_runtime", value=EMBEDDING_RUNTIME))
                    else:
                        runtime.value = EMBEDDING_RUNTIME
            db.commit()

    def recover(self):
        migrated = []
        with self.lock, self.sessions() as db:
            # Both uploaded and model answer mismatches are errors; the
            # resolution retains the grading provenance independently.
            for wrong in db.scalars(select(WrongQuestion).where(WrongQuestion.reason == "reference")):
                wrong.reason = "incorrect"
            for wrong in db.scalars(select(WrongQuestion)):
                if wrong.resolution and not answer_policy_current(wrong.resolution):
                    wrong.resolution = {}
                    if wrong.reason == "incorrect":
                        wrong.reason = "manual"
            for index in db.scalars(select(PracticeAnswerIndex).where(PracticeAnswerIndex.status == "processing")):
                index.status, index.error = "failed", "答案准备被重启中断，可在提交答案时重试。"
            for bank in db.scalars(select(PracticeSet).where(PracticeSet.status.in_(["queued", "processing"]))):
                doc = db.get(Document, bank.document_id)
                bank.status, bank.error = "failed", ("后端重启中断了 AI 生题，请在在线练习中重试生题。"
                                                     if doc and doc.kind == "generated" else "后端重启中断了题目整理，请点击重试。")
            for doc in db.scalars(select(Document).where(Document.status.in_(["queued", "processing"]))):
                doc.status = "failed"
                doc.error = "后端重启中断了处理，可点击重试。"
            for job in db.scalars(select(Job).where(Job.status.in_(["queued", "running"]))):
                job.status = "interrupted"
                job.stage = "任务被重启中断，可重试已完成章节之后的部分"
                outline = db.get(Outline, job.outline_id)
                if outline:
                    outline.status = "interrupted"
            # Upgrade old heading-based directories once; no reparsing or model calls.
            for course in db.scalars(select(Course)):
                if db.get(AppMeta, "file_chapter_policy:" + course.id) is None:
                    self.draft_chapters(db, course.id, reset=True)
                    migrated.append(course.id)
            db.commit()
        for course_id in migrated:
            try:
                self.sync_committed_chapters(course_id)
            except ValueError:
                logger.warning("File chapters saved; index metadata needs repair for %s", course_id, exc_info=True)

    @staticmethod
    def busy(locks, *identities):
        """Whether a task lock for any of these records is held."""
        return any((lock := locks.get(identity)) is not None and lock.locked() for identity in identities)

    def model_busy(self, db):
        """Queued notes or any held task lock; switching models then would mix configurations."""
        if db.scalar(select(Job.id).where(Job.status.in_(["queued", "running"])).limit(1)):
            return True
        # Copy the values: request threads add locks while this check runs.
        return any(lock.locked() for locks in (self.chat_locks, self.review_space_locks, self.practice_locks,
                                               self.explanation_locks, self.answer_preparation_locks)
                   for lock in list(locks.values()))

    def changed(self, db, course_id: str, unconfirm: bool = True):
        course = db.get(Course, course_id)
        course.version += 1
        if unconfirm:
            for chapter in db.scalars(select(Chapter).where(Chapter.course_id == course_id)):
                chapter.confirmed = False

    @contextmanager
    def _indexed_deletion(self, db, course_id, message):
        """SQL rollback cannot undo Chroma writes; retain a repair checkpoint."""
        try:
            yield
        except Exception as exc:
            logger.exception("Indexed deletion failed for course %s", course_id)
            db.rollback()
            try:
                flag = db.get(AppMeta, "index_repair:" + course_id)
                if flag:
                    flag.value = "rebuild_required"
                else:
                    db.add(AppMeta(key="index_repair:" + course_id, value="rebuild_required"))
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not persist the index repair checkpoint")
            raise RuntimeError(message) from exc

    def _cleanup_upload(self, path):
        # Physical cleanup follows a committed business deletion. A locked file
        # must not turn that successful deletion into an HTTP failure.
        try:
            path = Path(path).resolve()
            if path.parent != self.upload_dir.resolve():
                logger.warning("Skipped upload cleanup outside the uploads directory")
                return
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove retired upload %s", Path(path).name, exc_info=True)

    def _require_chat_idle(self, db, course_id):
        ids = db.scalars(select(ChatSession.id).where(ChatSession.course_id == course_id))
        if self.busy(self.chat_locks, *ids):
            raise ValueError("课程会话正在回答，请停止或完成后再删除")

    def remove_course(self, course_id: str):
        with self.lock, self.sessions() as db:
            course = db.get(Course, course_id)
            if course is None:
                return False
            documents = db.scalars(select(Document).where(Document.course_id == course_id)).all()
            session_ids = list(db.scalars(select(ChatSession.id).where(ChatSession.course_id == course_id)))
            space_ids = list(db.scalars(select(ReviewSpace.id).where(ReviewSpace.course_id == course_id)))
            if self.busy(self.review_space_locks, *space_ids):
                raise ValueError("复习空间正在安排下一步，请等待完成或暂停后再删除课程")
            if any(doc.status in ("queued", "processing") for doc in documents):
                raise ValueError("课程资料正在处理，请完成后再删除课程")
            doc_ids = [doc.id for doc in documents]
            if self.busy(self.practice_locks, *doc_ids) or self.busy(self.answer_preparation_locks, *doc_ids):
                raise ValueError("正在整理题目或回答练习，请完成后再删除课程")
            if db.scalar(select(Job.id).join(Outline).where(Outline.course_id == course_id, Job.status.in_(["queued", "running"]))):
                raise ValueError("课程笔记正在生成，请完成后再删除课程")
            self._require_chat_idle(db, course_id)
            # Resolve every file before deleting anything; never follow a saved path outside uploads.
            paths = [Path(doc.path).resolve() for doc in documents]
            if any(path.parent != self.upload_dir.resolve() for path in paths):
                raise ValueError("资料保存位置异常，未删除课程，请检查后端配置")
            document_ids = select(Document.id).where(Document.course_id == course_id)
            outline_ids = select(Outline.id).where(Outline.course_id == course_id)
            with self._indexed_deletion(db, course_id, "课程删除失败，课程仍保留；请重建索引或再次删除"):
                self.retrieval.delete_course(course_id)
                db.execute(delete(Chunk).where(Chunk.document_id.in_(document_ids)))
                db.execute(delete(Block).where(Block.document_id.in_(document_ids)))
                db.execute(delete(Document).where(Document.course_id == course_id))
                db.execute(delete(Chapter).where(Chapter.course_id == course_id))
                db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
                db.execute(delete(ChatSession).where(ChatSession.course_id == course_id))
                db.execute(delete(Job).where(Job.outline_id.in_(outline_ids)))
                db.execute(delete(Outline).where(Outline.course_id == course_id))
                db.execute(delete(AppMeta).where(AppMeta.key == "index_repair:" + course_id))
                db.execute(delete(AppMeta).where(AppMeta.key == "file_chapter_policy:" + course_id))
                db.execute(delete(AppMeta).where(AppMeta.key == "chapter_excluded:" + course_id))
                db.execute(delete(AppMeta).where(AppMeta.key.in_(
                    [prefix + doc.id for doc in documents for prefix in ("practice_answer_pair:", "practice_title:", "knowledge_units:", "practice_generation:")])))
                db.delete(course)
                db.commit()
            for identity in session_ids:
                self.chat_locks.pop(identity, None)
            for identity in space_ids:
                self.review_space_locks.pop(identity, None)
            for path in paths:
                self._cleanup_upload(path)
            return True

    def excluded_chapter_documents(self, db, course_id: str):
        flag = db.get(AppMeta, "chapter_excluded:" + course_id)
        return set(json.loads(flag.value)) if flag else set()

    def set_excluded_chapter_documents(self, db, course_id: str, document_ids):
        key = "chapter_excluded:" + course_id
        flag = db.get(AppMeta, key)
        if not document_ids:
            if flag:
                db.delete(flag)
        elif flag:
            flag.value = json.dumps(sorted(document_ids))
        else:
            db.add(AppMeta(key=key, value=json.dumps(sorted(document_ids))))

    def draft_chapters(self, db, course_id: str, reset: bool = False):
        """Add new files while retaining edits; explicitly reset to one chapter per file."""
        if reset:
            self.set_excluded_chapter_documents(db, course_id, set())
        excluded = set() if reset else self.excluded_chapter_documents(db, course_id)
        documents = sorted((doc for doc in db.scalars(select(Document).where(Document.course_id == course_id, Document.status == "ready", Document.kind != "generated"))
                            if doc.id not in excluded),
                           key=lambda doc: (file_order(doc.filename), doc.created_at, doc.id))
        chapters = db.scalars(select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.position)).all()
        blocks = db.scalars(select(Block).join(Document).where(Document.course_id == course_id)).all()
        if chapters and not reset:
            # Reparse/ingestion retains whole-file ownership, including merged chapters.
            by_document, document_owners, chapter_documents = {}, {}, {}
            existing = {chapter.id: chapter for chapter in chapters}
            for block in blocks:
                by_document.setdefault(block.document_id, []).append(block)
                if block.chapter_id in existing:
                    document_owners.setdefault(block.document_id, set()).add(block.chapter_id)
                    chapter_documents.setdefault(block.chapter_id, set()).add(block.document_id)
            added = []
            for doc in documents:
                entries = by_document.get(doc.id, [])
                if not entries:
                    continue
                owners = document_owners.get(doc.id, set())
                # A file reclassified as a paper or guidance must leave a merged knowledge chapter.
                separate = document_role(doc) != "knowledge" and any(len(chapter_documents[identity]) > 1 for identity in owners)
                if len(owners) == 1 and not separate:
                    chapter = existing[next(iter(owners))]
                else:
                    chapter = Chapter(id=new_id(), course_id=course_id, title=file_title(doc.filename))
                    db.add(chapter)
                    db.flush()
                    added.append(chapter)
                for block in entries:
                    block.chapter_id = chapter.id
            represented = {block.chapter_id for block in blocks if block.chapter_id}
            kept = [chapter for chapter in chapters + added if chapter.id in represented]
            for position, chapter in enumerate(kept):
                chapter.position, chapter.confirmed = position, False
            for chapter in chapters:
                if chapter.id not in represented:
                    db.delete(chapter)
            self.changed(db, course_id)
            db.flush()
            return
        owners, by_document = {}, {}
        for block in blocks:
            by_document.setdefault(block.document_id, []).append(block)
            if block.chapter_id:
                owners.setdefault(block.chapter_id, set()).add(block.document_id)
            block.chapter_id = None
        db.flush()
        kept = set()
        for position, doc in enumerate(documents):
            title = file_title(doc.filename)
            chapter = next((c for c in chapters if c.id not in kept and c.title == title
                            and owners.get(c.id) == {doc.id}), None)
            if chapter is None:
                chapter = Chapter(id=new_id(), course_id=course_id)
                db.add(chapter)
            chapter.title, chapter.position, chapter.confirmed = title, position, False
            db.flush()
            kept.add(chapter.id)
            for block in by_document.get(doc.id, []):
                block.chapter_id = chapter.id
        db.flush()
        for chapter in chapters:
            if chapter.id not in kept:
                db.delete(chapter)
        key = "file_chapter_policy:" + course_id
        if db.get(AppMeta, key) is None:
            db.add(AppMeta(key=key, value="1"))
        self.changed(db, course_id)
        db.flush()

    def sync_chapter_metadata(self, db, course_id):
        for document in db.scalars(select(Document).where(Document.course_id == course_id, Document.status == "ready")):
            blocks = db.scalars(select(Block).where(Block.document_id == document.id)).all()
            chunks = db.scalars(select(Chunk).where(Chunk.document_id == document.id)).all()
            self.retrieval.update_chapters(document, blocks, chunks)

    def sync_committed_chapters(self, course_id):
        """SQLite is authoritative; a failed derived-index update is visible and repairable."""
        with self.lock, self.sessions() as db:
            key = "index_repair:" + course_id
            existing = db.get(AppMeta, key)
            if existing and existing.value == "rebuild_required":
                raise ValueError("章节已保存，但本地检索索引损坏。请点击“重新建立索引”恢复问答。")
            try:
                self.sync_chapter_metadata(db, course_id)
            except Exception:
                flag = db.get(AppMeta, key)
                if flag is None:
                    db.add(AppMeta(key=key, value="required"))
                db.commit()
                raise ValueError("章节已保存，但向量索引同步失败。请点击重新建立索引，再继续检索。")
            flag = db.get(AppMeta, key)
            if flag:
                db.delete(flag)
                db.commit()

    def rebuild_index(self):
        """Rebuild derived vectors in a new generation; never overwrite the damaged store."""
        with self.lock, self.retrieval.knowledge.build_lock, self.retrieval.lock:
            with self.sessions() as db:
                if db.scalar(select(Document.id).where(Document.status.in_(["queued", "processing"])).limit(1)):
                    raise ValueError("资料正在处理，请完成后再重新建立索引。")
            replacement = None
            try:
                replacement = RetrievalService(
                    self.retrieval.settings, self.sessions, self.retrieval.embedder,
                    store_name="chroma-" + new_id(),
                )
                replacement.knowledge.staged = {}
                document_count, expected = 0, 0
                with self.sessions() as db:
                    documents = db.scalars(select(Document).where(
                        Document.status == "ready", Document.kind != "generated")).all()
                    for document in documents:
                        blocks = db.scalars(select(Block).where(Block.document_id == document.id).order_by(Block.order)).all()
                        chunks = db.scalars(select(Chunk).where(Chunk.document_id == document.id)).all()
                        if not chunks:
                            raise ValueError("可用资料缺少已保存的检索片段，请先对该资料重新入库。")
                        replacement.index_document(document, blocks, chunks)
                        expected += len(chunks)
                        document_count += 1
                # Finish persistence before publishing the new generation.
                replacement.reopen()
                if replacement.disk_issue() or replacement.collection.count() != expected:
                    raise ValueError("新索引未能完整保存，未切换索引。请检查本地目录写入权限和后端日志。")
                expected_passages = sum(len(passages) for _, passages, _ in replacement.knowledge.staged.values())
                if replacement.knowledge_collection.count() != expected_passages:
                    raise ValueError("整页/小节知识索引未能完整保存，未切换索引。请检查向量模型和后端日志。")
                with self.sessions() as db:
                    replacement.knowledge.publish_staged(db)
                    marker = db.get(AppMeta, "vector_store_name")
                    if marker is None:
                        db.add(AppMeta(key="vector_store_name", value=replacement.store_name))
                    else:
                        marker.value = replacement.store_name
                    for key, value in (("embedding_model", self.settings.embedding_model),
                                       ("embedding_runtime", EMBEDDING_RUNTIME)):
                        marker = db.get(AppMeta, key)
                        if marker is None:
                            db.add(AppMeta(key=key, value=value))
                        else:
                            marker.value = value
                    for flag in db.scalars(select(AppMeta).where(AppMeta.key.like("index_repair:%"))):
                        db.delete(flag)
                    db.commit()
                old_client = self.retrieval.client
                self.retrieval.client = replacement.client
                self.retrieval.collection = replacement.collection
                self.retrieval.knowledge_collection = replacement.knowledge_collection
                self.retrieval.store_dir = replacement.store_dir
                self.retrieval.store_name = replacement.store_name
                self.retrieval.index_compatible = True
                self.retrieval.knowledge.ensured.clear()
                self.retrieval.knowledge.retry_after.clear()
                replacement = None  # Ownership transferred to the live service.
                try:
                    old_client.close()
                except Exception:
                    logger.warning("Failed to close the retired index client", exc_info=True)
                return {"documents": document_count, "chunks": expected, "knowledge_passages": expected_passages}
            except Exception as exc:
                # A failed replacement does not damage the live index. Existing
                # repair flags stay in place until a replacement is published.
                logger.exception("Vector index rebuild failed; old index and SQLite records retained")
                if isinstance(exc, ValueError):
                    raise
                raise ValueError("重新建立索引失败，资料仍保留。请检查本地向量模型加载、目录权限和后端日志。") from exc
            finally:
                if replacement is not None:
                    try:
                        replacement.close()
                    except Exception:
                        logger.warning("Failed to close incomplete index client", exc_info=True)

    def ingest_document(self, document_id: str):
        # queued/processing reserves the file against deletion and reparsing.
        # Only SQLite publication holds the global lock; OCR/model work does not.
        with self.ingest_lock:
            try:
                self.retrieval.require_current_index()
                with self.lock, self.sessions() as db:
                    doc = db.get(Document, document_id)
                    if not doc or doc.status != "queued":
                        return
                    doc.status, doc.error = "processing", None
                    db.commit()
                result = parse_document(Path(doc.path), doc.filename, self.visual,
                                        image_dir=self.settings.data_dir / "question-images"
                                        if doc.kind in ("exam", "exercise") else None)
                with self.lock, self.sessions() as db:
                    doc = db.get(Document, document_id)
                    previous_blocks = db.scalars(select(Block).where(Block.document_id == doc.id).order_by(Block.order)).all()
                    def position(locator, unit_only=False):
                        keys = ("page", "slide", "paragraph", "table", "line") if unit_only else ("page", "slide", "shape_path", "paragraph", "table", "image", "line", "line_end", "tika_unit")
                        return tuple((key, locator[key]) for key in keys if key in locator)
                    exact_chapters, unit_chapters = {}, {}
                    for old in previous_blocks:
                        if old.chapter_id:
                            exact_chapters.setdefault(position(old.locator), old.chapter_id)
                            key = position(old.locator, True)
                            unit_chapters.setdefault(key, set()).add(old.chapter_id)
                    file_chapters = {old.chapter_id for old in previous_blocks if old.chapter_id}
                    doc.warnings = result.warnings
                    doc.units_total, doc.units_with_text = result.units_total, result.units_with_text
                    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
                    db.execute(delete(Block).where(Block.document_id == doc.id))
                    blocks = []
                    for parsed in result.blocks:
                        block = Block(id=new_id(), document_id=doc.id, order=parsed.order,
                                      text=parsed.text, locator=parsed.locator, heading=parsed.heading,
                                      block_type=parsed.block_type)
                        block.chapter_id = exact_chapters.get(position(parsed.locator))
                        candidates = unit_chapters.get(position(parsed.locator, True), set())
                        if block.chapter_id is None and len(candidates) == 1:
                            block.chapter_id = next(iter(candidates))
                        if block.chapter_id is None and len(file_chapters) == 1:
                            block.chapter_id = next(iter(file_chapters))
                        db.add(block)
                        blocks.append(block)
                    db.commit()
                # Raw text is preserved even if model loading/indexing fails.
                tokenizer = getattr(self.retrieval.embedder, "tokenizer", None)
                chunks = []
                for block in blocks:
                    for position, text in enumerate(split_text(block.text, tokenizer=tokenizer)):
                        chunks.append(Chunk(id=new_id(), document_id=doc.id, block_id=block.id, position=position, text=text))
                if not chunks:
                    raise ValueError("未提取到可入库内容。")
                with self.lock, self.sessions() as db:
                    db.add_all(chunks)
                    db.commit()
                self.retrieval.index_document(doc, blocks, chunks)
                with self.lock, self.sessions() as db:
                    doc = db.get(Document, document_id)
                    doc.status = "ready"
                    self.draft_chapters(db, doc.course_id)
                    db.commit()
                    self.sync_committed_chapters(doc.course_id)
            except Exception as exc:
                with self.lock, self.sessions() as db:
                    doc = db.get(Document, document_id)
                    if doc:
                        doc.status = "failed"
                        # Parsing errors are safe descriptive messages; network errors may contain provider internals.
                        doc.error = str(exc)[:1000] if isinstance(exc, ValueError) else "处理失败，请检查模型下载／网络和后端日志后重试。"
                        db.commit()
                try:
                    self.retrieval.delete_document(document_id)
                except Exception:
                    logger.exception("Failed to clean partial vectors; document remains unavailable: %s", document_id)
                logger.exception("Document processing failed: %s", document_id)

    def remove_document(self, document_id: str):
        with self.lock, self.sessions() as db:
            doc = db.get(Document, document_id)
            if doc is None:
                return False
            self._require_chat_idle(db, doc.course_id)
            if doc.status in ("queued", "processing"):
                raise ValueError("资料正在处理，请完成后再删除资料")
            if self.busy(self.practice_locks, doc.id):
                raise ValueError("正在整理题目或回答练习，请完成后再删除资料")
            if self.busy(self.answer_preparation_locks, doc.id):
                raise ValueError("正在准备这份练习的答案，请完成后再删除资料")
            if doc.kind == "generated":
                file_path = self.upload_dir / (doc.id + ".json")
                db.execute(delete(AppMeta).where(AppMeta.key.in_(["practice_title:" + doc.id, "practice_generation:" + doc.id])))
                db.delete(doc)
                db.commit()
                self._cleanup_upload(file_path)
                return True
            course_id, file_path = doc.course_id, Path(doc.path)
            if file_path.resolve().parent != self.upload_dir.resolve():
                raise ValueError("资料保存位置异常，未删除资料，请检查后端配置")
            with self._indexed_deletion(db, course_id, "资料删除失败，资料仍保留；请重建索引或再次删除"):
                self.retrieval.delete_document(doc.id)
                db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
                db.execute(delete(Block).where(Block.document_id == doc.id))
                self.set_excluded_chapter_documents(db, course_id, self.excluded_chapter_documents(db, course_id) - {doc.id})
                db.execute(delete(AppMeta).where(AppMeta.key.in_([
                    "practice_answer_pair:" + doc.id, "practice_title:" + doc.id, "knowledge_units:" + doc.id])))
                db.delete(doc)
                db.flush()
                self.draft_chapters(db, course_id)
                db.commit()
            self._cleanup_upload(file_path)
            return True
