#!/bin/bash
# Инициализация тестового датасета. Выполняется docker-entrypoint-initdb.d
# после 01_schema.sql. N_PRICES пробрасывается из окружения контейнера.
set -euo pipefail

# N_PRICES приходит из окружения и подставляется в SQL текстуально — валидируем,
# что это положительное целое, иначе прерываем инициализацию
N="${N_PRICES:-300000}"
case "$N" in
  ''|*[!0-9]*) echo "[init-source] ERROR: N_PRICES must be a positive integer, got: $N" >&2; exit 1 ;;
esac
if [ "$N" -lt 1 ]; then echo "[init-source] ERROR: N_PRICES must be >= 1" >&2; exit 1; fi

echo "[init-source] generating ${N} synthetic prices ..."
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
     -v n_prices="$N" -f /sql/synthetic.sql
echo "[init-source] done"
