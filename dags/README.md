# dags/ — бонусная реализация на Apache Airflow (задача со звёздочкой)

Здесь размещается DAG `smart_logistics_pipeline`, который вызывает шаги пакета `etl`
(`transform.run`, `load_marts.run`, `ml.train.run_training`, `ml.score.run_scoring`)
как `PythonOperator`, с параметризацией окна через `logical_date`.

Базовая реализация (CLI `python -m etl.main`) полностью самодостаточна и не требует Airflow.
