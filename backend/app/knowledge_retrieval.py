"""Precise Q&A versus course-wide comparisons, with bounded missing-evidence recovery."""
from collections import OrderedDict
import logging
import re

from .lexical import lexical_score, normalize, query_terms, retrieval_topic, term_pattern
from .course_vocabulary import alias_groups
from .models import Course

logger = logging.getLogger(__name__)
SCOPE_POLICY = "review_note_terms_v7"
CJK = re.compile(r"[\u4e00-\u9fff]")
# Characters that may sit next to a one-character name such as 树 or 根 without
# joining it into another word (搜索树, 根据).
PARTICLES = set("的和与及或是在中里内等为之对把被从")
INVENTORY = re.compile(r"各种|各类|各个|每种|所有|全部|汇总|总览|列举|哪些|哪几|几种|类型|种类|分类"
                       r"|all types|kinds|types|every|overview|summarize", re.I)


def _mentions(text, owners):
    """Concepts named in text, one entry per span; longer names win overlapping spans."""
    text = normalize(text)
    found = []
    for name, keys in owners.items():
        for match in re.finditer(term_pattern(name), text):
            start, end = match.span()
            if len(name) == 1 and CJK.match(name) and any(
                    0 <= i < len(text) and CJK.match(text[i]) and text[i] not in PARTICLES for i in (start - 1, end)):
                continue
            found.append((start, end, keys))
    found.sort(key=lambda item: item[1] - item[0], reverse=True)
    covered, spans = [], []
    for start, end, keys in found:
        if not any(start < right and end > left for left, right in covered):
            covered.append((start, end))
            spans.append(keys)
    return spans


def _combine(pools, limit=96):
    # Parent IDs deduplicate before the final limit; repeated shapes never take
    # slots from another page. Start with one candidate for every child topic.
    merged = OrderedDict()
    for offset in range(max((len(pool) for pool in pools), default=0)):
        for pool in pools:
            if offset >= len(pool):
                continue
            source = pool[offset]
            identity = source["source_id"]
            if identity not in merged:
                merged[identity] = source
            else:
                previous = merged[identity]
                previous["query_topics"] = list(dict.fromkeys(
                    [*previous.get("query_topics", []), *source.get("query_topics", [])]))
    return list(merged.values())[:limit]


class KnowledgeRetrieval:
    def __init__(self, services):
        self.s, self.r = services, services.retrieval

    def _scope(self, course_id, query, request, sources, explicit, chapter_ids):
        concepts, dimensions, incomplete = self.r.knowledge.vocabulary.read(course_id, chapter_ids)
        by_name = {normalize(c["name"]): c for c in concepts}
        owners = {}
        for concept, group in zip(concepts, alias_groups(concepts)):
            common = {normalize(t) for t in concept.get("common", [])}
            for name in group:
                owners.setdefault(name, {})[group[0]] = name in common
        words = request or query

        def matched(text, ordinary=True):
            spans = _mentions(text, owners)
            # A name shared by several concepts, or written across most of the
            # notes (查找, 记录), is ordinary wording unless nothing specific is named.
            specific = {key for keys in spans if len(keys) == 1 and not any(keys.values()) for key in keys}
            if specific or not ordinary:
                return specific
            return {key for keys in spans for key in keys}

        selected = matched(words)
        excluded, clauses = set(), re.findall(
            r"(?:除了|不包括|不含|排除|exclude|except)\s*(.*?)(?:之外|以外|除外|[，,。;；]|$)", words, re.I)
        for text in clauses:
            excluded.update(matched(text))
        broad = bool(re.search(r"各种|各类|各个|每种|所有|全部|汇总|总览|all types|every|overview|summarize", words, re.I))
        # The planner names the compared subjects, including course wording the notes
        # never use (链地址法 for 分离链接). Its topic list must be grounded in the
        # request; extra child queries are trusted only when asking for an inventory.
        parts = [p.strip() for p in re.split(r"[、，,；;：:/]|以及|及其|[和与及]|\s+(?:and|or|vs|versus)\s+",
                                             retrieval_topic(query)) if len(p.strip()) >= 2]
        said = normalize(words)
        inventory = bool(INVENTORY.search(words))
        planned = [*(q for q in explicit if inventory or normalize(q) in said),
                   *(p for p in (parts if len(parts) >= 2 else []) if normalize(p) in said)]
        literal = []
        for seed in dict.fromkeys(planned):
            # "哈希查找" is more specific than the everyday 查找 inside it.
            found = matched(seed, ordinary=normalize(seed) in owners)
            if found:
                selected |= found
            elif not any(normalize(seed) in normalize(text) for text in clauses):
                literal.append(seed)
        selected -= excluded
        children = {}
        for concept in concepts:
            for parent in concept["parents"]:
                for key in ({normalize(parent)} if normalize(parent) in by_name else matched(parent)):
                    children.setdefault(key, set()).add(normalize(concept["name"]))
        pending = list(excluded)
        while pending:
            for child in children.get(pending.pop(), set()) - excluded:
                excluded.add(child)
                pending.append(child)
        selected -= excluded
        if broad:
            # Notes are summaries; their topic inventory cannot certify every course subtype.
            incomplete = max(1, incomplete)
            pending = list(selected)
            while pending:
                parent = pending.pop()
                for child in children.get(parent, set()) - selected - excluded:
                    selected.add(child)
                    pending.append(child)
        if not selected and not literal:
            # A semantic-only fallback locates evidence but cannot prove a complete inventory.
            ids = {s.get("retrieval_unit_id", s.get("chunk_id")) for s in sources[:12]}
            selected = {key for key, c in by_name.items() if ids.intersection(c["unit_ids"])} - excluded
            incomplete = max(1, incomplete)
        ordered = [c for c in concepts if normalize(c["name"]) in selected]
        topics = [*({"name": c["name"], "queries": [c["name"]], "unit_ids": c["unit_ids"][:8]} for c in ordered),
                  *({"name": seed, "queries": [seed], "unit_ids": []} for seed in literal)]
        incomplete += max(0, len(topics) - 24)
        topics = topics[:24]
        if not topics:
            topics = [{"name": query, "queries": [query], "unit_ids": []}]
            incomplete = max(1, incomplete)
        facets = [f["name"] for f in dimensions
                  if lexical_score(words, alias_groups([f]))]
        if not facets and re.search(r"比较|对比|compare|comparison", words, re.I):
            ids = {i for t in topics for i in t["unit_ids"]}
            facets = [f["name"] for f in dimensions if ids.intersection(f["unit_ids"])]
        facets = list(dict.fromkeys(facets))
        incomplete += max(0, len(facets) - 6)
        return topics, facets[:6], incomplete

    @staticmethod
    def _coverage(topics, facets, sources, ranking):
        rows = []
        for topic in topics:
            name = topic["name"]
            supporting = {e["source_id"] for e in ranking.get("coverage", []) if e["topic"] == name}
            ids = [s["source_id"] for s in sources if name in s.get("matched_topics", [])
                   or s["source_id"] in supporting
                   or (not s.get("rerank_assessed", ranking.get("assessed")) and name in s.get("query_topics", []))]
            covered = {e["facet"] for e in ranking.get("coverage", []) if e["topic"] == name
                       and any(s["source_id"] == e["source_id"] for s in sources)}
            rows.append({"topic": name, "source_ids": ids, "covered_facets": sorted(covered),
                         "missing_facets": [f for f in facets if f not in covered],
                         "has_material": bool(ids), "assessed": bool(ranking.get("assessed"))})
        return rows

    def retrieve(self, course_id, query, request="", overview=False, queries=None, chapter_ids=None):
        chapters = list(dict.fromkeys(chapter_ids or []))
        with self.s.sessions() as db:
            course = db.get(Course, course_id)
            version = course.version if course else None
        cache, retrieval_errors = {}, []
        def candidates(seed, limit):
            key = (seed, limit)
            if key not in cache:
                larger = next((pool for (word, count), pool in cache.items()
                               if normalize(word) == normalize(seed) and count >= limit), None)
                if larger is not None:
                    cache[key] = larger[:limit]
                else:
                    try:
                        cache[key] = self.r.knowledge.candidates(course_id, seed, limit=limit, chapter_ids=chapters)
                    except ValueError as exc:
                        if "索引需要重建" in str(exc):
                            raise
                        # A failed child query must not discard readable
                        # catalog references and other topics already found.
                        retrieval_errors.append({"query": seed, "error": str(exc)})
                        logger.warning("Knowledge query unavailable: %s", exc)
                        cache[key] = []
            return cache[key]
        explicit = list(dict.fromkeys(q.strip() for q in (queries or []) if q.strip()))[:24]
        initial = candidates(query, 90 if overview else 60)
        topics, facets, omitted_titles = ([{"name": query, "queries": [query], "unit_ids": []}], [], 0)
        if overview:
            # Local planning is cheap and must see newly completed notes even at the same course version.
            topics, facets, omitted_titles = self._scope(course_id, query, request, initial, explicit, chapters)
        elif explicit:
            topics = [{"name": q, "queries": [q], "unit_ids": []} for q in explicit]
        if not overview:
            # Preserve literal example keys when the planner rewrites the concept.
            groups, _ = query_terms((request + " " + query).strip())
            numbers = [g[0] for g in groups if len(g) == 1 and g[0].isdigit()]
            if len(numbers) >= 2:
                found = candidates(" ".join(numbers), 24)
                initial = _combine([found, initial], 60)
        pools, query_log = [], []
        for topic in topics:
            subpools = []
            for seed in topic["queries"][:2]:
                found = candidates(seed, 24 if overview else 40)
                subpools.append([{**s, "query_topics": [topic["name"]]} for s in found])
                query_log.append({"query": seed, "topic": topic["name"], "candidate_units": len(found)})
            # Reading a real catalog reference is useful even if its title was
            # poorly represented in the vector search. Still rerank its body.
            with self.s.sessions() as db:
                referenced = [self.r.source(db, identity) for identity in topic["unit_ids"]]
            subpools.append([{**s, "query_topics": [topic["name"]]} for s in referenced if s
                            and s["course_id"] == course_id and (not chapters or s.get("chapter_id") in chapters)])
            pools.append(_combine(subpools, 32))
        pools.append(initial)
        recalled = _combine(pools, 96)
        names = [t["name"] for t in topics]
        rank_context = {"course_id": course_id, "course_version": version, "chapter_ids": sorted(chapters)}
        ranked, ranking = self.r.reranker.rank(request or query, recalled, names, facets, context=rank_context)
        rows = self._coverage(topics, facets, ranked, ranking)
        missing = [row for row in rows if not row["has_material"] or
                   (row["assessed"] and row["missing_facets"])]
        if missing:
            supplements = []
            for row in missing[:24]:
                suffix = " ".join(row["missing_facets"])
                seed = (row["topic"] + " " + suffix).strip()
                found = candidates(seed, 24)
                supplements.append([{**s, "query_topics": [row["topic"]]} for s in found])
                query_log.append({"query": seed, "topic": row["topic"], "candidate_units": len(found), "supplement": True})
            combined = _combine([*supplements, recalled], 96)
            scores = ranking.get("scores", {})
            # Already assessed pages need no second paid ranking call. New or
            # budget-omitted pages can still change a missing facet's coverage.
            excerpted = set(ranking.get("excerpted_sources", []))
            missing_topics = {r["topic"] for r in missing}
            focus = _combine(supplements, 24)
            pending = _combine([
                [s for s in combined if s["source_id"] not in scores],
                [s for s in focus if s["source_id"] in excerpted and missing_topics.intersection(s.get("query_topics", []))],
            ], 48)
            if pending:
                supplemental, extra = self.r.reranker.rank(request or query, pending, names, facets, context=rank_context)
                reassessed = set(extra.get("scores", {}))
                ranked = _combine([[s for s in ranked if s["source_id"] not in reassessed], supplemental], 128)
                ranking = {**ranking, "scores": {**scores, **extra.get("scores", {})},
                    "cache_hits": ranking.get("cache_hits", 0) + extra.get("cache_hits", 0),
                    "model_calls": ranking.get("model_calls", 0) + extra.get("model_calls", 0),
                    "coverage": [*(e for e in ranking.get("coverage", []) if e["source_id"] not in reassessed),
                                 *extra.get("coverage", [])],
                    "supplement": extra}
                ranking["assessed"] = all(s["source_id"] in ranking["scores"] for s in combined)
                ranking["assessed_count"] = len(ranking["scores"])
                ranking["candidate_count"] = len(combined)
                ranking["omitted_candidates"] = sum(s["source_id"] not in ranking["scores"] for s in combined)
                ranking["method"] = "model_rerank" if ranking["assessed"] else "partial_rerank" if ranking["scores"] else "hybrid_fallback"
                recalled = combined
                rows = self._coverage(topics, facets, ranked, ranking)
        # Reserve a useful page for every topic before adding detailed facets;
        # many pages for one tree must not push another tree past the limit.
        by_id = {s["source_id"]: s for s in ranked}
        selected = OrderedDict()
        facet_pools = []
        for row in rows:
            for identity in row["source_ids"][:1]:
                selected.setdefault(identity, by_id[identity])
            facet_pools.append([by_id[e["source_id"]] for e in ranking.get("coverage", [])
                                if e["topic"] == row["topic"] and e["source_id"] in by_id])
        for source in _combine(facet_pools):
            selected.setdefault(source["source_id"], source)
        for source in ranked:
            selected.setdefault(source["source_id"], source)
        result = list(selected.values())[:max(16, min(64, len(topics) * 4))]
        report = {"query": query, "request": request, "mode": "comparison" if overview else "precise",
            "course_version": version, "chapter_ids": chapters, "scope_policy": SCOPE_POLICY,
            "retrieval_errors": retrieval_errors,
            "topic_plan": topics,
            "topics": names, "facets": facets, "coverage": rows, "queries": query_log,
            "rerank": ranking, "candidate_units": len(recalled), "omitted_catalog_titles": omitted_titles,
            "selected": [{"source_id": s["source_id"], "filename": s["filename"], "locator": s["locator"],
                          "title": s.get("title", ""), "characters": len(s["text"])} for s in result]}
        return result, report
