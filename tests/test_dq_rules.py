"""Юнит-тесты чистых правил (без БД). SQL smoke-проверки — tests/test_sql_smoke.py (TODO, требуют контейнер)."""
import math
from datetime import date

import numpy as np
import pandas as pd

from ml.features import add_cyclical, train_test_bounds


def weight_category(w: float) -> str:
    """Эталон правила заполнения weight_category (зеркало SQL из 11_core_transform.sql)."""
    w = round(w, 1)
    if w >= 30:
        return "G20T"
    if w >= 10:
        return "FTL"
    if w >= 5:
        return "10T"
    if w >= 2.5:
        return "5T"
    if w >= 0.5:
        return "L5T"
    return "OTH"


def volume_category(v: float) -> str:
    return "FTL" if 0 < v <= 120 else "OTH"


def test_weight_category_boundaries():
    assert weight_category(30.0) == "G20T"   # граница включается вверх
    assert weight_category(29.96) == "G20T"  # округление до 0.1 -> 30.0
    assert weight_category(10.0) == "FTL"
    assert weight_category(9.96) == "FTL"
    assert weight_category(5.0) == "10T"
    assert weight_category(2.5) == "5T"
    assert weight_category(0.5) == "L5T"
    assert weight_category(0.49) == "L5T"   # 0.49 округляется до 0.5 до сравнения
    assert weight_category(0.44) == "OTH"


def test_volume_category_boundaries():
    assert volume_category(0) == "OTH"
    assert volume_category(120) == "FTL"
    assert volume_category(130) == "OTH"


def test_train_test_split_fixed():
    train_start, test_start, as_of = train_test_bounds(date(2026, 10, 26))
    assert train_start == date(2026, 7, 26)
    assert test_start == date(2026, 10, 5)   # 05.10 входит только в test
    assert as_of == date(2026, 10, 26)


def test_cyclical_encoding_continuity():
    """31 декабря и 1 января должны быть близки на единичной окружности."""
    df = pd.DataFrame({"loading_date": ["2026-12-31", "2027-01-01"]})
    out = add_cyclical(df)
    dist = math.dist(
        out.loc[0, ["doy_sin", "doy_cos"]].astype(float),
        out.loc[1, ["doy_sin", "doy_cos"]].astype(float),
    )
    assert dist < 0.03  # хорда дуги 2*pi*1.25/365.25 ~= 0.0215
    # sin^2 + cos^2 == 1
    for col in ("month", "dow", "doy"):
        assert np.allclose(out[f"{col}_sin"] ** 2 + out[f"{col}_cos"] ** 2, 1.0)


def test_no_leakage_features():
    from ml.features import FEATURES

    forbidden = {"price", "price_rub", "price_with_vat", "outlier_mark",
                 "price_group_id", "avg_price", "min_km_price", "max_km_price",
                 "customer_cost", "profit", "carrier_cost"}
    assert forbidden.isdisjoint(FEATURES)
