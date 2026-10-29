# -*- coding: utf-8 -*-
"""把 data/listings_seed.csv 导入 PostgreSQL（幂等：按 id 覆盖）

由 CSV 表头自动生成列清单，因此不需要手写 39 个字段名。

用法：
  python db/load_seed.py
  python db/load_seed.py --csv 其他文件.csv

流程：COPY 到临时表 → INSERT ... ON CONFLICT (id) DO UPDATE
"""
import argparse
import csv
import os
from pathlib import Path

import psycopg

DSN = os.getenv(
    "PG_DSN",
    "host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev",
)
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "listings_seed.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(CSV_PATH))
    args = ap.parse_args()

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"找不到 CSV: {path}")

    with path.open(encoding="utf-8-sig", newline="") as f:
        cols = next(csv.reader(f))
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("create temp table _seed (like listings) on commit drop")
            with path.open(encoding="utf-8-sig", newline="") as f:
                with cur.copy(
                    f"copy _seed ({col_list}) from stdin "
                    f"with (format csv, header true, null '')"
                ) as cp:
                    for chunk in iter(lambda: f.read(65536), ""):
                        cp.write(chunk)
            cur.execute(
                f"insert into listings ({col_list}) select {col_list} from _seed "
                f"on conflict (id) do update set {updates}"
            )
            affected = cur.rowcount
        conn.commit()

    print(f"导入完成，影响 {affected} 条")


if __name__ == "__main__":
    main()
