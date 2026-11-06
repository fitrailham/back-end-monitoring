import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app import config

logger = logging.getLogger(__name__)


def fetch_rows(
    sql: str,
    params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:

    if not config.DB_PASSWORD:
        raise RuntimeError(
            "DB_PASSWORD belum diisi pada file .env"
        )

    logger.info(
        "[DB 1/3] Connecting to PostgreSQL %s:%s/%s",
        config.DB_HOST,
        config.DB_PORT,
        config.DB_NAME
    )

    with psycopg.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        connect_timeout=8,
        row_factory=dict_row
    ) as conn:

        logger.info(
            "[DB 2/3] PostgreSQL connected"
        )

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        logger.info(
            "[DB 3/3] SELECT completed: %s row(s)",
            len(rows)
        )

        return rows