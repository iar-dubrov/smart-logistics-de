"""Шаг 2 (Вопрос 2): пересчёт аналитической витрины mart.mart_route_card.

Идемпотентность: месяцы окна удаляются и вставляются заново в одной транзакции.
"""
from __future__ import annotations

import logging

from ..db import SQL_ROOT, exec_sql_file, scalar

log = logging.getLogger(__name__)

# месячное окно для витрины (границы приводим к первому числу месяца)
DELETE_WINDOW = """
DELETE FROM mart.mart_route_card
WHERE (%(start)s::date IS NULL OR price_month >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR price_month <= %(end)s::date)
"""


def run(conn, window_start, window_end, as_of) -> dict:
    # окно витрины — по месяцам; если задано окно по дням, сужаем до покрытых месяцев
    def to_month(d):
        return d.replace(day=1) if d else None

    params = {"start": to_month(window_start), "end": to_month(window_end), "as_of": as_of}

    exec_sql_file(conn, SQL_ROOT / "mart" / "20_mart_ddl.sql")
    with conn.cursor() as cur:
        cur.execute(DELETE_WINDOW, params)
    exec_sql_file(conn, SQL_ROOT / "mart" / "21_mart_route_card.sql", params)

    rows = scalar(
        conn,
        """SELECT count(*) FROM mart.mart_route_card
           WHERE (%(start)s::date IS NULL OR price_month >= %(start)s::date)
             AND (%(end)s::date   IS NULL OR price_month <= %(end)s::date)""",
        params,
    )
    stats = {"mart_route_card_rows": rows}
    log.info("mart refresh: %s", stats)
    return stats
