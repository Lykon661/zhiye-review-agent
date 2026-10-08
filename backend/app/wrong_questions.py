"""Course-scoped wrong-question snapshots; saving never calls a model."""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from .models import Course, PracticeTranslation, WrongQuestion, WrongQuestionReview, new_id, now
from .math_formatting import plain_math_format
from .practice_progress import grading


class WrongQuestionService:
    def __init__(self, services):
        self.s = services

    @staticmethod
    def signature(question):
        payload = {"document_id": question.document_id, "number": question.number,
                   "section": question.section, "question_type": question.question_type,
                   "stem": question.stem, "options": question.options}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def row(record):
        return {column.name: getattr(record, column.name) for column in record.__table__.columns
                if column.name != "signature"}

    @staticmethod
    def require_course(db, course_id):
        if db.get(Course, course_id) is None:
            raise HTTPException(404, "课程不存在或已删除")

    def find(self, db, course_id, question):
        return db.scalar(select(WrongQuestion).where(
            WrongQuestion.course_id == course_id, WrongQuestion.signature == self.signature(question)))

    def refresh_formatting(self, db, document, questions):
        """Reconnect existing snapshots only when source text differs by math formatting.

        The user's original attempt stays in the notebook. A reference computed
        from flattened formulas is discarded so the corrected question can be graded again.
        """
        def key(question):
            return (str(question.get("number", "")), plain_math_format(question.get("section", "")),
                    question.get("question_type"), plain_math_format(question.get("stem", "")),
                    tuple((option["key"], plain_math_format(option["text"])) for option in question.get("options", [])))

        candidates = {}
        for question in questions:
            value = {"number": question.number, "section": question.section,
                     "question_type": question.question_type, "stem": question.stem, "options": question.options}
            candidates.setdefault(key(value), []).append(question)
        records = list(db.scalars(select(WrongQuestion).where(
            WrongQuestion.course_id == document.course_id, WrongQuestion.source_document_id == document.id)))
        signatures = {record.signature: record.id for record in records}
        for record in records:
            matches = candidates.get(key(record.question), [])
            if len(matches) != 1:
                continue
            question = matches[0]
            signature = self.signature(question)
            if signature in signatures and signatures[signature] != record.id:
                continue
            changed = (record.question.get("stem") != question.stem or record.question.get("options") != question.options)
            record.signature, record.source_question_id = signature, question.id
            record.question = {**record.question, "stem": question.stem, "options": question.options,
                               "section": question.section, "number": question.number,
                               "locator": question.locator, "filename": document.filename, "year": document.year,
                               "translation": None if changed else record.question.get("translation")}
            if changed:
                record.resolution = {}
            signatures[signature] = record.id

    def save_practice(self, db, question, document, *, resolution=None, selected=None,
                      own_answer=None, incorrect=False, reference_mismatch=False, create=True, translation_signature=None):
        record = self.find(db, document.course_id, question)
        if record is None and not create:
            return None
        # Finish all reads before attaching a new record: querying translations
        # can autoflush pending objects whose required snapshot is still unset.
        translation = db.get(PracticeTranslation, (document.course_id, translation_signature)) if translation_signature else None
        snapshot = {"number": question.number, "section": question.section,
                    "question_type": question.question_type, "stem": question.stem,
                    "options": question.options, "locator": question.locator,
                    "filename": document.filename, "year": document.year,
                    "translation": translation.content if translation else None}
        if record is None:
            record = WrongQuestion(id=new_id(), course_id=document.course_id,
                                   signature=self.signature(question), reason="manual", question=snapshot)
        else:
            record.question = snapshot
        record.source_document_id, record.source_question_id = document.id, question.id
        if resolution:
            record.resolution = resolution
        if incorrect:
            record.reason, record.selected = "incorrect", selected or []
        elif reference_mismatch:
            record.reason, record.selected = "incorrect", selected or []
        elif selected is not None and record.reason not in ("incorrect", "reference"):
            record.selected = selected
        if own_answer is not None:
            record.own_answer = own_answer
        record.updated_at = now()
        db.add(record)
        return record

    def list(self, course_id):
        with self.s.sessions() as db:
            self.require_course(db, course_id)
            records = list(db.scalars(select(WrongQuestion).where(
                WrongQuestion.course_id == course_id).order_by(WrongQuestion.updated_at.desc(), WrongQuestion.id)))
            reviews = {}
            for attempt in db.scalars(select(WrongQuestionReview).join(WrongQuestion).where(
                    WrongQuestion.course_id == course_id).order_by(WrongQuestionReview.created_at.desc(), WrongQuestionReview.id)):
                reviews.setdefault(attempt.wrong_question_id, []).append(
                    {"id": attempt.id, "created_at": attempt.created_at, **attempt.content})
            return [{**self.row(record), "reviews": reviews.get(record.id, [])} for record in records]

    def review(self, course_id, identity, selected, own_answer):
        with self.s.lock, self.s.sessions() as db:
            self.require_course(db, course_id)
            record = db.get(WrongQuestion, identity)
            if record is None or record.course_id != course_id:
                raise HTTPException(404, "错题不存在或已删除")
            question = record.question
            choice = question["question_type"] in ("single_choice", "multiple_choice")
            keys = {option["key"] for option in question.get("options", [])}
            if choice:
                if (not selected or len(set(selected)) != len(selected) or not set(selected).issubset(keys)
                        or question["question_type"] == "single_choice" and len(selected) != 1):
                    raise HTTPException(422, "请选择有效选项；单选题只能选一项，多选题至少选一项")
            elif selected or not own_answer.strip():
                raise HTTPException(422, "请填写本次作答，非选择题不支持自动判分")
            resolution = dict(record.resolution or {})
            labels = resolution.get("choices", [])
            comparable = (choice and resolution.get("origin") in ("uploaded", "embedded", "model")
                          and resolution.get("resolved") is not False and bool(labels)
                          and set(labels).issubset(keys)
                          and (question["question_type"] != "single_choice" or len(labels) == 1))
            assessment = grading(resolution, selected) if comparable else {
                "correct": None, "reference_match": None, "grading_basis": None}
            content = {"selected": selected, "own_answer": own_answer.strip(), **assessment,
                       "resolved": bool(comparable), "resolution": resolution, "question": dict(question)}
            attempt = WrongQuestionReview(id=new_id(), wrong_question_id=record.id, content=content)
            db.add(attempt)
            self.s.memory.record_attempt(
                db, course_id, {**question, "id": record.source_question_id},
                {**assessment, "selected": selected, "own_answer": own_answer.strip()}, source_key="review:" + attempt.id,
                document_id=record.source_document_id, wrong_id=record.id, kind="review")
            db.commit()
            self.s.memory.wake.set()
            return {"id": attempt.id, "created_at": attempt.created_at, **content}

    def chat_context(self, db, course_id, identity, selected=None, own_answer=None):
        record = db.get(WrongQuestion, identity)
        if record is None or record.course_id != course_id:
            raise HTTPException(404, "错题不存在或不属于当前课程")
        q = record.question
        values = record.selected if selected is None else selected
        if not set(values).issubset({o["key"] for o in q.get("options", [])}):
            raise HTTPException(422, "错题选项已变化，请重新选择")
        context = {"wrong_question_id": record.id, "question_id": record.source_question_id,
                   "document_id": record.source_document_id, "filename": q["filename"], "kind": "exercise",
                   "number": q.get("number") or "手动录入", "original_number": q.get("number", ""),
                   "section": q.get("section", ""), "stem": q["stem"], "options": q.get("options", []),
                   "locator": q.get("locator", {}), "selected": values,
                   "own_answer": record.own_answer if own_answer is None else own_answer}
        if record.resolution:
            context["reference_answer"] = {key: record.resolution[key] for key in
                                           ("answer", "choices", "origin", "source") if key in record.resolution}
        return context

    def add(self, course_id, stem, own_answer, answer):
        with self.s.lock, self.s.sessions() as db:
            self.require_course(db, course_id)
            record = WrongQuestion(id=new_id(), course_id=course_id, signature="manual:" + new_id(),
                                   question={"number": "", "section": "", "question_type": "other",
                                             "stem": stem, "options": [], "locator": {},
                                             "filename": "手动录入", "year": None, "translation": None},
                                   own_answer=own_answer,
                                   resolution={"answer": answer, "choices": [], "origin": "manual",
                                               "source": "手动填写", "sources": []} if answer else {})
            db.add(record)
            db.flush()
            self.s.memory.record_attempt(
                db, course_id, record.question, {"own_answer": own_answer},
                source_key="manual_wrong:" + record.id, wrong_id=record.id, kind="manual")
            db.commit()
            self.s.memory.wake.set()
            return self.row(record)

    def remove(self, course_id, identity):
        with self.s.lock, self.s.sessions() as db:
            self.require_course(db, course_id)
            record = db.get(WrongQuestion, identity)
            if record is None or record.course_id != course_id:
                raise HTTPException(404, "错题不存在或已删除")
            db.delete(record)
            db.commit()
