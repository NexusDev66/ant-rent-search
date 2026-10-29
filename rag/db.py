# -*- coding: utf-8 -*-
"""数据访问层：PostgreSQL(PostGIS) 为主库

环境变量：
  RAG_DB       postgres | sqlite   （默认 postgres）
  PG_DSN       默认 host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "listings.db"
BACKEND = os.getenv("RAG_DB", "postgres").lower()
PG_DSN = os.getenv(
    "PG_DSN",
    "host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev",
)

# 占位符风格：PG 用 %s，SQLite 用 ?
PH = "%s" if BACKEND == "postgres" else "?"
# 布尔字面量：PG 是 TRUE/FALSE，SQLite 是 1/0
TRUE = "TRUE" if BACKEND == "postgres" else "1"


@contextmanager
def get_conn():
    if BACKEND == "postgres":
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(PG_DSN, row_factory=dict_row, client_encoding="utf8")
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def is_postgres():
    return BACKEND == "postgres"
