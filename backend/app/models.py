from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("course_id", "file_hash"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    filename: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_hash: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    units_total: Mapped[int] = mapped_column(Integer, default=0)
    units_with_text: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Chapter(Base):
    __tablename__ = "chapters"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String)
    position: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class Block(Base):
    __tablename__ = "blocks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    chapter_id: Mapped[str | None] = mapped_column(ForeignKey("chapters.id"), nullable=True, index=True)
    order: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict] = mapped_column(JSON)
    heading: Mapped[str | None] = mapped_column(String, nullable=True)
    block_type: Mapped[str] = mapped_column(String, default="text")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class ChatSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String, default="新复习对话")
    created_at: Mapped[str] = mapped_column(String, default=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Outline(Base):
    __tablename__ = "outlines"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="queued")
    markdown: Mapped[str] = mapped_column(Text, default="")
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    chapter_results: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    outline_id: Mapped[str] = mapped_column(ForeignKey("outlines.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    stage: Mapped[str] = mapped_column(String, default="等待生成")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class AppMeta(Base):
    __tablename__ = "app_meta"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class PracticeSet(Base):
    __tablename__ = "practice_sets"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    signature: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PracticeQuestion(Base):
    __tablename__ = "practice_questions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("practice_sets.document_id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    number: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String, default="")
    question_type: Mapped[str] = mapped_column(String)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    locator: Mapped[dict] = mapped_column(JSON, default=dict)
    uploaded_answer: Mapped[str] = mapped_column(Text, default="")
    uploaded_choices: Mapped[list] = mapped_column(JSON, default=list)
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution_version: Mapped[int] = mapped_column(Integer, default=-1)


class PracticeTranslation(Base):
    __tablename__ = "practice_translations"
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    signature: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)


class PracticeProgress(Base):
    __tablename__ = "practice_progress"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    signature: Mapped[str] = mapped_column(String)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class PracticeAnswerIndex(Base):
    __tablename__ = "practice_answer_indexes"
    document_id: Mapped[str] = mapped_column(ForeignKey("practice_sets.document_id", ondelete="CASCADE"), primary_key=True)
    signature: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="processing")
    progress: Mapped[float] = mapped_column(Float, default=0)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WrongQuestion(Base):
    __tablename__ = "wrong_questions"
    __table_args__ = (UniqueConstraint("course_id", "signature"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    signature: Mapped[str] = mapped_column(String)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    source_question_id: Mapped[str | None] = mapped_column(
        ForeignKey("practice_questions.id", ondelete="SET NULL"), nullable=True, index=True)
    # Independent snapshots survive question extraction and source deletion.
    question: Mapped[dict] = mapped_column(JSON)
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    selected: Mapped[list] = mapped_column(JSON, default=list)
    own_answer: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(String, default="manual")
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class WrongQuestionReview(Base):
    __tablename__ = "wrong_question_reviews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    wrong_question_id: Mapped[str] = mapped_column(
        ForeignKey("wrong_questions.id", ondelete="CASCADE"), index=True)
    # Keep each attempt and its assessment independent of the original mistake.
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)


class SessionMemory(Base):
    __tablename__ = "session_memories"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String, default="")
    summary_cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    preference_cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    pending: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class MemoryPreference(Base):
    __tablename__ = "memory_preferences"
    __table_args__ = (UniqueConstraint("scope", "key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String, default="global")
    key: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=True)
    source_quote: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String, default=now)


class LearningEvent(Base):
    __tablename__ = "learning_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    source_key: Mapped[str] = mapped_column(String, unique=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    source_wrong_id: Mapped[str | None] = mapped_column(ForeignKey("wrong_questions.id", ondelete="CASCADE"), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=True)
    topic: Mapped[str] = mapped_column(String)
    content: Mapped[dict] = mapped_column(JSON)
    topic_pending: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    forgotten: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)
