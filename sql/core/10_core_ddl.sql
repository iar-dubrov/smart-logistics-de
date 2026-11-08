-- Core DWH: DDL очищенного слоя. Идемпотентно (IF NOT EXISTS).
-- Слои: public (raw источник) -> core (очищенные факты + справочники) -> mart (витрины).

CREATE SCHEMA IF NOT EXISTS core;

-- Дедуплицированный справочник городов: минимальный cities.id на gc_id
CREATE TABLE IF NOT EXISTS core.cities_dedup AS
SELECT DISTINCT ON (gc_id)
       id AS city_row_id, gc_id, name, full_name_local, latitude, longitude,
       -- парсинг "Город, Регион" -> регион для ML-признаков
       NULLIF(btrim(split_part(full_name_local, ',', 2)), '') AS region
FROM public.cities
ORDER BY gc_id, id;

CREATE UNIQUE INDEX IF NOT EXISTS cities_dedup_gc_id_uidx ON core.cities_dedup (gc_id);

-- Маппинг body_types -> стандартизированные бизнес-группы
CREATE TABLE IF NOT EXISTS core.body_type_groups AS
SELECT id AS body_type_id, name AS body_type_name,
       CASE
         WHEN id IN (56, 57, 58, 59, 60, 61) THEN 'Тент'
         WHEN id IN (4, 46, 47, 48)          THEN 'Рефрижератор'
         WHEN id = 2                         THEN 'Изотерм'
         WHEN id IN (1, 15)                  THEN 'Борт'
         ELSE 'Прочие'
       END AS body_group,
       CASE
         WHEN id IN (56, 57, 58, 59, 60, 61) THEN 56
         WHEN id IN (4, 46, 47, 48)          THEN 4
         WHEN id = 2                         THEN 2
         WHEN id IN (1, 15)                  THEN 15
         ELSE id
       END AS std_id
FROM public.body_types;

CREATE UNIQUE INDEX IF NOT EXISTS body_type_groups_id_uidx ON core.body_type_groups (body_type_id);

-- Очищенные факты сделок: нормализованная валюта, заполненные категории, флаг выброса из источника.
-- Партиционирование логическое по loading_date: перезапись окна = DELETE + INSERT в транзакции.
CREATE TABLE IF NOT EXISTS core.prices_clean (
    price_id            bigint PRIMARY KEY,
    created_at          timestamp,
    organization_id     integer,
    subscriber_id       integer,
    loading_date        date             NOT NULL,
    end_loading_date    date,
    loading_city_gcid   integer          NOT NULL,
    unloading_city_gcid integer          NOT NULL,
    distance            double precision NOT NULL,
    total_weight        double precision NOT NULL,
    total_volume        double precision NOT NULL,
    body_type           integer,
    price_src           double precision NOT NULL,   -- исходная ставка в валюте сделки
    currency_code       varchar(3)       NOT NULL,
    rate_value          double precision NOT NULL,   -- применённый курс (для 643 = 1.0)
    price_rub           double precision NOT NULL,   -- ставка в RUB без НДС
    price_type          smallint         NOT NULL,
    is_fact             boolean          NOT NULL,
    complex             boolean          NOT NULL,
    outlier_mark        boolean          NOT NULL,
    weight_category     varchar(4)       NOT NULL,
    volume_category     varchar(4)       NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_clean_route_idx
    ON core.prices_clean (loading_city_gcid, unloading_city_gcid, weight_category, loading_date);

-- Развертка сделки по группам кузова: сделка с типами из разных групп (напр. тент+борт)
-- даёт по одной строке в каждой группе; внутри одной группы цена не дублируется.
-- Сделка без типа кузова получает группу 'Не указан'.
CREATE TABLE IF NOT EXISTS core.price_body_groups (
    price_id   bigint NOT NULL REFERENCES core.prices_clean (price_id) ON DELETE CASCADE,
    body_group text   NOT NULL,
    is_primary boolean NOT NULL,   -- основная группа сделки (для ML и атрибутов), приоритет ниже
    PRIMARY KEY (price_id, body_group)
);
CREATE INDEX IF NOT EXISTS price_body_groups_group_idx ON core.price_body_groups (body_group, price_id);

-- Помесячная развертка по очищенным сделкам (аналог public.price_dates для core)
CREATE TABLE IF NOT EXISTS core.price_months (
    price_id    bigint NOT NULL REFERENCES core.prices_clean (price_id) ON DELETE CASCADE,
    price_month date   NOT NULL,
    UNIQUE (price_id, price_month)
);

-- Карантин DQ: отбракованные строки с кодом причины, ничего не теряется бесследно
CREATE TABLE IF NOT EXISTS core.dq_quarantine_prices (
    price_id   bigint      NOT NULL,
    rule_code  text        NOT NULL,  -- PRICE_NONPOSITIVE / RUB_BELOW_1000 / BAD_DISTANCE / BAD_WEIGHT / BAD_VOLUME / CITY_UNKNOWN / NO_FX_RATE
    payload    jsonb       NOT NULL,  -- сырой снимок строки источника
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (price_id, rule_code)
);

-- Статистика прогонов ETL (аудит идемпотентности)
CREATE TABLE IF NOT EXISTS core.etl_run_log (
    run_id       bigserial PRIMARY KEY,
    window_start date,
    window_end   date,
    step         text        NOT NULL,
    rows_in      bigint,
    rows_loaded  bigint,
    rows_quarantined bigint,
    finished_at  timestamptz NOT NULL DEFAULT now()
);
