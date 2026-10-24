"""Alembic environment. The URL comes from app.db, never from alembic.ini."""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))  # so `app` imports under any cwd

from app import db  # noqa: E402  -- importing it also loads backend/.env, so a CLI
# `alembic upgrade head` targets the same database the app does

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: init_db() calls this inside the app's startup, and the
    # default would switch off every logger already configured -- uvicorn's access and error
    # logs included, for the life of the process
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline() -> None:
    context.configure(url=db.database_url(), target_metadata=db.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(db.database_url())
    with engine.connect() as conn:
        # batch mode so a future ALTER survives SQLite, which can only rebuild the table
        context.configure(connection=conn, target_metadata=db.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
