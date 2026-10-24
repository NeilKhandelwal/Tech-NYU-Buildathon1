"""Wipe every table and reseed the 3 sample cases, so each demo run starts at C-1004 / CALL-1.

Run with the server up or down: `uv run python -m scripts.reset_demo`.
"""
from sqlalchemy import delete, text

from app import db
from scripts.seed import SAMPLES, seed

# children before parents: the foreign keys are real now
TABLES = ("transcript", "call_cases", "case_events", "calls", "cases")


def reset() -> None:
    with db.connect() as conn:
        for table in TABLES:
            conn.execute(delete(db.metadata.tables[table]))
        # ids have to start at C-1001 / CALL-1 again. SQLite hands out max(id)+1, so the
        # wipe above already did it; Postgres keeps a sequence that has to be told.
        if conn.engine.dialect.name != "sqlite":
            for table in ("cases", "calls", "transcript", "case_events"):
                conn.execute(text(f"ALTER SEQUENCE {table}_id_seq RESTART WITH 1"))


if __name__ == "__main__":
    db.init_db()
    reset()
    seed()
    print(f"reset: {len(SAMPLES)} cases, 0 calls")
