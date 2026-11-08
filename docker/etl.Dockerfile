FROM python:3.11-slim

WORKDIR /app

# Зависимости ставим из локальных wheel (docker/wheels/)
# с PyPI при каждой сборке. Если каталог пуст — pip возьмёт пакеты из PyPI.
COPY docker/wheels/ /tmp/wheels/
COPY requirements.txt .
RUN if ls /tmp/wheels/*.whl >/dev/null 2>&1; then \
      pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi && rm -rf /tmp/wheels

COPY sql/ ./sql/
COPY etl/ ./etl/
COPY ml/ ./ml/

# работаем не из-под root; /app/models — каталог для сохранения обученной модели
RUN useradd -m appuser && mkdir -p /app/models && chown -R appuser:appuser /app/models
USER appuser

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "etl.main"]
