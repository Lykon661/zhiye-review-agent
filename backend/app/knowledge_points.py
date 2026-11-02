from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
import json
import re
from time import sleep

from sqlalchemy import select
from sqlalchemy.orm import load_only

from .course_vocabulary import BILINGUAL, VOCABULARY_CACHE_POLICY, chapter_notes, term_start
from .lexical import normalize
from .models import Block, Chapter, Course, Document, KnowledgePoint, KnowledgePointState, Outline, RetrievalUnit, new_id, now

POINT_POLICY = "review_note_headings_v3"


def _newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _closing_at(text, delimiter):
    for match in re.finditer(re.escape(delimiter), text):
        prefix = text[:match.start()]
        if (len(prefix) - len(prefix.rstrip("\\"))) % 2 == 0:
            return match.start()
    return -1


def _strip_container(line, container):
    kind, width = container
    if kind == "quote":
        marker = re.match(r"^ {0,3}> ?", line)
        return line[marker.end():] if marker else None
    if not line.strip():
        return ""
    return line[width:] if len(line) - len(line.lstrip(" ")) >= width else None


def _container_line(line, containers):
    line, kept = line.expandtabs(4), []
    for container in containers:
        content = _strip_container(line, container)
        if content is None:
            break
        line = content
        kept.append(container)
    while line:
        quote = re.match(r"^ {0,3}> ?", line)
        if quote:
            kept.append(("quote", 0))
            line = line[quote.end():]
            continue
        marker = re.match(r"^( {0,3})([-+*]|\d{1,9}[.)])( +|$)", line)
        if marker is None or re.fullmatch(r" {0,3}(?:[-*_] *){3,}", line):
            break
        padding = len(marker[3]) if 1 <= len(marker[3]) <= 4 else 1
        width = len(marker[1]) + len(marker[2]) + padding
        kept.append(("list", width))
        line = line[width:]
    return line, kept


def _math_block_end(lines, start, containers, first):
    if len(first) - len(first.lstrip(" ")) >= 4:
        return None
    first = first.strip()
    opener = "$$" if first.startswith("$$") else (r"\[" if first.startswith(r"\[") else None)
    if opener is None:
        return None
    closer = "$$" if opener == "$$" else r"\]"
    for index in range(start, len(lines)):
        line = lines[index].expandtabs(4)
        if index != start:
            for container in containers:
                line = _strip_container(line, container)
                if line is None:
                    return None
        text = first[len(opener):] if index == start else line.strip()
        end = _closing_at(text, closer)
        if end >= 0:
            return index if not text[end + len(closer):].strip() else None
    return None


def _headings(markdown):
    fence, math_until, containers = None, -1, []
    lines = _newlines(markdown).split("\n")
    for line_number, line in enumerate(lines):
        if line_number <= math_until:
            continue
        if fence is not None:
            content = line.expandtabs(4)
            for container in containers:
                content = _strip_container(content, container)
                if content is None:
                    break
            if content is None:
                fence = None
            else:
                if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*", content):
                    fence = None
                continue
        line, containers = _container_line(line, containers)
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            continue
        end = _math_block_end(lines, line_number, containers, line)
        if end is not None:
            math_until = end
            continue
        heading = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$", line)
        if heading:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2] or "").strip()
            yield line_number, len(heading[1]), title


def parse_headings(markdown):
    points, parent = [], None
    for line_number, level, raw in _headings(markdown):
        if level <= 3:
            parent = None
        if level not in (3, 4) or not raw:
            continue
        title = raw[2:-2].strip() if raw.startswith("**") and raw.endswith("**") and len(raw) > 4 else raw
        subject = re.sub(r"`[^`]*`", "", title)
        pair = BILINGUAL.search(subject)
        names = list(dict.fromkeys(name.strip() for name in re.split(r"[,;/]", pair[2])
                                  if name.strip())) if pair else []
        chinese = term_start(pair[1], clause=False) if pair else title
        points.append({"title": title, "name_zh": chinese, "name_en": names[0] if names else None,
            "aliases": names[1:], "normalized_zh": normalize(chinese),
            "normalized_en": normalize(names[0]) if names else "", "level": level,
            "position": len(points), "body_line": line_number, "parent_position": parent})
        if level == 3:
            parent = len(points) - 1
    return points


def map_note_lines(outline):
    notes = {identity: _newlines(body) for identity, body in chapter_notes(outline).items()}
    snapshot, markdown = outline.snapshot, _newlines(outline.markdown)
    chapters = snapshot.get("chapters", [])
    parts, offsets = [f"# {snapshot.get('course_name', '')}复习笔记", ""], {}
    cursor = _newlines(parts[0]).count("\n") + 2
    for chapter in chapters:
        body = notes.get(chapter["id"], "")
        if body:
            header = _newlines(f"## {chapter['title']}")
            start = cursor + header.count("\n") + 2
            offsets[chapter["id"]] = {p["body_line"]: start + p["body_line"] for p in parse_headings(body)}
            parts.extend([header, "", body, ""])
            cursor = start + body.count("\n") + 2
    if _newlines("\n".join(parts).rstrip() + "\n") == markdown:
        return offsets
    lines, headings = markdown.split("\n"), list(_headings(markdown))
    counts = Counter(chapter["title"].strip() for chapter in chapters)
    offsets = {}
    for chapter in chapters:
        body = notes.get(chapter["id"], "")
        titles = [index for index, (_, level, title) in enumerate(headings)
                  if level == 2 and title == chapter["title"].strip()]
        if not body or counts[chapter["title"].strip()] != 1 or len(titles) != 1:
            continue
        index = titles[0]
        start = headings[index][0] + 1
        end = next((line for line, level, _ in headings[index + 1:] if level <= 2), len(lines))
        body_lines = body.rstrip("\n").split("\n")
        starts = [line for line in range(start, end - len(body_lines) + 1)
                  if lines[line:line + len(body_lines)] == body_lines]
        mapped = {}
        for point in parse_headings(body):
            if len(starts) == 1:
                mapped[point["body_line"]] = starts[0] + point["body_line"]
            else:
                raw = body.split("\n")[point["body_line"]]
                matches = [line for line, level, _ in headings if start <= line < end
                           and level == point["level"] and lines[line] == raw]
                if len(matches) == 1:
                    mapped[point["body_line"]] = matches[0]
        offsets[chapter["id"]] = mapped
    return offsets


class KnowledgePointService:
    def __init__(self, services):
        self.s = services

    def _current_outline(self, db, course):
        return db.scalar(select(Outline).options(load_only(Outline.id, Outline.course_id,
            Outline.version, Outline.status))
            .where(Outline.course_id == course.id, Outline.status == "completed", Outline.version == course.version)
            .order_by(Outline.created_at.desc(), Outline.id.desc()).limit(1))

    def _input_key(self, course, outline, chapters, notes_key, vocabulary_policy, source_key):
        return sha256(json.dumps([POINT_POLICY, outline.id, course.version, notes_key,
            vocabulary_policy, source_key, [[c.id, c.title, c.position] for c in chapters]],
            ensure_ascii=False).encode()).hexdigest()

    def _cached(self, course_id):
        vocabulary = self.s.retrieval.knowledge.vocabulary
        with self.s.sessions() as db:
            course = db.get(Course, course_id)
            if course is None:
                raise LookupError("未找到该课程")
            outline = self._current_outline(db, course)
            if outline is None:
                completed = db.scalar(select(Outline.id).where(Outline.course_id == course_id,
                    Outline.status == "completed").limit(1))
                return {"course_id": course_id, "outline_id": None,
                    "status": "stale" if completed else "no_notes", "warnings": [], "chapters": []}
            state = db.get(KnowledgePointState, course_id)
            if (state is None or state.outline_id != outline.id or state.course_version != course.version
                    or state.policy != POINT_POLICY or state.vocabulary_policy != VOCABULARY_CACHE_POLICY):
                return None
            chapters = db.scalars(select(Chapter).where(Chapter.course_id == course_id)
                .order_by(Chapter.position, Chapter.id)).all()
            source_key = vocabulary.source_key(db, course_id)
            input_key = self._input_key(course, outline, chapters, state.notes_key,
                VOCABULARY_CACHE_POLICY, source_key)
            if state.input_key != input_key or state.vocabulary_source_key != source_key:
                return None
            result = self._serialize(db, course, outline, state, chapters)
            current = self._current_outline(db, course)
            if (db.scalar(select(Course.version).where(Course.id == course_id)) != course.version
                    or current is None or current.id != outline.id
                    or db.scalar(select(KnowledgePointState.input_key).where(
                        KnowledgePointState.course_id == course_id)) != input_key
                    or vocabulary.source_key(db, course_id) != source_key):
                return None
            return result

    @contextmanager
    def _build_locks(self):
        build_lock = getattr(self.s.retrieval.knowledge, "build_lock", None)
        for attempt in range(3):
            with self.s.lock:
                if build_lock is None or build_lock.acquire(blocking=False):
                    try:
                        yield
                    finally:
                        if build_lock is not None:
                            build_lock.release()
                    return
            if attempt < 2:
                sleep(0.02)
        raise RuntimeError("知识点正在准备，请稍后刷新")

    def prepare(self, course_id):
        self.read(course_id)

    def read(self, course_id):
        cached = self._cached(course_id)
        if cached is not None:
            return cached
        knowledge = self.s.retrieval.knowledge
        with self._build_locks():
            with self.s.sessions() as db:
                course = db.get(Course, course_id)
                if course is None:
                    raise LookupError("未找到该课程")
                outline = self._current_outline(db, course)
                if outline is None:
                    completed = db.scalar(select(Outline.id).where(Outline.course_id == course_id,
                        Outline.status == "completed").limit(1))
                    return {"course_id": course_id, "outline_id": None,
                        "status": "stale" if completed else "no_notes", "warnings": [], "chapters": []}
                outline_id = outline.id
            vocabulary = knowledge.vocabulary.snapshot(outline_id)
            if vocabulary is None:
                raise RuntimeError("课程术语缓存暂不可用，请刷新后重试")
            with self.s.sessions() as db:
                course = db.get(Course, course_id)
                outline = self._current_outline(db, course) if course else None
                if outline is None or outline.id != outline_id:
                    raise RuntimeError("课程笔记已经变化，请刷新后重试")
                chapters = db.scalars(select(Chapter).where(Chapter.course_id == course_id)
                    .order_by(Chapter.position, Chapter.id)).all()
                course_version = course.version
                notes_key = vocabulary["notes_key"]
                input_key = self._input_key(course, outline, chapters, notes_key,
                    vocabulary["policy"], vocabulary["source_key"])
                state = db.get(KnowledgePointState, course_id)
                rebuild = (state is None or state.input_key != input_key or state.policy != POINT_POLICY
                    or state.outline_id != outline_id or state.course_version != course_version
                    or state.vocabulary_policy != vocabulary["policy"]
                    or state.vocabulary_source_key != vocabulary["source_key"])
                if rebuild:
                    notes, locations, nodes, warnings = chapter_notes(outline), map_note_lines(outline), [], []
                    for chapter in chapters:
                        if chapter.id not in notes:
                            warnings.append(f"章节“{chapter.title}”没有可唯一对应的笔记正文")
                        for point in parse_headings(notes.get(chapter.id, "")):
                            point.update(chapter_id=chapter.id, chapter_title=chapter.title,
                                note_line=locations.get(chapter.id, {}).get(point["body_line"]))
                            if point["level"] == 4 and point["parent_position"] is None:
                                warnings.append(f"章节“{chapter.title}”的四级标题“{point['title']}”没有三级父标题")
                            if point["note_line"] is None:
                                warnings.append(f"标题“{point['title']}”无法唯一定位到笔记行号")
                            nodes.append(point)
                    if not nodes:
                        warnings.append("当前笔记没有可提取的三级或四级标题")
                    self._align_sources(nodes, vocabulary)
                    self._reconcile(db, course_id, outline_id, nodes)
                    if state is None:
                        state = KnowledgePointState(course_id=course_id)
                        db.add(state)
                    state.outline_id, state.course_version = outline_id, course_version
                    state.notes_key, state.input_key = notes_key, input_key
                    state.vocabulary_source_key = vocabulary["source_key"]
                    state.vocabulary_policy, state.policy = vocabulary["policy"], POINT_POLICY
                    state.warnings, state.updated_at = list(dict.fromkeys(warnings)), now()
                    db.flush()
                result = self._serialize(db, course, outline, state, chapters)
                if rebuild:
                    current = self._current_outline(db, course)
                    if (db.scalar(select(Course.version).where(Course.id == course_id)) != course_version
                            or current is None or current.id != outline_id
                            or knowledge.vocabulary.source_key(db, course_id) != vocabulary["source_key"]):
                        raise RuntimeError("课程笔记或资料已经变化，请刷新后重试")
                    db.commit()
                return result

    def _align_sources(self, nodes, vocabulary):
        by_chapter = {}
        for chapter in vocabulary["chapters"]:
            names = by_chapter.setdefault(chapter["chapter_id"], {})
            for concept in chapter["concepts"]:
                for name in [concept["name"], *concept["aliases"]]:
                    names.setdefault(normalize(name), []).extend(concept["unit_ids"])
        for node in nodes:
            names = by_chapter.get(node["chapter_id"], {})
            node["unit_ids"] = list(dict.fromkeys(identity
                for name in ([node["name_en"], *node["aliases"]] if node["name_en"] else [])
                for identity in names.get(normalize(name), [])))

    def _reconcile(self, db, course_id, outline_id, nodes):
        previous = db.scalars(select(KnowledgePoint).where(KnowledgePoint.course_id == course_id)).all()
        by_id, by_chapter, used = {p.id: p for p in previous}, {}, set()
        indexed = {(node["chapter_id"], node["position"]): node for node in nodes}
        for point in previous:
            by_chapter.setdefault(point.chapter_id, []).append(point)

        def score(point, node):
            parent = indexed.get((node["chapter_id"], node["parent_position"]))
            old_parent = by_id.get(point.parent_id)
            same_parent = point.parent_id is None if parent is None else (
                point.parent_id == parent.get("id") if parent.get("id") else old_parent is not None and (
                    bool(parent["normalized_en"]) and old_parent.normalized_en == parent["normalized_en"]
                    or old_parent.normalized_zh == parent["normalized_zh"]))
            return (point.retired, not same_parent, point.level != node["level"], point.title != node["title"],
                    abs(point.position - node["position"]), point.position, point.id)

        for field in ("normalized_en", "normalized_zh"):
            for node in nodes:
                if "id" in node or not node[field]:
                    continue
                candidates = [p for p in by_chapter.get(node["chapter_id"], [])
                              if p.id not in used and getattr(p, field) == node[field]]
                if candidates:
                    node["id"] = min(candidates, key=lambda point: score(point, node)).id
                    used.add(node["id"])
        stamp = now()
        for point in previous:
            if point.id not in used and not point.retired:
                point.retired, point.updated_at = True, stamp
        for node in nodes:
            node.setdefault("id", new_id())
            point = by_id.get(node["id"])
            if point is None:
                point = KnowledgePoint(id=node["id"], course_id=course_id)
                db.add(point)
                by_id[point.id] = point
            for field in ("chapter_id", "chapter_title", "title", "name_zh", "name_en", "aliases",
                          "normalized_zh", "normalized_en", "level", "position", "note_line", "unit_ids"):
                setattr(point, field, node[field])
            point.parent_id, point.retired = None, False
            point.last_outline_id, point.updated_at = outline_id, stamp
        db.flush()
        for node in nodes:
            parent = indexed.get((node["chapter_id"], node["parent_position"]))
            by_id[node["id"]].parent_id = parent["id"] if parent else None

    def _serialize(self, db, course, outline, state, chapters):
        points = db.scalars(select(KnowledgePoint).where(KnowledgePoint.course_id == course.id,
            KnowledgePoint.retired.is_(False), KnowledgePoint.last_outline_id == outline.id)
            .order_by(KnowledgePoint.position, KnowledgePoint.id)).all()
        unit_ids = list(dict.fromkeys(identity for point in points for identity in point.unit_ids))
        units = []
        for offset in range(0, len(unit_ids), 500):
            units.extend(db.execute(select(RetrievalUnit, Document).join(Document)
                .options(load_only(RetrievalUnit.id, RetrievalUnit.course_id, RetrievalUnit.document_id,
                    RetrievalUnit.chapter_id, RetrievalUnit.position, RetrievalUnit.title,
                    RetrievalUnit.block_ids, RetrievalUnit.locator),
                    load_only(Document.id, Document.filename, Document.kind, Document.created_at))
                .where(RetrievalUnit.id.in_(unit_ids[offset:offset + 500]), RetrievalUnit.course_id == course.id,
                    Document.course_id == course.id, Document.status == "ready",
                    Document.kind.in_(["lecture", "note"]))).all())
        block_ids = list(dict.fromkeys(identity for unit, _ in units for identity in unit.block_ids))
        blocks = {}
        for offset in range(0, len(block_ids), 500):
            blocks.update({identity: (document_id, chapter_id) for identity, document_id, chapter_id in db.execute(
                select(Block.id, Block.document_id, Block.chapter_id).where(Block.id.in_(block_ids[offset:offset + 500])))})
        sources = {}
        for unit, document in sorted(units, key=lambda row: (row[1].created_at, row[1].id, row[0].position, row[0].id)):
            if (not unit.block_ids or len(set(unit.block_ids)) != len(unit.block_ids)
                    or any(blocks.get(identity) != (unit.document_id, unit.chapter_id) for identity in unit.block_ids)):
                continue
            sources[unit.id] = (unit.chapter_id, {"source_id": "S" + unit.id,
                "document_id": document.id, "filename": document.filename, "kind": document.kind,
                "locator": unit.locator, "title": unit.title})
        ranks = {identity: rank for rank, identity in enumerate(sources)}
        entries = {}
        for point in points:
            direct = [sources[identity][1] for identity in sorted(set(point.unit_ids), key=lambda key: ranks.get(key, len(ranks)))
                      if identity in sources and sources[identity][0] == point.chapter_id] if point.name_en else []
            entries[point.id] = {"id": point.id, "title": point.title, "name_zh": point.name_zh,
                "name_en": point.name_en, "aliases": point.aliases, "level": point.level,
                "position": point.position, "parent_id": point.parent_id, "note_line": point.note_line,
                "sources": direct, "source_reason": "no_english" if not point.name_en else (None if direct else "not_matched"),
                "children": []}
        trees = {chapter.id: {"chapter_id": chapter.id, "title": chapter.title,
            "position": chapter.position, "points": []} for chapter in chapters}
        for point in points:
            if point.chapter_id not in trees:
                continue
            parent = entries.get(point.parent_id)
            if parent is not None:
                parent["children"].append(entries[point.id])
            else:
                trees[point.chapter_id]["points"].append(entries[point.id])
        return {"course_id": course.id, "outline_id": outline.id, "status": "ready",
            "warnings": state.warnings, "chapters": list(trees.values())}
