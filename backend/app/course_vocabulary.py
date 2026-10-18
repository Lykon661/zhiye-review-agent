"""Reuse bilingual review-note names locally; answers still require course originals."""
from collections import OrderedDict
from functools import lru_cache
from hashlib import sha256
import json
import re
from threading import RLock

from sqlalchemy import select
from sqlalchemy.orm import load_only

from .lexical import normalize, term_pattern
from .models import AppMeta, Course, Document, Outline, RetrievalUnit
from .retrieval_units import UNIT_POLICY

VOCABULARY_CACHE_KEY = "__course_vocabulary__"
VOCABULARY_CACHE_POLICY = "review_note_terms_cache_v2"


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
        # Heading, quote and list markers are not part of a term such as "B+ 树".
        text = re.sub(r"^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+)*", "", line)
        pairs = list(BILINGUAL.finditer(text))
        table_header = ("|" in line and index + 1 < len(lines)
                        and bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[index + 1])))
        for pair in pairs:
            chinese = pair[1].strip()
            names = tuple(dict.fromkeys(t.strip() for t in re.split(r"[,;/]", pair[2]) if t.strip()))
            if names:
                # Any column after the first (row-label) column names a comparison dimension.
                entries.append({"names": names, "chinese": chinese,
                                "parents": tuple(name for _, name in headings),
                                "facet": table_header and "|" in text[:pair.start()].strip().lstrip("|")})
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
        self.lock = RLock()

    def _source_key(self, db, course_id):
        # Index signatures detect lazy unit upgrades without rereading source bodies.
        sources = [list(row) for row in db.execute(select(Document.id, AppMeta.value)
            .outerjoin(AppMeta, AppMeta.key == "knowledge_units:" + Document.id)
            .where(Document.course_id == course_id, Document.status == "ready",
                   Document.kind.in_(["lecture", "note"]))
            .order_by(Document.id))]
        return sha256(json.dumps([UNIT_POLICY, self.r.store_name, sources],
                                 ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _merge(target, entries):
        for entry in entries:
            previous = target.setdefault(normalize(entry["name"]),
                {"name": entry["name"], "aliases": [], "parents": [], "unit_ids": []})
            for field in ("aliases", "unit_ids"):
                previous[field] = list(dict.fromkeys([*previous[field], *entry[field]]))
            self_names = {normalize(t) for t in previous["aliases"] + [previous["name"]]}
            previous["parents"] = list(dict.fromkeys(p for p in [*previous["parents"], *entry["parents"]]
                                                    if normalize(p) not in self_names))

    def _build(self, db, outline):
        statement = select(RetrievalUnit).join(Document).where(RetrievalUnit.course_id == outline.course_id,
            Document.status == "ready", Document.kind.in_(["lecture", "note"]))
        by_chapter = {}
        for unit in db.scalars(statement.order_by(RetrievalUnit.position)):
            by_chapter.setdefault(unit.chapter_id, []).append(unit)
        notes, chapters = chapter_notes(outline), []
        for chapter_id, units in by_chapter.items():
            concepts, facets = OrderedDict(), OrderedDict()
            originals = [(u, normalize(u.title + "\n" + u.text)) for u in units]
            for entry in note_terms(notes.get(chapter_id, "")):
                names = list(entry["names"])
                # General plural variants are kept only when found in the originals.
                variants = list(dict.fromkeys([*names, *(name + "s" for name in names)]))
                patterns = [(t, re.compile(term_pattern(t))) for t in variants]
                matched = [(u, text) for u, text in originals if any(p.search(text) for _, p in patterns)]
                if not matched:
                    continue
                present = [t for t, pattern in patterns if any(pattern.search(text) for _, text in matched)]
                self._merge(facets if entry["facet"] else concepts, [{"name": present[0],
                    "aliases": list(dict.fromkeys([entry["chinese"], *names, *present])),
                    "parents": list(entry["parents"]), "unit_ids": [u.id for u, _ in matched]}])
            chapters.append({"chapter_id": chapter_id, "concepts": list(concepts.values()),
                             "facets": list(facets.values()), "incomplete": int(not concepts and not facets)})
        return chapters

    def _prepare(self, db, outline):
        key = self._source_key(db, outline.course_id)
        cached = outline.chapter_results.get(VOCABULARY_CACHE_KEY)
        if (isinstance(cached, dict) and cached.get("policy") == VOCABULARY_CACHE_POLICY
                and cached.get("source_key") == key and isinstance(cached.get("chapters"), list)):
            return cached
        chapters = self._build(db, outline)
        # A concurrently published index must not receive a cache of retired units.
        if self._source_key(db, outline.course_id) != key:
            return None
        cached = {"policy": VOCABULARY_CACHE_POLICY, "source_key": key, "chapters": chapters}
        outline.chapter_results = {**outline.chapter_results, VOCABULARY_CACHE_KEY: cached}
        db.commit()
        return cached

    def prepare(self, outline_id):
        """Persist original-checked terms before a generated note becomes completed."""
        with self.lock, self.r.sessions() as db:
            outline = db.get(Outline, outline_id)
            if outline is None:
                return
            course = db.get(Course, outline.course_id)
            if course is not None and course.version == outline.version:
                self._prepare(db, outline)

    def read(self, course_id, chapter_ids=None):
        concepts, facets, incomplete = OrderedDict(), OrderedDict(), 0
        with self.lock, self.r.sessions() as db:
            course = db.get(Course, course_id)
            if course is None:
                return [], [], 1
            outline = db.scalar(select(Outline).options(load_only(Outline.id, Outline.course_id,
                Outline.version, Outline.status, Outline.chapter_results)).where(Outline.course_id == course_id,
                Outline.status == "completed", Outline.version == course.version)
                .order_by(Outline.created_at.desc(), Outline.id.desc()).limit(1))
            if outline is None:
                return [], [], 1
            cached = self._prepare(db, outline)
            if cached is None:
                return [], [], 1
            for chapter in cached["chapters"]:
                if chapter_ids and chapter["chapter_id"] not in chapter_ids:
                    continue
                self._merge(concepts, chapter["concepts"])
                self._merge(facets, chapter["facets"])
                incomplete += chapter["incomplete"]
        return list(concepts.values()), list(facets.values()), incomplete
