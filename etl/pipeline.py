"""Оркестратор пакетного пайплайна: core transform -> mart refresh -> (опц.) ML.

Каждый шаг — отдельная транзакция; повторный запуск за то же окно строго идемпотентен
(DELETE + INSERT). Для Airflow-версии шаги импортируются как PythonCallables в dags/.
"""
from __future__ import annotations

import logging
import time

from .config import Settings
from .db import connect
from .steps import load_marts, transform

log = logging.getLogger(__name__)


def wait_for_db(settings: Settings, timeout_s: int = 600) -> None:
    """Ждёт готовности БД и завершения initdb (генерация синтетики занимает минуты;
    healthcheck контейнера проходит раньше, чем заканчиваются docker-entrypoint-скрипты)."""
    from .db import connect

    t0 = time.monotonic()
    reason = "первичная проверка"
    while True:
        try:
            with connect(settings, connect_timeout=5) as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM public.prices")
                n = cur.fetchone()[0]
                if n > 0:
                    log.info("db ready: public.prices has %d rows", n)
                    return
                reason = "таблица prices ещё пуста (идёт генерация)"
        except Exception as e:  # БД ещё не поднялась или схема не создана
            reason = str(e).strip().splitlines()[0]
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"database not ready after {timeout_s}s")
        log.info("waiting for db init... (%s)", reason)
        time.sleep(10)


def run_pipeline(settings: Settings, with_ml: bool = False) -> None:
    t0 = time.monotonic()
    log.info("pipeline start: window=%s..%s as_of=%s",
             settings.window_start, settings.window_end, settings.as_of)

    wait_for_db(settings)

    with connect(settings) as conn:
        stats = transform.run(conn, settings.window_start, settings.window_end)
        _log_run(conn, settings, "core_transform", stats)

    with connect(settings) as conn:
        stats = load_marts.run(conn, settings.window_start, settings.window_end, settings.as_of)
        _log_run(conn, settings, "mart_refresh", stats)

    if with_ml:
        from ml.score import run_scoring
        from ml.train import run_training

        with connect(settings) as conn:
            metrics = run_training(conn, settings)
        log.info("ml train metrics: %s", metrics)

        with connect(settings) as conn:
            stats = run_scoring(conn, settings)
            _log_run(conn, settings, "ml_scoring", stats)

    log.info("pipeline done in %.1fs", time.monotonic() - t0)


def _log_run(conn, settings: Settings, step: str, stats: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO core.etl_run_log (window_start, window_end, step, rows_in, rows_loaded, rows_quarantined)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (settings.window_start, settings.window_end, step,
             stats.get("rows_in"), stats.get("rows_loaded") or stats.get("mart_route_card_rows") or stats.get("rows_scored"),
             stats.get("rows_quarantined")),
        )
