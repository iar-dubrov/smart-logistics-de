-- Расчёт витрины mart.mart_route_card (Вопрос 2).
-- Идемпотентность: атомарная перезапись месяцев окна в одной транзакции (DELETE + INSERT),
-- MoM/YoY пересчитываются отдельным UPDATE после вставки (окно затрагивает соседние месяцы).
-- Параметры: %(start)s, %(end)s (месяцы, inclusive; NULL = все), %(as_of)s::date.

WITH facts AS (
    SELECT pm.price_month,
           pc.loading_city_gcid, lc.name AS loading_city, lc.region AS loading_region,
           pc.unloading_city_gcid, uc.name AS unloading_city, uc.region AS unloading_region,
           pbg.body_group, pc.weight_category, pc.volume_category,
           pc.price_id, pc.organization_id, pc.price_type,
           pc.price_rub, pc.price_rub / NULLIF(pc.distance, 0) AS price_km
    FROM core.prices_clean pc
    JOIN core.price_months pm ON pm.price_id = pc.price_id
    -- сделка с типами из разных групп учитывается в каждой группе один раз (PK price_body_groups)
    JOIN core.price_body_groups pbg ON pbg.price_id = pc.price_id
    JOIN core.cities_dedup lc ON lc.gc_id = pc.loading_city_gcid
    JOIN core.cities_dedup uc ON uc.gc_id = pc.unloading_city_gcid
    WHERE pc.is_fact                                   -- только завершённые перевозки (price_type 1,2,3,4,5,9)
      AND NOT pc.complex                               -- простые прямые маршруты
      AND NOT pc.outlier_mark                          -- без статистических выбросов
      AND pc.price_rub > 0 AND pc.distance > 0
      AND pm.price_month <= date_trunc('month', %(as_of)s::date)::date  -- будущие плановые месяцы не в истории
      AND (%(start)s::date IS NULL OR pm.price_month >= %(start)s::date)
      AND (%(end)s::date   IS NULL OR pm.price_month <= %(end)s::date)
),
agg AS (
    SELECT price_month, loading_city_gcid, loading_city, loading_region,
           unloading_city_gcid, unloading_city, unloading_region,
           body_group, weight_category,
           min(volume_category) AS volume_category,      -- атрибут ячейки (справочно)
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub) AS p25_trip,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub) AS median_trip,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub) AS p75_trip,
           avg(price_rub)                                          AS avg_trip,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price_km)  AS p25_km,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY price_km)  AS median_km,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price_km)  AS p75_km,
           count(DISTINCT price_id)                                AS trips_count,
           count(DISTINCT organization_id) FILTER (WHERE price_type IN (3, 9)) AS carriers_count,
           CASE WHEN count(*) >= 2 AND avg(price_rub) <> 0
                THEN stddev_samp(price_rub) / avg(price_rub) END   AS cv_rate
    FROM facts
    GROUP BY 1,2,3,4,5,6,7,8,9
)
INSERT INTO mart.mart_route_card (
    price_month, loading_city_gcid, loading_city, loading_region,
    unloading_city_gcid, unloading_city, unloading_region,
    body_group, weight_category, volume_category,
    p25_trip, median_trip, p75_trip, avg_trip, p25_km, median_km, p75_km,
    trips_count, carriers_count, cv_rate, mom_pct, yoy_pct, reliability_level, as_of
)
SELECT a.*, NULL, NULL,
       CASE
         WHEN a.trips_count >= 30 AND a.carriers_count >= 5
              AND a.price_month >= (%(as_of)s::date - interval '30 days')::date THEN 'HIGH'
         WHEN a.trips_count >= 10 THEN 'MEDIUM'
         ELSE 'LOW_DATA'
       END,
       %(as_of)s::date
FROM agg a;

-- MoM/YoY: строго календарный предыдущий месяц / тот же месяц год назад; если ячейки нет — NULL.
-- Пересчёт выполняется по всей витрине после вставки окна: MoM/YoY соседних месяцев
-- (окно+1 мес., окно+12 мес.) зависят от только что обновлённых ячеек.
UPDATE mart.mart_route_card cur
SET mom_pct = m.mom_pct,
    yoy_pct = m.yoy_pct
FROM (
    SELECT c.price_month, c.loading_city_gcid, c.unloading_city_gcid, c.body_group, c.weight_category,
           CASE WHEN prev.median_trip IS NULL OR prev.median_trip = 0 THEN NULL
                ELSE (c.median_trip / prev.median_trip - 1) * 100 END AS mom_pct,
           CASE WHEN prev_y.median_trip IS NULL OR prev_y.median_trip = 0 THEN NULL
                ELSE (c.median_trip / prev_y.median_trip - 1) * 100 END AS yoy_pct
    FROM mart.mart_route_card c
    LEFT JOIN mart.mart_route_card prev
      ON prev.price_month = (c.price_month - interval '1 month')::date
     AND prev.loading_city_gcid = c.loading_city_gcid
     AND prev.unloading_city_gcid = c.unloading_city_gcid
     AND prev.body_group = c.body_group
     AND prev.weight_category = c.weight_category
    LEFT JOIN mart.mart_route_card prev_y
      ON prev_y.price_month = (c.price_month - interval '12 months')::date
     AND prev_y.loading_city_gcid = c.loading_city_gcid
     AND prev_y.unloading_city_gcid = c.unloading_city_gcid
     AND prev_y.body_group = c.body_group
     AND prev_y.weight_category = c.weight_category
) m
WHERE cur.price_month = m.price_month
  AND cur.loading_city_gcid = m.loading_city_gcid
  AND cur.unloading_city_gcid = m.unloading_city_gcid
  AND cur.body_group = m.body_group
  AND cur.weight_category = m.weight_category;
