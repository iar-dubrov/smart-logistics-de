"""Шаг 1 (Вопрос 1): валидация, очистка, конвертация валют, идемпотентная загрузка raw -> core.

Вся логика — в sql/core/11_core_transform.sql (ELT-подход: тяжёлые трансформации на SQL,
без построчных INSERT в Python). Шаг атомарен: одна транзакция на окно дат.
"""
from __future__ import annotations

import logging

from ..db import SQL_ROOT, exec_sql_file, scalar

log = logging.getLogger(__name__)


def run(conn, window_start, window_end) -> dict:
    params = {"start": window_start, "end": window_end}

    rows_in = scalar(
        conn,
        """SELECT count(*) FROM public.prices p
           WHERE (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
             AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date)""",
        params,
    )

    exec_sql_file(conn, SQL_ROOT / "core" / "10_core_ddl.sql")
    exec_sql_file(conn, SQL_ROOT / "core" / "11_core_transform.sql", params)

    loaded = scalar(
        conn,
        """SELECT count(*) FROM core.prices_clean pc
           WHERE (%(start)s::date IS NULL OR pc.loading_date >= %(start)s::date)
             AND (%(end)s::date   IS NULL OR pc.loading_date <= %(end)s::date)""",
        params,
    )
    quarantined = scalar(
        conn,
        """SELECT count(*) FROM core.dq_quarantine_prices q
           JOIN public.prices p ON p.id = q.price_id
           WHERE (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
             AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date)""",
        params,
    )

    stats = {"rows_in": rows_in, "rows_loaded": loaded, "rows_quarantined": quarantined}
    log.info("core transform: %s", stats)
    return stats
