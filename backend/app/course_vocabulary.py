"""Reuse bilingual review-note names locally; answers still require course originals."""
from collections import OrderedDict
from functools import lru_cache
import re

from sqlalchemy import select

from .lexical import normalize, term_pattern
from .models import Course, Document, Outline, RetrievalUnit


BILINGUAL = re.compile(
    r"((?:[A-Za-z0-9+#-]+\s*)?[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9 +#./_-]{0,59}?)(?:\*\*)?\s*[（(](?:\*\*)?"
    r"([A-Za-z][A-Za-z0-9 +#./_,;’':-]{0,119}?)(?:\*\*)?[）)]")


def alias_groups(concepts):
    return [tuple(dict.fromkeys(normalize(t) for t in [c["name"], *c["aliases"]])) for c in concepts]


@lru_cache(maxsize=128)
def note_terms(markdown):
    """Read explicit 中文（English） pairs and heading membership, without inference."""
    entries, headings, fence = [], [], None
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        line = re.sub(r"`[^`]*`", "", line)
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading:
            level = len(heading[1])
            headings = [(depth, name) for depth, name in headings if depth < level]
        pairs = list(BILINGUAL.finditer(line))
        table_header = ("|" in line and index + 1 < len(lines)
                        and bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[index + 1])))
        for pair in pairs:
            chinese = pair[1].strip()
            names = tuple(dict.fromkeys(t.strip() for t in re.split(r"[,;/]", pair[2]) if t.strip()))
            if names:
                entries.append({"names": names, "chinese": chinese,
                                "parents": tuple(name for _, name in headings),
                                "facet": table_header and "|" in line[:pair.start()].strip().strip("|")})
        if heading:
            title = pairs[0][2].split(",")[0].strip() if pairs else heading[2].strip("* ")
            headings.append((len(heading[1]), title))
    return tuple(entries)


def chapter_notes(outline):
    results = {identity: value["markdown"] for identity, value in outline.chapter_results.items()
               if isinstance(value, dict) and isinstance(value.get("markdown"), str)}
    # Legacy rendered notes are usable only when their chapter title is unambiguous.
    names = {}
    for chapter in outline.snapshot.get("chapters", []):
        names.setdefault(chapter["title"], []).append(chapter["id"])
    for match in re.finditer(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", outline.markdown, re.M | re.S):
        ids = names.get(match[1].strip(), [])
        if len(ids) == 1:
            results.setdefault(ids[0], match[2])
    return results


class CourseVocabulary:
    def __init__(self, retrieval):
        self.r = retrieval

    def read(self, course_id, chapter_ids=None):
        concepts, facets, incomplete = OrderedDict(), OrderedDict(), 0
        with self.r.sessions() as db:
            course = db.get(Course, course_id)
            if course is None:
                return [], [], 1
            outline = db.scalar(select(Outline).where(Outline.course_id == course_id,
                Outline.status == "completed", Outline.version == course.version)
                .order_by(Outline.created_at.desc(), Outline.id.desc()).limit(1))
            if outline is None:
                return [], [], 1
            statement = select(RetrievalUnit).join(Document).where(RetrievalUnit.course_id == course_id,
                Document.status == "ready", Document.kind.in_(["lecture", "note"]))
            if chapter_ids:
                statement = statement.where(RetrievalUnit.chapter_id.in_(chapter_ids))
            by_chapter = {}
            for unit in db.scalars(statement.order_by(RetrievalUnit.position)):
                by_chapter.setdefault(unit.chapter_id, []).append(unit)
            notes = chapter_notes(outline)
            for chapter_id, units in by_chapter.items():
                markdown = notes.get(chapter_id, "")
                if not markdown:
                    incomplete += 1
                    continue
                found = False
                originals = [(u, normalize(u.title + "\n" + u.text)) for u in units]
                for entry in note_terms(markdown):
                    names = list(entry["names"])
                    # General plural variants are kept only when found in the originals.
                    variants = list(dict.fromkeys([*names, *(name + "s" for name in names)]))
                    patterns = [(t, re.compile(term_pattern(t))) for t in variants]
                    matched = [(u, text) for u, text in originals if any(p.search(text) for _, p in patterns)]
                    if not matched:
                        continue
                    found = True
                    present = [t for t, pattern in patterns if any(pattern.search(text) for _, text in matched)]
                    target = facets if entry["facet"] else concepts
                    name = present[0]
                    previous = target.setdefault(normalize(name), {"name": name, "aliases": [], "parents": [], "unit_ids": []})
                    previous["aliases"] = list(dict.fromkeys([*previous["aliases"], entry["chinese"], *names, *present]))
                    self_names = {normalize(t) for t in previous["aliases"] + [name]}
                    previous["parents"] = list(dict.fromkeys([*previous["parents"],
                        *(p for p in entry["parents"] if normalize(p) not in self_names)]))
                    previous["unit_ids"] = list(dict.fromkeys([*previous["unit_ids"], *(u.id for u, _ in matched)]))
                incomplete += int(not found)
        return list(concepts.values()), list(facets.values()), incomplete
