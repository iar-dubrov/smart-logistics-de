"""Работа с БД: соединение, выполнение SQL-файлов с параметрами, логирование rowcount."""
from __future__ import annotations

import logging
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

SQL_ROOT = Path(__file__).resolve().parent.parent / "sql"


def connect(settings, connect_timeout: int | None = None):
    # keyword-аргументы вместо DSN-строки: пароль с особыми символами не ломает парсинг
    kwargs = {"connect_timeout": connect_timeout} if connect_timeout else {}
    return psycopg.connect(
        host=settings.pg_host, port=settings.pg_port, dbname=settings.pg_db,
        user=settings.pg_user, password=settings.pg_password,
        autocommit=False, **kwargs,
    )


def _split_statements(sql: str) -> list[str]:
    """Разбивает скрипт на операторы по ';' вне строковых литералов и комментариев."""
    stmts, buf, i, n = [], [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(sql[i])
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                buf.append(sql[i])
                i += 1
            continue
        if ch == ";":
            stmts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    stmts.append("".join(buf))
    return [s for s in (s.strip() for s in stmts) if s and s.strip("- \n")]


def exec_sql_file(conn, path: Path, params: dict | None = None) -> None:
    """Выполняет SQL-файл по операторам в текущей транзакции (psycopg3 не принимает
    multi-statement с серверными параметрами)."""
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        for stmt in _split_statements(sql):
            cur.execute(stmt, params or {})
    log.info("applied %s", path.name)


def scalar(conn, sql: str, params: dict | None = None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None
