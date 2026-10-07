"""Question-specific answer evidence for course chat; never infer paper identity."""
import re

from .answer_matching import compact, number, section_order


def asks_for_question_answer(question):
    return bool(re.search(r"第\s*\d+\s*题|\d+\s*题|这(?:道)?题|那(?:道)?题|标准答案|正确答案|答案|解题|解答|试卷|\b(?:answer|solution|question|problem|exam)\b", question, re.I))


class ChatAnswerGrounding:
    def __init__(self, practice):
        self.practice = practice

    def current_question(self, context):
        if not context or not context.get("document_id") or not context.get("question_id"):
            return context
        reference = context.get("reference_answer", {})
        if reference.get("origin") in ("uploaded", "embedded"):
            return context
        for question, resolution in self.practice.uploaded_answers.chat_matches(context["document_id"]):
            if (question.id == context["question_id"] and question.stem == context["stem"]
                    and question.options == context["options"]):
                return {**context, "reference_answer": {key: resolution[key] for key in
                                                       ("answer", "choices", "origin", "source") if key in resolution}}
        return context

    def sources(self, course_id, sources, current_question=None, question=""):
        target_numbers = {str(int(value)) for match in re.findall(r"(?:第\s*)?(\d+)\s*题|\b(?:question|problem)\s*(?:no\.?\s*)?(\d+)\b", question, re.I)
                          for value in match if value}
        target_numbers.update(value for text in re.findall(r"第\s*([一二三四五六七八九十]+)\s*题", question)
                              if (value := section_order(text + "、")))
        target_years = {int(value) for value in re.findall(r"(?<!\d)((?:19|20)\d{2})\s*年", question)}
        named_papers = {s.get("document_id") for s in sources if s["kind"] in ("exam", "exercise")
                        and s["filename"].rsplit('.', 1)[0] in question}
        question_sources = [s for s in sources if s["kind"] in ("exam", "exercise")]
        if not question_sources and not current_question and not asks_for_question_answer(question):
            # A standalone answer-file summary is allowed, but cannot establish
            # correspondence to a question that has not been identified.
            return [{**s, "answer_correspondence": "unverified"} if s["kind"] == "answer" else s
                    for s in sources]
        by_document = {}
        verified = []
        for source in sources:
            if source.get("course_id", course_id) != course_id:
                continue
            if source["kind"] == "answer":
                # Supply only the scoped answer below, never a whole answer page
                # containing answers to unrelated papers or question numbers.
                continue
            value = dict(source)
            if source["kind"] in ("exam", "exercise"):
                document_id = source.get("document_id")
                year = source.get("year")
                if year is None:
                    filename_years = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", source["filename"])
                    year = int(filename_years[0]) if len(filename_years) == 1 else None
                if document_id not in by_document:
                    by_document[document_id] = self.practice.uploaded_answers.chat_matches(document_id) if document_id else []
                visible = compact(source["text"] + "\n" + "\n".join(c["text"] for c in source.get("context", [])))
                answers = []
                for paper_question, resolution in by_document[document_id]:
                    if current_question and paper_question.id != current_question.get("question_id"):
                        continue
                    if not current_question and (target_numbers and number(paper_question.number) not in target_numbers
                                                 or named_papers and document_id not in named_papers
                                                 or target_years and year not in target_years):
                        continue
                    stem = compact(paper_question.stem)
                    if not stem or stem not in visible or (not current_question and len(stem) < 12):
                        continue
                    answers.append({"question_id": paper_question.id, "document_id": document_id,
                                    "number": paper_question.number, "section": paper_question.section,
                                    "stem": paper_question.stem, "options": paper_question.options,
                                    "reference_answer": {key: resolution[key] for key in
                                                         ("answer", "choices", "origin", "source") if key in resolution}})
                value["matched_answers"] = answers
                value["answer_correspondence"] = "question_specific" if answers else "unverified"
            verified.append(value)
        return verified
