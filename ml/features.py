"""Feature pipeline (Вопрос 3): сбор признаков из core-слоя без утечек данных.

ЗАПРЕЩЕНО как признаки (Data Leakage):
  * price_with_vat и любые производные тарифа текущего рейса (price/distance, price/weight);
  * компоненты маржи (carrier_cost = customer_cost - profit);
  * price_group_stats (avg_price, min/max_km_price, price_group_id) — постфактум-агрегаты;
  * флаг outlier_mark (только фильтр таргета, не признак).
Все статистики импутации/скейлинга обучаются строго на train.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "price_rub"

CAT_FEATURES = [
    "loading_region", "unloading_region",
    "body_group", "weight_category", "volume_category",
]
NUM_FEATURES = [
    "distance", "total_weight", "total_volume",
    "loading_lat", "loading_lon", "unloading_lat", "unloading_lon",
    "year", "month_sin", "month_cos", "dow_sin", "dow_cos", "doy_sin", "doy_cos",
]
FEATURES = CAT_FEATURES + NUM_FEATURES

# единый SELECT фактов: is_fact, прямые маршруты, валидные price_rub/distance,
# без выбросов; география из дедуплицированного справочника
FACTS_SQL = """
SELECT pc.price_id, pc.loading_date, pc.loading_city_gcid, pc.unloading_city_gcid,
       pbg.body_group, pc.weight_category, pc.volume_category,
       pc.distance, pc.total_weight, pc.total_volume, pc.price_rub,
       lc.region AS loading_region, lc.latitude AS loading_lat, lc.longitude AS loading_lon,
       uc.region AS unloading_region, uc.latitude AS unloading_lat, uc.longitude AS unloading_lon
FROM core.prices_clean pc
-- для ML берём одну (основную) группу кузова на сделку, чтобы не дублировать строки
JOIN core.price_body_groups pbg ON pbg.price_id = pc.price_id AND pbg.is_primary
JOIN core.cities_dedup lc ON lc.gc_id = pc.loading_city_gcid
JOIN core.cities_dedup uc ON uc.gc_id = pc.unloading_city_gcid
WHERE pc.is_fact AND NOT pc.complex AND NOT pc.outlier_mark
  AND pc.price_rub > 0 AND pc.distance > 0
"""


def add_cyclical(df: pd.DataFrame) -> pd.DataFrame:
    """Периодическое кодирование loading_date: sin/cos для месяца, дня недели, дня года."""
    d = pd.to_datetime(df["loading_date"])
    df = df.copy()
    df["year"] = d.dt.year
    month = d.dt.month
    dow = d.dt.dayofweek
    doy = d.dt.dayofyear
    df["month_sin"] = np.sin(2 * np.pi * month / 12)
    df["month_cos"] = np.cos(2 * np.pi * month / 12)
    df["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    df["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return df


def load_facts(conn, where: str = "", params: dict | None = None) -> pd.DataFrame:
    """where — только внутренние константы из train.py/score.py, пользовательский
    ввод сюда не передаётся; все значения идут через params (серверные параметры)."""
    import warnings

    sql = FACTS_SQL + ("\n  AND " + where if where else "")
    with warnings.catch_warnings():
        # psycopg3-коннекция работает с pandas корректно, warning неинформативен
        warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
        df = pd.read_sql(sql, conn, params=params)
    df["loading_date"] = pd.to_datetime(df["loading_date"])
    for col in CAT_FEATURES:
        df[col] = df[col].fillna("Не указан").astype(str)
    return add_cyclical(df)


def train_test_bounds(as_of) -> tuple:
    """Хронологические границы: train=[as_of-3м, as_of-3н), test=[as_of-3н, as_of]."""
    from dateutil.relativedelta import relativedelta

    train_start = as_of - relativedelta(months=3)          # 2026-07-26 вкл.
    test_start = as_of - relativedelta(weeks=3)            # 2026-10-05 вкл.
    return train_start, test_start, as_of
