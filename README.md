# Smart Logistics Data & ML Platform

Сквозной прототип платформы аналитики и прогнозирования тарифов грузоперевозок
(тестовое задание Middle Data Engineer, «Умная Логистика»): ETL → DWH → аналитические
витрины → ML batch-скоринг. Разворачивается одной командой `docker-compose up`.

## Архитектура

```
public (raw источник: schema.sql + synthetic.sql)
   │
   ▼  etl/steps/transform.py  + sql/core/11_core_transform.sql   [Вопрос 1]
core (prices_clean, cities_dedup, body_type_groups, price_body_groups,
      price_months, dq_quarantine_prices, etl_run_log)
   │
   ├──► etl/steps/load_marts.py + sql/mart/21_mart_route_card.sql ─► mart.mart_route_card   [Вопрос 2]
   └──► ml/features.py → ml/train.py → ml/score.py ─► mart.ml_price_predictions             [Вопрос 3]
```

* **СУБД:** PostgreSQL 17, слои `raw → core → mart`. Тяжёлые трансформации — на SQL (ELT),
  Python отвечает за оркестрацию, параметризацию окон, логирование и ML.
* **Идемпотентность:** каждый шаг перезаписывает своё окно дат атомарным `DELETE + INSERT`
  в одной транзакции; повторный прогон и backfill не дают дублей и не меняют агрегаты
  (проверено: контрольные суммы всех таблиц после полного прогона, повторного прогона
  и оконного backfill совпадают побайтно).
* **DQ:** невалидные строки не теряются — карантин `core.dq_quarantine_prices` с `rule_code`
  и сырым payload; статистика прогонов — `core.etl_run_log`.
* **ML:** CatBoostRegressor, строгий time-based split, early stopping на val-хвосте train
  (test не участвует в подборе итераций), защита от утечек (запрещённые признаки
  перечислены в `ml/features.py` и проверяются автотестом), batch-скоринг в БД.

### Ключевые проектные решения и ограничения исходных данных

* Дата снимка **`as_of = 2026-10-26`** фиксирована конфигом (`ML_AS_OF`), системная дата не используется.
* `nearest_rate_date` заполнен относительно `created_at`; конвертация валют — только по точному
  курсу на эту дату, fallback по `loading_date` не применяется (строки без курса → карантин `NO_FX_RATE`).
* Сделки за последний день снимка (2026-10-26) не прошли ночную разметку аномалий и имеют
  `outlier_mark = false` по умолчанию — зафиксированное ограничение источника; флаг в базовом
  решении не пересчитывается.
* Совпадение `loading_city_gcid = unloading_city_gcid` **не** является DQ-правилом.
* Дубли `cities.gc_id` устраняются выбором минимального `cities.id` (`core.cities_dedup`).
* Сделка с несколькими типами кузова разворачивается в `core.price_body_groups`:
  по одной строке на каждую бизнес-группу (внутри группы цена не дублируется).
  Основная группа (`is_primary`, используется в ML) — по приоритету
  Тент > Рефрижератор > Изотерм > Борт > Прочие > Не указан.
* Месячное зерно витрины — из `price_dates.price_date` (долгосрочная заявка присутствует
  в нескольких месяцах); месяцы после `as_of` в историческую статистику не попадают.

## Запуск

```bash
cp .env.example .env
docker-compose up          # postgres:17 + генерация 300k сделок + ETL + витрины + ML train + скоринг
```

Backfill за конкретный период (идемпотентно):

```bash
docker-compose run --rm etl python -m etl.main --start 2026-09-01 --end 2026-09-30
```

Только ETL без ML: `python -m etl.main` (без `--with-ml`).

Юнит-тесты (без БД): `pytest tests/`

## Проверочные SQL-запросы

```sql
-- витрина карточки маршрута
SELECT * FROM mart.mart_route_card
WHERE loading_city = 'Москва' AND unloading_city = 'Казань'
ORDER BY price_month DESC;

-- факт vs прогноз
SELECT loading_date, actual_price_rub, predicted_price_rub, ape_pct
FROM mart.ml_price_predictions
WHERE actual_price_rub IS NOT NULL
ORDER BY ape_pct DESC NULLS LAST LIMIT 20;

-- аудит идемпотентности: два прогона за одно окно дают одинаковые count/sum
SELECT count(*), sum(median_trip) FROM mart.mart_route_card;

-- статистика DQ
SELECT rule_code, count(*) FROM core.dq_quarantine_prices GROUP BY 1 ORDER BY 2 DESC;
```

## Метрики ML (прогон docker-compose на полном датасете, 300 000 сделок)

Split: fit = 21 348 строк, val = 2 878 (хвост train, early stopping), test = 12 808.
Скоринг: 17 696 сделок, из них 4 888 плановых будущих (actual/ошибки = NULL).

| Модель | R² | MAPE, % | RMSE, руб. | MAE, руб. |
|---|---|---|---|---|
| CatBoostRegressor | 0.921 | 15.0 | 53 690 | 27 728 |

Значения близки к референсным ориентирам R&D-отчёта (R² ~0.93, MAPE ~13%).

## Структура репозитория

```
sql/            # schema.sql, synthetic.sql (источник), core/ (DDL+трансформации), mart/ (витрины)
etl/            # пайплайн: config, db, steps/transform, steps/load_marts, pipeline, CLI main
ml/             # features (без утечек), train (time split + CatBoost), score (batch inference)
tests/          # юнит-тесты DQ-правил, сплита, циклических признаков, стоп-листа утечек
docker/         # etl.Dockerfile, init-source.sh
dags/           # (бонус) Airflow DAG
```

