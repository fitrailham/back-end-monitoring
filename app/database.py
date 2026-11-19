from contextlib import contextmanager
from contextvars import ContextVar
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app import config
from app.sync_runtime import WorkerConnectionLost, remaining_time

logger = logging.getLogger(__name__)
_worker_connection = ContextVar("scheduler_database_connection", default=None)


def open_db():
    if not config.DB_PASSWORD:
        raise psycopg.OperationalError("Database password is not configured")
    return psycopg.connect(
        host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
        user=config.DB_USER, password=config.DB_PASSWORD,
        connect_timeout=8, row_factory=dict_row,
    )


@contextmanager
def use_worker_connection(connection, statement_timeout_seconds):
    token = _worker_connection.set((connection, statement_timeout_seconds))
    try:
        yield
    finally:
        _worker_connection.reset(token)


class WorkerTransaction:
    def __init__(self, connection, statement_timeout_seconds):
        self.connection = connection
        self.statement_timeout_seconds = statement_timeout_seconds

    def execute(self, sql, params=None):
        remaining = remaining_time()
        if remaining is not None and remaining < self.statement_timeout_seconds:
            self.connection.execute("SELECT set_config('statement_timeout', %s, true)",
                                    (f"{max(1, int(remaining * 1000))}ms",))
        return self.connection.execute(sql, params)


@contextmanager
def db_session():
    worker = _worker_connection.get()
    if worker is None:
        with open_db() as connection:
            yield connection
        return
    connection, statement_timeout_seconds = worker
    remaining_time()
    if connection.closed or connection.broken:
        raise WorkerConnectionLost("Sesi PostgreSQL yang memegang lock terputus.")
    try:
        with connection.transaction():
            yield WorkerTransaction(connection, statement_timeout_seconds)
            remaining_time()
    except psycopg.Error as exc:
        if connection.closed or connection.broken:
            raise WorkerConnectionLost("Sesi PostgreSQL yang memegang lock terputus.") from exc
        raise


def verify_worker_connection():
    if _worker_connection.get() is not None:
        with db_session() as connection:
            connection.execute("SELECT 1")


def fetch_rows(
    sql: str,
    params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:

    if not config.DB_PASSWORD:
        raise psycopg.OperationalError(
            "DB_PASSWORD belum diisi pada file .env"
        )

    logger.info(
        "[DB 1/3] Connecting to PostgreSQL %s:%s/%s",
        config.DB_HOST,
        config.DB_PORT,
        config.DB_NAME
    )

    with db_session() as conn:

        logger.info(
            "[DB 2/3] PostgreSQL connected"
        )

        rows = conn.execute(sql, params).fetchall()

        logger.info(
            "[DB 3/3] SELECT completed: %s row(s)",
            len(rows)
        )

        return rows
