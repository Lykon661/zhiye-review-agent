"""FTS5/BM25 over complete contextual units, with an exact scan fallback."""
import logging
import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .lexical import normalize

logger = logging.getLogger(__name__)


def search_tokens(value):
    result = []
    for match in re.finditer(r"[a-z0-9]+|[\u4e00-\u9fff]+", normalize(value)):
        word = match.group()
        if word.isascii():
            result.append(word)
        elif len(word) == 1:
            result.append("z" + word.encode().hex())
        else:
            result.extend("z" + word[i:i + 2].encode().hex() for i in range(len(word) - 1))
    return result


def index_text(value):
    return " ".join(search_tokens(value))


def initialize_unit_index(engine):
    try:
        with engine.begin() as connection:
            existed = connection.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE name = 'retrieval_unit_lexical_index'"
            ).scalar()
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_unit_lexical_index "
                "USING fts5(unit_id UNINDEXED, terms, tokenize='unicode61')"
            )
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS retrieval_unit_insert AFTER INSERT ON retrieval_units BEGIN
                    INSERT INTO retrieval_unit_lexical_index(rowid, unit_id, terms)
                    VALUES (new.rowid, new.id, review_unit_terms(new.index_text));
                END
            """)
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS retrieval_unit_delete AFTER DELETE ON retrieval_units BEGIN
                    DELETE FROM retrieval_unit_lexical_index WHERE rowid = old.rowid;
                END
            """)
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS retrieval_unit_update AFTER UPDATE OF index_text ON retrieval_units BEGIN
                    DELETE FROM retrieval_unit_lexical_index WHERE rowid = old.rowid;
                    INSERT INTO retrieval_unit_lexical_index(rowid, unit_id, terms)
                    VALUES (new.rowid, new.id, review_unit_terms(new.index_text));
                END
            """)
            if not existed:
                connection.exec_driver_sql(
                    "INSERT INTO retrieval_unit_lexical_index(rowid, unit_id, terms) "
                    "SELECT rowid, id, review_unit_terms(index_text) FROM retrieval_units"
                )
        return True
    except OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            raise
        logger.warning("Unit FTS5 unavailable; using contextual unit term scan")
        return False


def ranked_unit_ids(db, course_id, groups, chapter_id=None, year=None, limit=120, chapter_ids=None):
    if not db.get_bind().get_execution_options().get("unit_lexical_index_enabled"):
        return None
    clauses = []
    for group in groups:
        for term in group:
            tokens = search_tokens(term)
            if tokens:
                clauses.append("(" + " AND ".join('"' + token + '"' for token in tokens) + ")")
    if not clauses:
        return []
    statement = """
        SELECT u.id FROM retrieval_unit_lexical_index
        JOIN retrieval_units u ON u.id = retrieval_unit_lexical_index.unit_id
        JOIN documents d ON d.id = u.document_id
        WHERE retrieval_unit_lexical_index MATCH :query AND u.course_id = :course_id
          AND d.status = 'ready' AND d.kind IN ('lecture', 'note')
    """
    params = {"query": " OR ".join(dict.fromkeys(clauses)), "course_id": course_id, "limit": limit}
    chapters = list(dict.fromkeys(chapter_ids or ([chapter_id] if chapter_id else [])))
    if chapters:
        names = ["chapter_" + str(i) for i in range(len(chapters))]
        statement += " AND u.chapter_id IN (" + ",".join(":" + name for name in names) + ")"
        params.update(zip(names, chapters))
    if year is not None:
        statement += " AND d.year = :year"
        params["year"] = year
    statement += " ORDER BY bm25(retrieval_unit_lexical_index), u.id LIMIT :limit"
    return list(db.scalars(text(statement), params))
