# -*- coding: utf-8 -*-
"""SQLite -> PostgreSQL(PostGIS) 同步
用法: python db/migrate_to_pg.py
幂等：按 id UPSERT；SQLite 里下架的房源在 PG 里同步置为 is_active=false
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))

import psycopg
from psycopg.types.json import Jsonb

DB_PATH = ROOT / "db" / "listings.db"
SCHEMA = ROOT / "db" / "schema_pg.sql"
DSN = os.getenv(
    "PG_DSN",
    "host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev",
)

BOOL_COLS = {"is_south_facing", "elevator", "ownership_checked",
             "has_watermark_free", "is_active", "pets_allowed"}
JSON_COLS = {"quality_flags", "risk_flags", "tags", "amenities", "raw_json"}

src = sqlite3.connect(DB_PATH)
src.row_factory = sqlite3.Row
cols = [r[1] for r in src.execute("PRAGMA table_info(listings)")]
cols.remove("geom") if "geom" in cols else None
rows = src.execute("SELECT * FROM listings").fetchall()

pg_cols = [c for c in cols]
placeholders = ", ".join(
    f"%s::jsonb" if c in JSON_COLS else "%s" for c in pg_cols)
updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in pg_cols if c != "id")
sql = (f"INSERT INTO listings ({', '.join(pg_cols)}) VALUES ({placeholders}) "
       f"ON CONFLICT (id) DO UPDATE SET {updates}")

def conv(row, c):
    v = row[c]
    if c in BOOL_COLS:
        return None if v is None else bool(v)
    if c in JSON_COLS:
        return json.dumps(json.loads(v) if v else [], ensure_ascii=False) \
            if c != "raw_json" else (v or "null")
    return v

with psycopg.connect(DSN) as conn:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    n = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, [conv(row, c) for c in pg_cols])
            n += 1
    conn.commit()
    cur = conn.execute("SELECT COUNT(*) AS n, COUNT(geom) AS geo FROM listings")
    r = cur.fetchone()
    print(f"PG 同步完成: {n} 行写入 | 库内总数 {r[0]} | 有坐标 {r[1]}")
    print("数据源: SQLite" + f" ({DB_PATH})")
