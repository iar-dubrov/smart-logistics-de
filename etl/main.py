"""CLI-точка входа пайплайна.

Примеры:
  python -m etl.main                          # полная загрузка (core + mart)
  python -m etl.main --with-ml                # + обучение модели и batch-скоринг
  python -m etl.main --start 2026-09-01 --end 2026-09-30   # backfill за месяц
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from .config import Settings
from .pipeline import run_pipeline


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Smart Logistics ETL pipeline")
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="начало окна backfill по loading_date (inclusive)")
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="конец окна backfill (inclusive)")
    parser.add_argument("--with-ml", action="store_true",
                        help="после ETL выполнить ML train + batch scoring")
    args = parser.parse_args()

    import os

    if args.start:
        os.environ["ETL_START_DATE"] = args.start.isoformat()
    if args.end:
        os.environ["ETL_END_DATE"] = args.end.isoformat()

    settings = Settings()
    run_pipeline(settings, with_ml=args.with_ml)


if __name__ == "__main__":
    main()
