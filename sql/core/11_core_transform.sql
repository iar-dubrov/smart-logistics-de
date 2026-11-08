-- Трансформация raw -> core за окно дат (%(start)s .. %(end)s по loading_date; NULL = без границы).
-- Идемпотентность: вся трансформация выполняется в одной транзакции, целевые строки окна
-- сначала удаляются (DELETE), затем пересчитываются (INSERT). Повторный прогон не даёт дублей.
-- Выполняется из etl/steps/transform.py через psycopg с параметрами.

-- 0. Параметры окна доступны как %(start)s::date и %(end)s::date (inclusive).
--    Условие окна: (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
--              AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date)

-- 1. Удаляем прежние результаты окна (price_id из окна источника)
DELETE FROM core.prices_clean pc
USING public.prices p
WHERE pc.price_id = p.id
  AND (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date);

DELETE FROM core.dq_quarantine_prices q
USING public.prices p
WHERE q.price_id = p.id
  AND (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date);

-- 2. Карантин невалидных строк (каждое нарушенное правило = отдельная строка с rule_code)
INSERT INTO core.dq_quarantine_prices (price_id, rule_code, payload)
SELECT p.id, v.rule_code, to_jsonb(p)
FROM public.prices p
CROSS JOIN LATERAL (VALUES
    ('PRICE_NONPOSITIVE', p.price <= 0),
    ('RUB_BELOW_1000',    p.currency_code = '643' AND p.price <= 1000),
    ('BAD_DISTANCE',      p.distance <= 0),
    ('BAD_WEIGHT',        p.total_weight < 0),
    ('BAD_VOLUME',        p.total_volume < 0),
    ('CITY_UNKNOWN',      NOT EXISTS (SELECT 1 FROM core.cities_dedup c WHERE c.gc_id = p.loading_city_gcid)
                       OR NOT EXISTS (SELECT 1 FROM core.cities_dedup c WHERE c.gc_id = p.unloading_city_gcid)),
    ('NO_FX_RATE',        p.currency_code <> '643' AND (p.nearest_rate_date IS NULL OR NOT EXISTS (
                            SELECT 1 FROM public.exchange_rates er
                            WHERE er.num_code = p.currency_code AND er.setting_date = p.nearest_rate_date)))
) AS v(rule_code, violated)
WHERE v.violated
  AND (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date);

-- 3. Загрузка очищенных фактов: конвертация валют, заполнение категорий.
--    Строки с хотя бы одним нарушением (есть в карантине за это окно) в core не попадают.
--    Совпадение loading_city_gcid = unloading_city_gcid НЕ является DQ-правилом (см. README задания).
INSERT INTO core.prices_clean (
    price_id, created_at, organization_id, subscriber_id, loading_date, end_loading_date,
    loading_city_gcid, unloading_city_gcid, distance, total_weight, total_volume,
    body_type, price_src, currency_code, rate_value, price_rub,
    price_type, is_fact, complex, outlier_mark, weight_category, volume_category
)
SELECT p.id, p.created_at, p.organization_id, p.subscriber_id, p.loading_date, p.end_loading_date,
       p.loading_city_gcid, p.unloading_city_gcid, p.distance, p.total_weight, p.total_volume,
       p.body_type,
       p.price, p.currency_code,
       fx.rate_value,
       p.price * fx.rate_value,
       p.price_type, p.is_fact, p.complex, p.outlier_mark,
       -- weight_category: заполняем пропуски по весу с округлением до 0.1 т
       COALESCE(p.weight_category, CASE
           WHEN round(p.total_weight::numeric, 1) >= 30  THEN 'G20T'
           WHEN round(p.total_weight::numeric, 1) >= 10  THEN 'FTL'
           WHEN round(p.total_weight::numeric, 1) >= 5   THEN '10T'
           WHEN round(p.total_weight::numeric, 1) >= 2.5 THEN '5T'
           WHEN round(p.total_weight::numeric, 1) >= 0.5 THEN 'L5T'
           ELSE 'OTH' END),
       -- volume_category: FTL при 0 < V <= 120 м3, иначе OTH
       COALESCE(p.volume_category,
           CASE WHEN p.total_volume > 0 AND p.total_volume <= 120 THEN 'FTL' ELSE 'OTH' END)
FROM public.prices p
-- курс по точной дате nearest_rate_date; fallback по loading_date не применяется
CROSS JOIN LATERAL (
    SELECT COALESCE(er.value, 1.0) AS rate_value
    FROM (SELECT 1) one
    LEFT JOIN public.exchange_rates er
      ON er.num_code = p.currency_code AND er.setting_date = p.nearest_rate_date
    WHERE p.currency_code = '643' OR er.value IS NOT NULL
    LIMIT 1
) fx
WHERE NOT EXISTS (
        SELECT 1 FROM core.dq_quarantine_prices q WHERE q.price_id = p.id
      )
  AND (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date);

-- 3b. Развертка сделок по группам кузова. Источники типов: связь price_body_types,
--     fallback на prices.body_type (если связей нет), иначе 'Не указан'.
--     Одна цена внутри одной группы не дублируется (DISTINCT по группе);
--     сделка с типами из разных групп присутствует в каждой группе один раз.
--     Основная группа (is_primary) — по бизнес-приоритету: Тент > Рефрижератор > Изотерм > Борт > Прочие > Не указан.
DELETE FROM core.price_body_groups pbg
USING public.prices p
WHERE pbg.price_id = p.id
  AND (%(start)s::date IS NULL OR p.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR p.loading_date <= %(end)s::date);

INSERT INTO core.price_body_groups (price_id, body_group, is_primary)
SELECT price_id, body_group,
       row_number() OVER (
           PARTITION BY price_id
           ORDER BY CASE body_group WHEN 'Тент' THEN 1 WHEN 'Рефрижератор' THEN 2
                                    WHEN 'Изотерм' THEN 3 WHEN 'Борт' THEN 4
                                    WHEN 'Прочие' THEN 5 ELSE 6 END
       ) = 1 AS is_primary
FROM (
    SELECT DISTINCT pc.price_id, g.body_group
    FROM core.prices_clean pc
    JOIN (
        SELECT pbt.price_id, pbt.body_type_id AS id FROM public.price_body_types pbt
        UNION ALL
        SELECT p.id, p.body_type FROM public.prices p
        WHERE p.body_type IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM public.price_body_types pbt WHERE pbt.price_id = p.id)
    ) bt ON bt.price_id = pc.price_id
    JOIN core.body_type_groups g ON g.body_type_id = bt.id
    WHERE (%(start)s::date IS NULL OR pc.loading_date >= %(start)s::date)
      AND (%(end)s::date   IS NULL OR pc.loading_date <= %(end)s::date)
) grp;

-- сделки без типа кузова -> группа 'Не указан'
INSERT INTO core.price_body_groups (price_id, body_group, is_primary)
SELECT pc.price_id, 'Не указан', true
FROM core.prices_clean pc
WHERE NOT EXISTS (SELECT 1 FROM core.price_body_groups pbg WHERE pbg.price_id = pc.price_id)
  AND (%(start)s::date IS NULL OR pc.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR pc.loading_date <= %(end)s::date)
ON CONFLICT (price_id, body_group) DO NOTHING;

-- 4. Помесячная развертка очищенных сделок (месяц из price_dates.price_date)
DELETE FROM core.price_months pm
USING core.prices_clean pc
WHERE pm.price_id = pc.price_id
  AND (%(start)s::date IS NULL OR pc.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR pc.loading_date <= %(end)s::date);

INSERT INTO core.price_months (price_id, price_month)
SELECT pd.price_id, date_trunc('month', pd.price_date)::date
FROM public.price_dates pd
JOIN core.prices_clean pc ON pc.price_id = pd.price_id
WHERE (%(start)s::date IS NULL OR pc.loading_date >= %(start)s::date)
  AND (%(end)s::date   IS NULL OR pc.loading_date <= %(end)s::date)
ON CONFLICT (price_id, price_month) DO NOTHING;
