"""Конфигурация из переменных окружения. Пароли и хосты не хардкодим."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


def _opt_date(name: str) -> date | None:
    v = os.getenv(name, "").strip()
    return date.fromisoformat(v) if v else None


@dataclass(frozen=True)
class Settings:
    pg_host: str = os.getenv("POSTGRES_HOST", "localhost")
    pg_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    pg_db: str = os.getenv("POSTGRES_DB", "deals_bigdata")
    pg_user: str = os.getenv("POSTGRES_USER", "postgres")
    pg_password: str = os.getenv("POSTGRES_PASSWORD", "postgres")

    # дата снимка источника — фиксированная, не системная
    as_of: date = date.fromisoformat(os.getenv("ML_AS_OF", "2026-10-26"))
    random_seed: int = int(os.getenv("ML_RANDOM_SEED", "42"))

    @property
    def window_start(self) -> date | None:
        return _opt_date("ETL_START_DATE")

    @property
    def window_end(self) -> date | None:
        return _opt_date("ETL_END_DATE")
