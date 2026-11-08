"""Обучение бейзлайн-модели (Вопрос 3): Time-Based Split, CatBoostRegressor, метрики.

Границы (as_of = 2026-10-26):
  train = [2026-07-26, 2026-10-05), test = [2026-10-05, 2026-10-26].
shuffle по временным данным запрещён. random_seed фиксирован.
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from .features import CAT_FEATURES, FEATURES, TARGET, load_facts, train_test_bounds

log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_NAME = "catboost_v1"


def mape_pct(y_true, y_pred) -> float:
    y_true = np.asarray(y_true, dtype=float)
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - np.asarray(y_pred)[mask]) / y_true[mask])) * 100)


def run_training(conn, settings) -> dict:
    train_start, test_start, as_of = train_test_bounds(settings.as_of)

    df = load_facts(
        conn,
        "pc.loading_date >= %(train_start)s::date AND pc.loading_date <= %(as_of)s::date",
        {"train_start": train_start, "as_of": as_of},
    )
    train = df[df["loading_date"] < pd_ts(test_start)]
    test = df[df["loading_date"] >= pd_ts(test_start)]

    # val для early stopping отрезаем от ХВОСТА train (хронологически), test не участвует
    # в обучении и подборе итераций — иначе это temporal leakage.
    val_start = pd.Timestamp(test_start) - pd.Timedelta(days=7)
    fit_mask = train["loading_date"] < val_start
    fit, val = train[fit_mask], train[~fit_mask]
    log.info("split: fit=%d, val=%d (хвост train) [%s..%s), test=%d rows [%s..%s]",
             len(fit), len(val), train_start, test_start, len(test), test_start, as_of)

    MODEL_DIR.mkdir(exist_ok=True)
    model = CatBoostRegressor(
        loss_function="RMSE",
        iterations=800,
        learning_rate=0.08,
        depth=8,
        random_seed=settings.random_seed,
        train_dir=str(MODEL_DIR / "catboost_info"),  # служебные файлы, CWD может быть read-only
        verbose=100,
    )
    model.fit(
        fit[FEATURES], fit[TARGET],
        cat_features=[FEATURES.index(c) for c in CAT_FEATURES],
        eval_set=(val[FEATURES], val[TARGET]),
        use_best_model=True,
    )

    pred = model.predict(test[FEATURES])
    metrics = {
        "r2": float(r2_score(test[TARGET], pred)),
        "mape_pct": mape_pct(test[TARGET], pred),
        "rmse_rub": float(mean_squared_error(test[TARGET], pred) ** 0.5),
        "mae_rub": float(mean_absolute_error(test[TARGET], pred)),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
    }
    log.info("test metrics: %s", metrics)

    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump({"model": model, "features": FEATURES, "cat": CAT_FEATURES, "name": MODEL_NAME},
                MODEL_DIR / "model.joblib")
    return metrics


def pd_ts(d):
    import pandas as pd

    return pd.Timestamp(d)
