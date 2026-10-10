from pathlib import Path
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from .models import Base
from .lexical_index import index_terms, initialize_index
from .retrieval_lexical import index_text, initialize_unit_index


def create_database(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{(data_dir / 'review.db').as_posix()}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def pragmas(connection, _):
        connection.create_function("review_lexical_terms", 1, index_terms, deterministic=True)
        connection.create_function("review_unit_terms", 1, index_text, deterministic=True)
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    # create_all does not add columns to existing chat history tables.
    # Old replies stay null: their provider usage cannot be recovered later.
    with engine.begin() as connection:
        if "token_usage" not in {c["name"] for c in inspect(connection).get_columns("messages")}:
            connection.execute(text("ALTER TABLE messages ADD COLUMN token_usage JSON"))
    engine.update_execution_options(lexical_index_enabled=initialize_index(engine))
    engine.update_execution_options(unit_lexical_index_enabled=initialize_unit_index(engine))
    return engine, sessionmaker(bind=engine, expire_on_commit=False)
