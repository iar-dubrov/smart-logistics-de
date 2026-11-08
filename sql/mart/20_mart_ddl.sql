-- Mart layer: DDL аналитических витрин. Идемпотентно.

CREATE SCHEMA IF NOT EXISTS mart;

-- Витрина "Карточка маршрута и коридор рыночных ставок".
-- Grain: месяц x город отправления x город назначения x группа кузова x весовая категория.
CREATE TABLE IF NOT EXISTS mart.mart_route_card (
    price_month         date   NOT NULL,          -- месяц из price_dates.price_date
    loading_city_gcid   int    NOT NULL,
    loading_city        text   NOT NULL,
    loading_region      text,
    unloading_city_gcid int    NOT NULL,
    unloading_city      text   NOT NULL,
    unloading_region    text,
    body_group          text   NOT NULL,
    weight_category     varchar(4) NOT NULL,
    volume_category     varchar(4),               -- атрибут, не входит в grain
    -- коридор за рейс, руб.
    p25_trip            double precision,
    median_trip         double precision,
    p75_trip            double precision,
    avg_trip            double precision,
    -- коридор за км, руб./км
    p25_km              double precision,
    median_km           double precision,
    p75_km              double precision,
    -- доказательная база
    trips_count         bigint NOT NULL,
    carriers_count      bigint NOT NULL,
    cv_rate             double precision,
    mom_pct             double precision,
    yoy_pct             double precision,
    reliability_level   text   NOT NULL,          -- HIGH / MEDIUM / LOW_DATA
    as_of               date   NOT NULL,
    refreshed_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (price_month, loading_city_gcid, unloading_city_gcid, body_group, weight_category)
);

-- Витрина пакетного ML-скоринга "Факт vs Прогноз"
CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    price_id            bigint NOT NULL,
    loading_date        date   NOT NULL,
    loading_city_gcid   int    NOT NULL,
    unloading_city_gcid int    NOT NULL,
    body_group          text,
    actual_price_rub    double precision,         -- NULL для будущих плановых заявок
    predicted_price_rub double precision NOT NULL,
    abs_error_rub       double precision,         -- NULL для будущих
    ape_pct             double precision,         -- NULL для будущих
    model_name          text   NOT NULL,
    scored_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (price_id, model_name)
);
CREATE INDEX IF NOT EXISTS ml_price_predictions_date_idx ON mart.ml_price_predictions (loading_date);
