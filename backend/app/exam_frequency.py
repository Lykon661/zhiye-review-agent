import re
import unicodedata

from sqlalchemy import select

from .file_chapters import file_title
from .models import Document, PracticeQuestion

# Single section words such as "Insertion" or "Analysis" appear in too many questions to mean a point.
GENERIC = {
    "analysis", "applications", "balancing", "basic concepts", "bounds", "complexity", "cost", "counting",
    "cycle", "definition", "delete", "deletion", "find", "graph", "height", "insert", "insertion",
    "implementation", "introduction", "list", "operations", "order", "overview", "path", "probing",
    "properties", "pros cons", "queue", "remove", "rotation", "search", "searching", "sorting", "structure",
    "time complexity", "traversal", "tree", "trees",
}
SPLIT = re.compile(r"\s*(?:,|;|&|\band\b)\s*")


def _plain(text):
    text = unicodedata.normalize("NFKC", re.sub(r"<[^>]+>", " ", text or "")).casefold()
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", re.sub(r"[-‐-—_]", " ", text)).strip()


def _pieces(raw):
    for part in SPLIT.split(raw):
        options = [option.strip() for option in part.split("/")]
        # "Best/Average/Worst Case" shares its head word across the slash list.
        head = options[-1].rsplit(" ", 1)
        if len(options) > 1 and len(head) == 2 and all(" " not in option for option in options[:-1]):
            options = [f"{option} {head[1]}" for option in options[:-1]] + [options[-1]]
        yield from options


def point_terms(point):
    terms = set()
    # The heading's own parentheses keep "Best/Average/Worst Case" whole; name_en may hold only "Best".
    english = re.findall(r"[（(]([^（）()]*[A-Za-z][^（）()]*)[）)]", point.title or "")
    for raw in english or [point.name_en or "", *(point.aliases or [])]:
        for piece in _pieces(raw):
            term = _plain(piece).strip(" .()")
            if not re.search(r"[a-z]", term):
                continue
            named = re.fullmatch(r"([a-z]+)'s (?:algorithm|method)", term)
            if named:
                terms.add(named[1])
            if term not in GENERIC and len(term) >= 3:
                terms.add(term)
    chinese = (point.name_zh or "").strip()
    if len(chinese) >= 3 and not re.search(r"[A-Za-z]", chinese):
        terms.add(_plain(chinese))
    return terms


def _pattern(term):
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?:s|es)?(?![a-z0-9])")


def exam_hits(db, course_id, points):
    rows = db.execute(select(PracticeQuestion, Document).join(Document, Document.id == PracticeQuestion.document_id)
        .where(Document.course_id == course_id, Document.status == "ready", Document.kind.in_(["exam", "exercise"]))
        .order_by(Document.created_at, Document.id, PracticeQuestion.position)).all()
    patterns = {point.id: [(term, _pattern(term)) for term in point_terms(point)] for point in points}
    papers, hits = {}, {}
    for question, document in rows:
        paper = papers.setdefault(document.id, {"document_id": document.id, "title": file_title(document.filename),
            "year": document.year, "question_count": 0, "matched_count": 0})
        paper["question_count"] += 1
        text = _plain(" ".join([question.stem, *(option.get("text", "") for option in question.options or [])]))
        matched = {}
        for point_id, terms in patterns.items():
            found = [term for term, pattern in terms if pattern.search(text)]
            if found:
                matched[point_id] = max(found, key=len)
        # A shorter term inside a longer matched one, such as "spanning tree" in "minimum spanning tree", is not a hit.
        matched = {point_id: term for point_id, term in matched.items()
                   if not any(term != other and term in other for other in matched.values())}
        if matched:
            paper["matched_count"] += 1
        for point_id, term in matched.items():
            hits.setdefault(point_id, []).append({"question_id": question.id, "document_id": document.id,
                "paper": paper["title"], "number": question.number, "section": question.section,
                "question_type": question.question_type, "stem": question.stem, "options": question.options or [],
                "term": term})
    return list(papers.values()), hits
