"""Batch Inference (Вопрос 3): скоринг test-периода и плановых будущих заявок.

Прогнозы пишутся в mart.ml_price_predictions батчами (execute_values, без построчных
INSERT). Для будущих заявок (loading_date > as_of) actual/abs_error/ape = NULL.
Идемпотентность: перезапись строк модели за скоринговое окно (DELETE + INSERT).
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np

from .features import FEATURES, load_facts, train_test_bounds
from .train import MODEL_DIR

log = logging.getLogger(__name__)

DELETE_SCORED = """
DELETE FROM mart.ml_price_predictions
WHERE model_name = %(model_name)s
  AND loading_date >= %(score_from)s::date
"""


def run_scoring(conn, settings) -> dict:
    bundle = joblib.load(Path(MODEL_DIR) / "model.joblib")
    model, model_name = bundle["model"], bundle["name"]

    _, test_start, as_of = train_test_bounds(settings.as_of)

    df = load_facts(conn, "pc.loading_date >= %(score_from)s::date", {"score_from": test_start})
    if df.empty:
        log.warning("nothing to score")
        return {"rows_scored": 0}

    df["predicted_price_rub"] = model.predict(df[FEATURES])

    is_future = df["loading_date"] > pd_ts(as_of)
    actual = df["price_rub"].where(~is_future)
    abs_err = (df["predicted_price_rub"] - actual).abs().where(~is_future)
    ape = (abs_err / actual * 100).where(~is_future & (actual != 0))

    rows = [
        (
            int(r.price_id), r.loading_date, int(r.loading_city_gcid), int(r.unloading_city_gcid),
            r.body_group,
            _f(a), _f(p), _f(e), _f(ap),
            model_name,
        )
        for r, a, p, e, ap in zip(
            df.itertuples(), actual, df["predicted_price_rub"], abs_err, ape
        )
    ]

    with conn.cursor() as cur:
        cur.execute(DELETE_SCORED, {"model_name": model_name, "score_from": test_start})
        cur.executemany(
            """INSERT INTO mart.ml_price_predictions
               (price_id, loading_date, loading_city_gcid, unloading_city_gcid, body_group,
                actual_price_rub, predicted_price_rub, abs_error_rub, ape_pct, model_name)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (price_id, model_name) DO UPDATE SET
                 actual_price_rub = EXCLUDED.actual_price_rub,
                 predicted_price_rub = EXCLUDED.predicted_price_rub,
                 abs_error_rub = EXCLUDED.abs_error_rub,
                 ape_pct = EXCLUDED.ape_pct,
                 scored_at = now()""",
            rows,
        )
    stats = {"rows_scored": len(rows), "future_rows": int(is_future.sum())}
    log.info("scored: %s", stats)
    return stats


def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def pd_ts(d):
    import pandas as pd

    return pd.Timestamp(d)
