"""Automatic local progress; only previously requested answers may be restored."""
import json

from fastapi import HTTPException
from sqlalchemy import select

from .models import Course, PracticeProgress, PracticeQuestion, PracticeSet, now
from .model_images import image_resolution_current

ANSWER_POLICY = "source_and_choices_v7_inline_choice"


def answer_policy_current(resolution):
    return bool(resolution) and (resolution.get("origin") not in ("uploaded", "embedded", "model")
                                 or resolution.get("answer_policy") == ANSWER_POLICY)


def grading(result, selected):
    comparable = result.get("resolved") is not False and bool(result.get("choices"))
    matches = set(selected) == set(result.get("choices", [])) if selected is not None and comparable else None
    uploaded = result.get("origin") in ("uploaded", "embedded")
    return {"correct": matches if uploaded else None,
            "reference_match": matches if not uploaded else None,
            "grading_basis": result.get("origin") if uploaded else "model"}


class PracticeProgressService:
    def __init__(self, practice):
        self.p, self.s = practice, practice.s

    def context(self, db, document_id):
        doc = self.p._document(db, document_id)
        bank = db.get(PracticeSet, document_id)
        if bank is None or bank.status != "ready" or bank.signature != self.p._signature(self.p._blocks(db, document_id)):
            raise HTTPException(409, "题目已更新，请等待重新整理后再保存作答")
        questions = list(db.scalars(select(PracticeQuestion).where(
            PracticeQuestion.document_id == document_id).order_by(PracticeQuestion.position)))
        signature = self.p._signature({"bank": bank.signature, "questions": [q.id for q in questions]})
        return doc, questions, signature

    def record(self, db, document_id, signature):
        saved = db.get(PracticeProgress, document_id)
        if saved is None:
            saved = PracticeProgress(document_id=document_id, signature=signature, state={}, attempts={}, revision=0)
            db.add(saved)
        elif saved.signature != signature:
            saved.signature, saved.state, saved.attempts, saved.revision = signature, {}, {}, 0
        return saved

    def get(self, document_id):
        with self.s.lock, self.s.sessions() as db:
            doc, questions, signature = self.context(db, document_id)
            saved = db.get(PracticeProgress, document_id)
            if saved is None or saved.signature != signature:
                return {"signature": signature, "revision": 0, "state": {}, "results": {}, "updated_at": None}
            version = db.get(Course, doc.course_id).version
            results = {}
            for q in questions:
                attempt = saved.attempts.get(q.id)
                if (not attempt or attempt.get("version") != version or not q.resolution
                        or q.resolution_version != version
                        or attempt.get("resolution_signature") != self.p._signature(q.resolution)):
                    continue
                if not answer_policy_current(q.resolution):
                    continue
                if not image_resolution_current(q, q.resolution):
                    continue
                selected = attempt.get("selected")
                if selected is not None and set(selected) != set(saved.state.get("selections", {}).get(q.id, [])):
                    continue
                results[q.id] = {**q.resolution, **grading(q.resolution, selected), "selected": selected,
                                 "wrong_question_id": None}
            return {"signature": signature, "revision": saved.revision, "state": saved.state,
                    "results": results, "updated_at": saved.updated_at}

    def save(self, document_id, payload):
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
            raise HTTPException(413, "作答内容过长，无法保存练习进度")
        with self.s.lock, self.s.sessions() as db:
            _, questions, signature = self.context(db, document_id)
            if payload["signature"] != signature:
                raise HTTPException(409, "题目已重新整理，旧作答不会覆盖新的题目")
            by_id = {q.id: q for q in questions}
            current = payload["current_question_id"]
            if current is not None and current not in by_id:
                raise HTTPException(422, "当前题目不属于这份练习")
            selections, drafts = payload["selections"], payload["drafts"]
            if any(key not in by_id for key in (*selections, *drafts)):
                raise HTTPException(422, "作答包含已删除或其他试卷的题目")
            for key, labels in selections.items():
                q = by_id[key]
                allowed = {o["key"] for o in q.options}
                if (not set(labels).issubset(allowed) or len(set(labels)) != len(labels)
                        or (q.question_type != "multiple_choice" and len(labels) > 1)):
                    raise HTTPException(422, "作答选项与原题不一致")
            saved = self.record(db, document_id, signature)
            if payload["revision"] > saved.revision:
                saved.state = {"current_question_id": current, "selections": selections, "drafts": drafts}
                saved.revision, saved.updated_at = payload["revision"], now()
                db.commit()
            return {"revision": saved.revision, "updated_at": saved.updated_at}

    def submitted(self, db, doc, q, result, selected, version):
        _, _, signature = self.context(db, doc.id)
        saved = self.record(db, doc.id, signature)
        state = dict(saved.state)
        if selected is not None:
            state["selections"] = {**state.get("selections", {}), q.id: selected}
        state.setdefault("current_question_id", q.id)
        saved.state = state
        saved.attempts = {**saved.attempts, q.id: {"selected": selected, "version": version,
                          "resolution_signature": self.p._signature(result)}}
        saved.updated_at = now()
