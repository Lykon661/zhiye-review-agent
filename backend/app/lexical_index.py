"""SQLite postings for normalized character pairs; exact scoring stays in lexical.py."""
import logging

from sqlalchemy import String, text
from sqlalchemy.exc import OperationalError

from .lexical import normalize

logger = logging.getLogger(__name__)
INDEX_POLICY = "normalized_bigrams_v1"


def gram_tokens(value):
    value = normalize(value)
    # Encode punctuation, spaces and Chinese as ordinary unicode61 word tokens.
    # Any exact substring match necessarily contains all of its character pairs.
    return sorted({"g" + value[i:i + 2].encode("utf-8").hex() for i in range(len(value) - 1)})


def index_terms(value):
    return " ".join(gram_tokens(value))


def initialize_index(engine):
    """Backfill existing chunks once; triggers maintain the same DB transaction."""
    try:
        with engine.begin() as connection:
            existed = connection.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE name = 'chunk_lexical_index'"
            ).scalar()
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_lexical_index "
                "USING fts5(chunk_id UNINDEXED, terms, tokenize='unicode61')"
            )
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS chunk_lexical_insert AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunk_lexical_index(rowid, chunk_id, terms)
                    VALUES (new.rowid, new.id, review_lexical_terms(new.text));
                END
            """)
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS chunk_lexical_delete AFTER DELETE ON chunks BEGIN
                    DELETE FROM chunk_lexical_index WHERE rowid = old.rowid;
                END
            """)
            connection.exec_driver_sql("""
                CREATE TRIGGER IF NOT EXISTS chunk_lexical_update AFTER UPDATE OF id, text ON chunks BEGIN
                    DELETE FROM chunk_lexical_index WHERE rowid = old.rowid;
                    INSERT INTO chunk_lexical_index(rowid, chunk_id, terms)
                    VALUES (new.rowid, new.id, review_lexical_terms(new.text));
                END
            """)
            policy = connection.exec_driver_sql(
                "SELECT value FROM app_meta WHERE key = 'lexical_index_policy'"
            ).scalar()
            if not existed or policy != INDEX_POLICY:
                connection.exec_driver_sql("DELETE FROM chunk_lexical_index")
                connection.exec_driver_sql(
                    "INSERT INTO chunk_lexical_index(rowid, chunk_id, terms) "
                    "SELECT rowid, id, review_lexical_terms(text) FROM chunks"
                )
                connection.exec_driver_sql(
                    "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('lexical_index_policy', ?)",
                    (INDEX_POLICY,),
                )
        return True
    except OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            raise
        logger.warning("SQLite FTS5 unavailable; retaining exact lexical scan")
        return False


def candidate_chunks(db, groups):
    if not db.get_bind().get_execution_options().get("lexical_index_enabled"):
        return None
    clauses = []
    for group in groups:
        for term in group:
            tokens = gram_tokens(term)
            if not tokens:
                # Single-character aliases must not silently lose recall.
                return None
            # A subset is sufficient to filter; exact spelling, phrase order
            # and English word boundaries are checked by lexical_score later.
            tokens = tokens[::max(1, len(tokens) // 8)][:8]
            clauses.append("(" + " AND ".join(tokens) + ")")
    if not clauses:
        return None
    expression = " OR ".join(dict.fromkeys(clauses))
    return text(
        "SELECT chunk_id FROM chunk_lexical_index WHERE chunk_lexical_index MATCH :lexical_query"
    ).bindparams(lexical_query=expression).columns(chunk_id=String)
