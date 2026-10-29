# -*- coding: utf-8 -*-
"""导出脱敏房源数据为 CSV（供协作与联调使用）

排除字段：url / raw_json / search_text / geom / verify_code
—— 包含来源地址、内部编号或非通用信息，不随仓库分发。

用法：
  python db/export_seed.py
输出：
  data/listings_seed.csv（UTF-8 with BOM，Excel 可直接打开）
"""
import csv
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DSN = os.getenv(
    "PG_DSN",
    "host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev",
)
EXCLUDE = {"url", "raw_json", "search_text", "geom", "verify_code"}
OUT = Path(__file__).resolve().parent.parent / "data" / "listings_seed.csv"


def main():
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        cols = [
            r["column_name"]
            for r in conn.execute(
                "select column_name from information_schema.columns "
                "where table_name = 'listings' order by ordinal_position"
            ).fetchall()
        ]
        cols = [c for c in cols if c not in EXCLUDE]
        rows = conn.execute(
            f"select {', '.join(cols)} from listings order by id"
        ).fetchall()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    k: (
                        ""
                        if v is None
                        else json.dumps(v, ensure_ascii=False)
                        if isinstance(v, (list, dict))
                        else v
                    )
                    for k, v in row.items()
                }
            )

    print(f"已导出 {len(rows)} 条 -> {OUT}")


if __name__ == "__main__":
    main()
