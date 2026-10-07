from contextlib import asynccontextmanager
import hashlib
import logging
import re
from pathlib import Path
from threading import Lock
import sys
from typing import Literal
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import AliasChoices, BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from .agent import ReviewAgentService
from .chat_answers import ChatAnswerGrounding
from .config import Settings
from .file_chapters import chapter_role, document_role
from .llm import ModelClient, ModelRuntimeError, model_failure_message
from .models import AppMeta, Block, Chapter, ChatSession, Course, Document, Job, Message, Outline, PracticeQuestion, PracticeSet, new_id
from .model_images import image_resolution_current
from .outlines import NOTES_STYLE, OutlineService
from .practice import PracticeService
from .practice_progress import answer_policy_current
from .store import Services
from .upload_formats import SUPPORTED_SUFFIXES, SUPPORTED_FORMATS_LABEL

logger = logging.getLogger(__name__)


class CourseInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("课程名称不能为空")
        return value


class ChapterInput(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=150)
    position: int = Field(default=0, ge=0)
    block_ids: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("章节名称不能为空")
        return value


class ChaptersInput(BaseModel):
    chapters: list[ChapterInput]
    confirm: bool = True
    expected_version: int | None = Field(default=None, ge=0)


class SessionInput(BaseModel):
    title: str = Field(default="新复习对话", min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("会话名称不能为空")
        return value


class SessionNameInput(SessionInput):
    title: str = Field(min_length=1, max_length=100)


class NoteChatInput(BaseModel):
    outline_id: str = Field(min_length=1, max_length=100)
    selected_text: str = Field(min_length=1, max_length=1200)
    source_text: str = Field(min_length=1, max_length=4500)
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=1)


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000, validation_alias=AliasChoices("message", "question"))
    practice_question_id: str | None = Field(default=None, max_length=100)
    wrong_question_id: str | None = Field(default=None, max_length=100)
    note_context: NoteChatInput | None = None
    selected: list[str] = Field(default_factory=list, max_length=100)
    own_answer: str = Field(default="", max_length=20000)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class ModelConfigInput(BaseModel):
    provider: str = Field(default="custom", max_length=100)
    api: Literal["openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai"] = "openai-completions"
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    # null retains a key only for the same provider/protocol/endpoint; empty removes it.
    api_key: str | None = None
    allow_without_key: bool = False
    timeout_seconds: float = Field(default=90, ge=10, le=600)
    context_window: int = Field(default=32768, ge=4096, le=2000000)
    max_tokens: int = Field(default=8192, ge=256, le=200000)
    reasoning: bool = False
    supports_images: bool = False


class PracticeSubmission(BaseModel):
    selected: list[str] = Field(min_length=1, max_length=26)


class PracticeAnswerPairingInput(BaseModel):
    mode: Literal["auto", "manual", "none"] = "auto"
    answer_document_id: str | None = Field(default=None, max_length=100)


class PracticeProgressInput(BaseModel):
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=1, le=9007199254740991)
    current_question_id: str | None = None
    selections: dict[str, list[str]] = Field(default_factory=dict)
    drafts: dict[str, str] = Field(default_factory=dict)


class WrongQuestionMark(BaseModel):
    own_answer: str | None = Field(default=None, max_length=20000)
    selected: list[str] | None = Field(default=None, max_length=26)


class WrongQuestionInput(BaseModel):
    stem: str = Field(min_length=1, max_length=20000)
    own_answer: str = Field(default="", max_length=20000)
    answer: str = Field(default="", max_length=20000)

    @field_validator("stem", "own_answer", "answer")
    @classmethod
    def clean_text(cls, value, info):
        value = value.strip()
        if info.field_name == "stem" and not value:
            raise ValueError("题目内容不能为空")
        return value


class WrongQuestionReviewInput(BaseModel):
    selected: list[str] = Field(default_factory=list, max_length=26)
    own_answer: str = Field(default="", max_length=20000)


def row(obj):
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns
            if column.name not in ("path", "snapshot", "chapter_results")}


def require(db, model, identity):
    result = db.get(model, identity)
    if result is None:
        raise HTTPException(404, "未找到该记录")
    return result


def create_app(settings: Settings | None = None, embedder=None, llm=None):
    # HelloAgents 0.2.9 prints emoji. A GBK console must not make a valid API request fail.
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            try:
                output.reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass
    settings = settings or Settings.from_env()
    services = Services(settings, embedder, llm)
    agent, outline_service = ReviewAgentService(services), OutlineService(services)
    practice = PracticeService(services)
    agent.answer_grounding = ChatAnswerGrounding(practice)

    @asynccontextmanager
    async def lifespan(app):
        services.recover()
        yield
        try:
            services.visual.close()
        finally:
            try:
                services.retrieval.close()
            finally:
                services.engine.dispose()

    app = FastAPI(title="期末复习 Agent", version="0.1.0", lifespan=lifespan)
    app.state.services, app.state.sessions, app.state.settings = services, services.sessions, settings
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:4173", "http://localhost:4173"],
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Content-Type"],
                       expose_headers=["Content-Disposition"])

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, exc):
        # A handled response passes through CORS, so the browser can display the
        # database failure instead of reporting it as a network connection error.
        logger.error("Database operation failed", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(status_code=500, content={"detail": "数据库操作失败，本次请求未完成，请查看后端日志后重试。"})

    @app.get("/health")
    def health():
        with services.sessions() as db:
            repair_courses = list(db.scalars(select(AppMeta.key).where(AppMeta.key.like("index_repair:%"))))
        return {"status": "ok", "llm_configured": bool(services.llm.configured),
                "embedding_model": settings.embedding_model, "embedding_ready": services.retrieval.ready,
                "index_repair_courses": [key.split(":", 1)[1] for key in repair_courses]}

    @app.get("/system/model-config")
    def model_configuration():
        return services.model_config.public()

    @app.get("/system/model-catalog")
    def model_catalog():
        try:
            return ModelClient(settings, services.model_config).catalog()
        except ModelRuntimeError as exc:
            raise HTTPException(503, model_failure_message(exc)) from exc

    @app.put("/system/model-config")
    def save_model_configuration(payload: ModelConfigInput):
        with services.lock, services.sessions() as db:
            if (db.scalar(select(Job.id).where(Job.status.in_(["queued", "running"])).limit(1))
                    or any(lock.locked() for lock in services.chat_locks.values())
                    or any(lock.locked() for lock in services.practice_locks.values())
                    or any(lock.locked() for lock in services.explanation_locks.values())
                    or any(lock.locked() for lock in services.answer_preparation_locks.values())):
                raise HTTPException(409, "正在回答、生成练习解析、整理练习或生成笔记，请结束任务后再切换模型配置")
            try:
                return services.model_config.save(payload.model_dump())
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except OSError as exc:
                raise HTTPException(500, "无法保存模型配置，请检查本地 data 目录的写入权限") from exc

    @app.post("/system/model-config/check")
    def check_model_configuration(payload: ModelConfigInput):
        try:
            candidate = services.model_config.candidate(payload.model_dump())
            return ModelClient(settings, services.model_config).check(candidate)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, model_failure_message(exc)) from exc

    @app.post("/system/warmup", status_code=202)
    def warmup(tasks: BackgroundTasks):
        def load():
            services.retrieval.embedder.embed(["模型预检"])
        tasks.add_task(load)
        return {"message": "向量模型正在加载；首次使用会下载模型，请检查后端日志。"}

    @app.get("/courses")
    def courses():
        with services.sessions() as db:
            return [row(c) for c in db.scalars(select(Course).order_by(Course.created_at))]

    @app.post("/courses", status_code=201)
    def create_course(payload: CourseInput):
        with services.lock, services.sessions() as db:
            course = Course(name=payload.name)
            db.add(course)
            db.commit()
            return row(course)

    @app.patch("/courses/{course_id}")
    def rename_course(course_id: str, payload: CourseInput):
        with services.lock, services.sessions() as db:
            course = require(db, Course, course_id)
            course.name = payload.name
            db.commit()
            return row(course)

    @app.delete("/courses/{course_id}", status_code=204)
    def delete_course(course_id: str):
        try:
            if not services.remove_course(course_id):
                raise HTTPException(404, "课程不存在或已删除")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        return Response(status_code=204)

    @app.get("/courses/{course_id}/documents")
    def documents(course_id: str):
        with services.sessions() as db:
            require(db, Course, course_id)
            excluded = services.excluded_chapter_documents(db, course_id)
            return [{**row(d), "chapter_excluded": d.id in excluded}
                    for d in db.scalars(select(Document).where(Document.course_id == course_id).order_by(Document.created_at))]

    def queue_document(db, doc):
        """Reserve a document before reparsing or changing its upload metadata."""
        if doc.status in ("queued", "processing"):
            raise HTTPException(409, "文件仍在处理中，请完成后再修改或重新解析")
        if services.practice_locks.get(doc.id) and services.practice_locks[doc.id].locked():
            raise HTTPException(409, "正在整理题目或回答练习，请完成后再修改或重新解析")
        if services.answer_preparation_locks.get(doc.id) and services.answer_preparation_locks[doc.id].locked():
            raise HTTPException(409, "正在准备这份练习的答案，请完成后再修改或重新解析")
        if db.get(AppMeta, "index_repair:" + doc.course_id):
            raise HTTPException(409, "请先点击“重新建立索引”恢复本地索引，再修改或重新解析资料。")
        if db.scalar(select(Job.id).join(Outline).where(
                Outline.course_id == doc.course_id, Job.status.in_(["queued", "running"])).limit(1)):
            raise HTTPException(409, "本课程的复习笔记任务仍在执行，请完成后再修改或重新解析资料。")
        session_ids = db.scalars(select(ChatSession.id).where(ChatSession.course_id == doc.course_id))
        if any(services.chat_locks.get(identity) and services.chat_locks[identity].locked() for identity in session_ids):
            raise HTTPException(409, "本课程的会话正在回答，请停止或完成后再修改或重新解析资料。")
        doc.status, doc.error = "queued", None
        services.changed(db, doc.course_id)

    @app.post("/courses/{course_id}/documents", status_code=202)
    def upload_document(course_id: str, tasks: BackgroundTasks, file: UploadFile = File(...),
                        kind: Literal["lecture", "note", "exam", "exercise", "answer"] = Form("lecture"),
                        year: int | None = Form(None, ge=1900, le=2100)):
        filename = (file.filename or "").replace("\\", "/").split("/")[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(415, f"支持 {SUPPORTED_FORMATS_LABEL}。")
        content = file.file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "文件超过 50 MB，请拆分后上传。")
        digest = hashlib.sha256(content).hexdigest()
        with services.lock, services.sessions() as db:
            require(db, Course, course_id)
            existing = db.scalar(select(Document).where(Document.course_id == course_id, Document.file_hash == digest))
            if existing:
                if existing.kind == kind and existing.year == year:
                    return {**row(existing), "duplicate": True}
                # The selected type/year correct the existing upload. Reparse
                # so a file newly used as a paper also gets its local figures.
                queue_document(db, existing)
                existing.kind, existing.year = kind, year
                db.commit()
                document_id = existing.id
                result = {**row(existing), "duplicate": True, "metadata_updated": True}
            else:
                if db.get(AppMeta, "index_repair:" + course_id):
                    raise HTTPException(409, "请先点击“重新建立索引”恢复本地索引，再上传新资料。")
                document_id = new_id()
                path = services.upload_dir / (document_id + suffix)
                path.write_bytes(content)
                document = Document(id=document_id, course_id=course_id, filename=filename, kind=kind,
                                    year=year, file_hash=digest, path=str(path))
                db.add(document)
                services.changed(db, course_id)
                db.commit()
                result = row(document)
        tasks.add_task(services.ingest_document, document_id)
        return result

    @app.get("/documents/{document_id}/blocks")
    def document_blocks(document_id: str):
        with services.sessions() as db:
            require(db, Document, document_id)
            return [row(b) for b in db.scalars(select(Block).where(Block.document_id == document_id).order_by(Block.order))]

    @app.get("/question-images/{filename}")
    def question_image(filename: str):
        if not re.fullmatch(r"[a-f0-9]{64}\.png", filename):
            raise HTTPException(404, "题图不存在")
        path = services.settings.data_dir / "question-images" / filename
        if not path.is_file():
            raise HTTPException(404, "题图不存在")
        return FileResponse(path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/documents/{document_id}/retry", status_code=202)
    @app.post("/documents/{document_id}/reparse", status_code=202)
    def retry_document(document_id: str, tasks: BackgroundTasks):
        with services.lock, services.sessions() as db:
            doc = require(db, Document, document_id)
            queue_document(db, doc)
            db.commit()
            result = row(doc)
        tasks.add_task(services.ingest_document, document_id)
        return result

    @app.delete("/documents/{document_id}", status_code=204)
    def delete_document(document_id: str):
        try:
            if not services.remove_document(document_id):
                raise HTTPException(404, "资料不存在")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return Response(status_code=204)

    @app.get("/documents/{document_id}/practice")
    def practice_state(document_id: str, tasks: BackgroundTasks):
        result = practice.state(document_id)
        if result["status"] == "ready":
            result["answer_preparation"] = practice.uploaded_answers.schedule(document_id, tasks)
        return result

    @app.post("/documents/{document_id}/practice", status_code=202)
    def prepare_practice(document_id: str, tasks: BackgroundTasks):
        return practice.prepare(document_id, tasks)

    @app.put("/documents/{document_id}/practice/answer-pairing")
    def save_answer_pairing(document_id: str, payload: PracticeAnswerPairingInput):
        return practice.uploaded_answers.save_pairing(document_id, payload.mode, payload.answer_document_id)

    @app.get("/documents/{document_id}/practice/progress")
    def get_practice_progress(document_id: str):
        return practice.progress.get(document_id)

    @app.put("/documents/{document_id}/practice/progress")
    def save_practice_progress(document_id: str, payload: PracticeProgressInput):
        return practice.progress.save(document_id, payload.model_dump())

    @app.delete("/practice/questions/{question_id}", status_code=204)
    def delete_practice_question(question_id: str):
        practice.remove_question(question_id)
        return Response(status_code=204)

    @app.post("/practice/questions/{question_id}/answer")
    def practice_answer(question_id: str):
        return practice.answer(question_id)

    @app.post("/practice/questions/{question_id}/translation")
    def translate_practice(question_id: str):
        return practice.translate(question_id)

    @app.post("/practice/questions/{question_id}/submit")
    def submit_practice(question_id: str, payload: PracticeSubmission):
        return practice.answer(question_id, payload.selected)

    @app.post("/practice/questions/{question_id}/explanation")
    def practice_explanation(question_id: str):
        return practice.explanation(question_id)

    @app.post("/practice/questions/{question_id}/wrong")
    def mark_wrong_question(question_id: str, payload: WrongQuestionMark):
        return practice.mark_wrong(question_id, payload.own_answer, payload.selected)

    @app.get("/courses/{course_id}/wrong-questions")
    def wrong_questions(course_id: str):
        return practice.wrong_questions.list(course_id)

    @app.post("/courses/{course_id}/wrong-questions", status_code=201)
    def add_wrong_question(course_id: str, payload: WrongQuestionInput):
        return practice.wrong_questions.add(course_id, payload.stem, payload.own_answer, payload.answer)

    @app.post("/courses/{course_id}/wrong-questions/{identity}/reviews", status_code=201)
    def review_wrong_question(course_id: str, identity: str, payload: WrongQuestionReviewInput):
        return practice.wrong_questions.review(course_id, identity, payload.selected, payload.own_answer)

    @app.delete("/courses/{course_id}/wrong-questions/{identity}", status_code=204)
    def delete_wrong_question(course_id: str, identity: str):
        practice.wrong_questions.remove(course_id, identity)
        return Response(status_code=204)

    def chapter_rows(db, course_id):
        chapters = db.scalars(select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.position)).all()
        documents = {doc.id: doc for doc in db.scalars(select(Document).where(Document.course_id == course_id, Document.status == "ready"))}
        result = []
        for chapter in chapters:
            entries = list(db.execute(select(Block.id, Block.document_id).join(Document).where(
                Block.chapter_id == chapter.id, Document.status == "ready", Document.course_id == course_id,
            ).order_by(Document.created_at, Block.order)))
            block_ids = [entry[0] for entry in entries]
            source_docs = [documents[identity] for identity in {entry[1] for entry in entries}]
            result.append({**row(chapter), "block_ids": block_ids, "block_count": len(block_ids), "kind": chapter_role(source_docs)})
        return result

    @app.get("/courses/{course_id}/chapters")
    def get_chapters(course_id: str):
        with services.sessions() as db:
            require(db, Course, course_id)
            return chapter_rows(db, course_id)

    def require_chapter_idle(db, course_id):
        if db.scalar(select(Document.id).where(Document.course_id == course_id, Document.status.in_(["queued", "processing"]))):
            raise HTTPException(409, "资料正在处理，请完成后再调整章节目录")
        if db.scalar(select(Job.id).join(Outline).where(Outline.course_id == course_id, Job.status.in_(["queued", "running"]))):
            raise HTTPException(409, "复习笔记正在生成，请完成后再调整章节目录")
        session_ids = db.scalars(select(ChatSession.id).where(ChatSession.course_id == course_id))
        if any(services.chat_locks.get(identity) and services.chat_locks[identity].locked() for identity in session_ids):
            raise HTTPException(409, "课程会话正在回答，请停止或完成后再调整章节目录")

    @app.post("/courses/{course_id}/chapters/draft")
    def draft_chapters(course_id: str):
        with services.lock, services.sessions() as db:
            require(db, Course, course_id)
            require_chapter_idle(db, course_id)
            services.draft_chapters(db, course_id, reset=True)
            db.commit()
            try:
                services.sync_committed_chapters(course_id)
            except ValueError as exc:
                raise HTTPException(503, str(exc))
            return chapter_rows(db, course_id)

    @app.patch("/courses/{course_id}/chapters")
    def edit_chapters(course_id: str, payload: ChaptersInput):
        with services.lock, services.sessions() as db:
            course = require(db, Course, course_id)
            require_chapter_idle(db, course_id)
            if payload.expected_version is not None and payload.expected_version != course.version:
                raise HTTPException(409, "资料或目录已经变化，请根据刷新后的目录重新操作")
            existing = {c.id: c for c in db.scalars(select(Chapter).where(Chapter.course_id == course_id))}
            excluded = services.excluded_chapter_documents(db, course_id)
            ready_blocks = [block for block in db.scalars(select(Block).join(Document).where(Document.course_id == course_id, Document.status == "ready"))
                            if block.document_id not in excluded]
            by_id = {block.id: block for block in ready_blocks}
            assigned = [block_id for chapter in payload.chapters for block_id in chapter.block_ids]
            identities = [chapter.id for chapter in payload.chapters if chapter.id]
            if len(identities) != len(set(identities)) or any(identity not in existing for identity in identities):
                raise HTTPException(422, "章节 ID 不属于当前课程或重复")
            if len(assigned) != len(set(assigned)) or not set(assigned).issubset(by_id):
                raise HTTPException(422, "原文块不属于当前课程、不可用或被重复分配")
            if payload.confirm and set(assigned) != set(by_id):
                raise HTTPException(422, "确认目录前，请为全部可用原文分配章节；无法归类的可放入未归类资料")
            ready_docs = {doc.id: doc for doc in db.scalars(select(Document).where(Document.course_id == course_id, Document.status == "ready"))}
            file_blocks = {}
            for block in ready_blocks:
                file_blocks.setdefault(block.document_id, set()).add(block.id)
            for item in payload.chapters:
                owners = {by_id[identity].document_id for identity in item.block_ids}
                if not owners:
                    # Retain existing mappings for failed files so retry can restore their chapter.
                    if item.id and not any(block.chapter_id == item.id for block in ready_blocks):
                        continue
                    raise HTTPException(422, "章节必须包含可用原文")
                expected_blocks = set().union(*(file_blocks[identity] for identity in owners))
                if set(item.block_ids) != expected_blocks:
                    raise HTTPException(422, "每份文件的原文应完整保留在同一章节中")
                if len(owners) > 1 and any(document_role(ready_docs[identity]) != "knowledge" for identity in owners):
                    raise HTTPException(422, "只能合并知识章节；提纲、试卷和答案需保留独立归属")
            original_owners = {block.id: block.chapter_id for block in ready_blocks}
            # Reassign ready blocks; unavailable files retain their chapter or follow its merge.
            for block in ready_blocks:
                block.chapter_id = None
            kept = set()
            destinations = {}
            ordered = sorted(enumerate(payload.chapters), key=lambda pair: (pair[1].position, pair[0]))
            for position, (_, item) in enumerate(ordered):
                chapter = existing[item.id] if item.id else Chapter(id=new_id(), course_id=course_id)
                chapter.title, chapter.position, chapter.confirmed = item.title, position, payload.confirm
                db.add(chapter)
                db.flush()
                kept.add(chapter.id)
                for block_id in item.block_ids:
                    by_id[block_id].chapter_id = chapter.id
                    previous = original_owners[block_id]
                    if previous:
                        destinations.setdefault(previous, set()).add(chapter.id)
            for block in db.scalars(select(Block).join(Document).where(Document.course_id == course_id, Document.status != "ready")):
                if block.chapter_id and block.chapter_id not in kept:
                    targets = destinations.get(block.chapter_id, set())
                    block.chapter_id = next(iter(targets)) if len(targets) == 1 else None
            db.flush()
            for identity, chapter in existing.items():
                if identity not in kept:
                    db.delete(chapter)
            services.changed(db, course_id, unconfirm=False)
            db.flush()
            db.commit()
            try:
                services.sync_committed_chapters(course_id)
            except ValueError as exc:
                raise HTTPException(503, str(exc))
            return chapter_rows(db, course_id)

    @app.delete("/courses/{course_id}/chapters/{chapter_id}")
    def delete_chapter(course_id: str, chapter_id: str, expected_version: int | None = None):
        with services.lock, services.sessions() as db:
            course = require(db, Course, course_id)
            chapter = require(db, Chapter, chapter_id)
            if chapter.course_id != course_id:
                raise HTTPException(404, "章节不属于当前课程")
            require_chapter_idle(db, course_id)
            if expected_version is not None and expected_version != course.version:
                raise HTTPException(409, "资料或目录已经变化，请根据刷新后的目录重新操作")
            blocks = db.scalars(select(Block).join(Document).where(Document.course_id == course_id, Block.chapter_id == chapter_id)).all()
            document_ids = {block.document_id for block in blocks}
            source_docs = db.scalars(select(Document).where(Document.id.in_(document_ids))).all()
            if chapter_role(source_docs) != "knowledge":
                raise HTTPException(422, "只能删除知识章节，辅助资料和题目保留独立归属")
            services.set_excluded_chapter_documents(db, course_id, services.excluded_chapter_documents(db, course_id) | document_ids)
            for block in blocks:
                block.chapter_id = None
            db.flush()
            db.delete(chapter)
            db.flush()
            remaining = db.scalars(select(Chapter).where(Chapter.course_id == course_id).order_by(Chapter.position)).all()
            for position, item in enumerate(remaining):
                item.position = position
            services.changed(db, course_id, unconfirm=False)
            db.commit()
            try:
                services.sync_committed_chapters(course_id)
            except ValueError as exc:
                raise HTTPException(503, str(exc))
            return chapter_rows(db, course_id)

    @app.post("/courses/{course_id}/index/repair")
    def repair_index(course_id: str):
        with services.sessions() as db:
            require(db, Course, course_id)
        try:
            result = services.rebuild_index()
        except ValueError as exc:
            raise HTTPException(503, str(exc))
        return {"message": f"已重新建立检索索引：{result['documents']} 份资料，{result['chunks']} 个检索片段。可以再次提问。",
                **result}

    @app.get("/courses/{course_id}/search")
    def search(course_id: str, query: str, kind: Literal["material", "exam"] = "material", chapter_id: str | None = None,
               year: int | None = None, limit: int = 5):
        with services.sessions() as db:
            require(db, Course, course_id)
            if chapter_id and require(db, Chapter, chapter_id).course_id != course_id:
                raise HTTPException(422, "章节不属于当前课程")
        try:
            return {"sources": services.retrieval.search(course_id, query[:1500], kind, chapter_id, year, min(20, max(1, limit)))}
        except ValueError as exc:
            raise HTTPException(503, str(exc))

    @app.get("/sources/{source_id}")
    def source(source_id: str):
        identity = source_id[1:] if source_id.startswith("S") else source_id
        with services.sessions() as db:
            result = services.retrieval.source(db, identity)
            if result is None:
                raise HTTPException(404, "引用资料不存在、已删除或未完成入库")
            return result

    @app.get("/courses/{course_id}/sessions")
    def sessions(course_id: str):
        with services.sessions() as db:
            require(db, Course, course_id)
            return [row(s) for s in db.scalars(select(ChatSession).where(ChatSession.course_id == course_id).order_by(ChatSession.created_at.desc()))]

    @app.post("/courses/{course_id}/sessions", status_code=201)
    def create_session(course_id: str, payload: SessionInput | None = None):
        with services.lock, services.sessions() as db:
            require(db, Course, course_id)
            chat_session = ChatSession(course_id=course_id, title=(payload.title if payload else "新复习对话"))
            db.add(chat_session)
            db.commit()
            return row(chat_session)

    @app.patch("/sessions/{session_id}")
    def rename_session(session_id: str, payload: SessionNameInput):
        with services.lock, services.sessions() as db:
            session = require(db, ChatSession, session_id)
            lock = services.chat_locks.get(session_id)
            if lock and lock.locked():
                raise HTTPException(409, "会话正在回答，请停止或完成后再改名")
            session.title = payload.title
            db.commit()
            return row(session)

    @app.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str):
        with services.lock, services.sessions() as db:
            session = require(db, ChatSession, session_id)
            lock = services.chat_locks.get(session_id)
            if lock and lock.locked():
                raise HTTPException(409, "会话正在回答，请停止或完成后再删除")
            db.execute(delete(Message).where(Message.session_id == session_id))
            db.delete(session)
            db.commit()
            services.chat_locks.pop(session_id, None)
        return Response(status_code=204)

    @app.get("/sessions/{session_id}/messages")
    def messages(session_id: str):
        with services.sessions() as db:
            require(db, ChatSession, session_id)
            return [{**row(m), "content": re.sub(r"\[S[a-fA-F0-9]{32}\]", "", m.content) if m.role == "assistant" else m.content,
                     "practice_context": next((c["practice_context"] for c in (m.citations or []) if "practice_context" in c), None) if m.role == "user" else None,
                     "note_context": next((c["note_context"] for c in (m.citations or []) if "note_context" in c), None) if m.role == "user" else None,
                     "citations": []} for m in db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at))]

    @app.post("/sessions/{session_id}/chat")
    def chat(session_id: str, payload: ChatInput):
        practice_context, note_context = None, None
        with services.lock, services.sessions() as db:
            session = require(db, ChatSession, session_id)
            if sum(bool(value) for value in (payload.practice_question_id, payload.wrong_question_id, payload.note_context)) > 1:
                raise HTTPException(422, "一次提问只能携带一道题目或一段笔记")
            if payload.wrong_question_id:
                practice_context = practice.wrong_questions.chat_context(
                    db, session.course_id, payload.wrong_question_id, payload.selected, payload.own_answer)
            if payload.note_context:
                target = payload.note_context
                note = require(db, Outline, target.outline_id)
                if note.course_id != session.course_id:
                    raise HTTPException(422, "所选笔记不属于当前课程")
                lines = note.markdown.splitlines()
                if not 0 <= target.start_line < target.end_line <= len(lines):
                    raise HTTPException(422, "所选段落已变化，请重新选择")
                excerpt = "\n".join(lines[target.start_line:target.end_line])
                if excerpt != target.source_text:
                    raise HTTPException(409, "笔记段落已更新，请重新选择需要解释的内容")
                if not excerpt.strip() or len(excerpt.encode("utf-8")) > 4500:
                    raise HTTPException(422, "所选段落过长，请缩小选择范围后再解释")
                heading = next((re.sub(r"^#{1,6}\s+", "", line).strip() for line in reversed(lines[:target.start_line + 1])
                                if re.match(r"^#{1,6}\s+", line)), "复习笔记")
                note_context = {"outline_id": note.id, "heading": heading, "text": excerpt,
                                "selected_text": target.selected_text, "start_line": target.start_line,
                                "end_line": target.end_line, "stale": note.version != db.get(Course, session.course_id).version}
            if payload.practice_question_id:
                q = require(db, PracticeQuestion, payload.practice_question_id)
                doc = require(db, Document, q.document_id)
                bank = db.get(PracticeSet, doc.id)
                if doc.course_id != session.course_id:
                    raise HTTPException(422, "当前题目不属于这个会话的课程")
                if doc.status != "ready" or bank is None or bank.status != "ready":
                    raise HTTPException(409, "题目资料正在处理，请完成后再提问")
                keys = {o["key"] for o in q.options}
                if any(key not in keys for key in payload.selected):
                    raise HTTPException(422, "当前题目的选项已变化，请刷新题目")
                practice_context = {"question_id": q.id, "document_id": doc.id, "filename": doc.filename, "kind": doc.kind,
                                    "number": str(q.position + 1), "original_number": q.number, "section": q.section,
                                    "stem": q.stem, "options": q.options, "locator": q.locator,
                                    "selected": payload.selected, "own_answer": payload.own_answer}
                course = db.get(Course, doc.course_id)
                if (q.resolution_version == course.version and image_resolution_current(q, q.resolution)
                        and answer_policy_current(q.resolution)):
                    practice_context["reference_answer"] = {key: q.resolution[key] for key in
                                                            ("answer", "choices", "origin", "source") if key in q.resolution}
            if db.get(AppMeta, "index_repair:" + session.course_id):
                raise HTTPException(503, "本地检索索引需要重建，请先点击“重新建立索引”。资料和复习笔记仍已保存。")
            if not services.llm.configured:
                raise HTTPException(503, "请在页面右上角“模型配置”中填写模型连接信息并保存")
            lock = services.chat_locks.setdefault(session_id, Lock())
            if not lock.acquire(blocking=False):
                raise HTTPException(409, "当前会话仍在回答，请等待或停止后再试")

        def generate():
            try:
                yield from agent.chat(session_id, payload.message, practice_context, note_context)
            finally:
                lock.release()
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/courses/{course_id}/outlines")
    def list_outlines(course_id: str):
        with services.sessions() as db:
            course = require(db, Course, course_id)
            result = []
            for outline in db.scalars(select(Outline).where(Outline.course_id == course_id).order_by(Outline.created_at.desc())):
                job = db.scalar(select(Job).where(Job.outline_id == outline.id).order_by(Job.created_at.desc()))
                result.append({"id": outline.id, "status": outline.status, "stale": course.version != outline.version,
                               "style": outline.snapshot.get("outline_style", "legacy"),
                               "created_at": outline.created_at, "job_id": job.id if job else None})
            return result

    @app.post("/courses/{course_id}/outlines", status_code=202)
    def generate_outline(course_id: str, tasks: BackgroundTasks):
        with services.lock, services.sessions() as db:
            course = require(db, Course, course_id)
            chapters = db.scalars(select(Chapter).where(Chapter.course_id == course_id)).all()
            if not chapters or any(not chapter.confirmed for chapter in chapters):
                raise HTTPException(409, "请先确认章节目录和全部原文归属")
            if db.scalar(select(Document.id).where(Document.course_id == course_id, Document.status.in_(["queued", "processing"]))):
                raise HTTPException(409, "请等待资料处理完成后生成复习笔记")
            if not services.llm.configured:
                raise HTTPException(503, "请先在页面右上角“模型配置”中接入真实聊天模型")
            active = db.scalar(select(Job.id).join(Outline).where(Outline.course_id == course_id, Job.status.in_(["queued", "running"])))
            if active:
                raise HTTPException(409, "本课程已有复习笔记任务正在运行")
            snapshot = outline_service.snapshot(db, course_id)
            if not snapshot["blocks"]:
                raise HTTPException(409, "没有成功入库的原文")
            ids = {chapter.id for chapter in chapters}
            if any(block["chapter_id"] not in ids for block in snapshot["blocks"]):
                raise HTTPException(409, "部分资料尚未分配章节")
            outline = Outline(id=new_id(), course_id=course_id, version=course.version, snapshot=snapshot)
            job = Job(id=new_id(), outline_id=outline.id)
            db.add(outline)
            db.flush()
            db.add(job)
            db.commit()
            result = {"job_id": job.id, "outline_id": outline.id}
        tasks.add_task(outline_service.run, job.id)
        return result

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        with services.sessions() as db:
            return row(require(db, Job, job_id))

    @app.post("/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, tasks: BackgroundTasks):
        with services.lock, services.sessions() as db:
            job = require(db, Job, job_id)
            outline = require(db, Outline, job.outline_id)
            course = require(db, Course, outline.course_id)
            if job.status not in ("failed", "interrupted"):
                raise HTTPException(409, "只有失败或中断的任务可以重试")
            if course.version != outline.version:
                raise HTTPException(409, "资料或目录已经变化，请确认目录并生成新的复习笔记")
            active = db.scalar(select(Job.id).join(Outline).where(
                Outline.course_id == outline.course_id, Job.status.in_(["queued", "running"])).limit(1))
            if active:
                raise HTTPException(409, "本课程已有复习笔记任务正在运行，请完成后再重试")
            if db.scalar(select(Document.id).where(
                    Document.course_id == outline.course_id, Document.status.in_(["queued", "processing"])).limit(1)):
                raise HTTPException(409, "请等待资料处理完成后再重试复习笔记")
            if not services.llm.configured:
                raise HTTPException(503, "请先配置聊天模型")
            job.status, job.error, outline.status = "queued", None, "queued"
            db.commit()
            result = {"job_id": job.id, "outline_id": outline.id}
        tasks.add_task(outline_service.run, job_id)
        return result

    @app.get("/outlines/{outline_id}")
    def get_outline(outline_id: str):
        with services.sessions() as db:
            outline = require(db, Outline, outline_id)
            course = require(db, Course, outline.course_id)
            job = db.scalar(select(Job).where(Job.outline_id == outline_id).order_by(Job.created_at.desc()))
            return {**row(outline), "stale": course.version != outline.version,
                    "style": outline.snapshot.get("outline_style", "legacy"), "current_style": NOTES_STYLE,
                    "job_id": job.id if job else None}

    @app.get("/outlines/{outline_id}/export")
    def export_outline(outline_id: str):
        with services.sessions() as db:
            outline = require(db, Outline, outline_id)
            if outline.status != "completed":
                raise HTTPException(409, "复习笔记尚未生成完成")
            course = require(db, Course, outline.course_id)
            filename = quote(course.name + "-复习笔记.md", safe="")
            return Response(outline.markdown, media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})

    @app.delete("/outlines/{outline_id}", status_code=204)
    def delete_outline(outline_id: str):
        with services.lock, services.sessions() as db:
            outline = require(db, Outline, outline_id)
            course = require(db, Course, outline.course_id)
            if course.version == outline.version:
                raise HTTPException(409, "只支持删除已过期的复习笔记")
            if outline.status in ("queued", "running") or db.scalar(select(Job.id).where(Job.outline_id == outline_id, Job.status.in_(["queued", "running"]))):
                raise HTTPException(409, "这份笔记正在生成，请完成后再删除")
            db.execute(delete(Job).where(Job.outline_id == outline_id))
            db.delete(outline)
            db.commit()
        return Response(status_code=204)

    return app


app = create_app()
