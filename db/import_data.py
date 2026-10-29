# -*- coding: utf-8 -*-
"""
agent.jsonl -> SQLite 幂等导入
- 以房源 id 为主键：新 id 插入，已存在更新
- 本次未出现的 id 标记 is_active=0（下架，不删除）
用法: python import_data.py [jsonl路径]
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "listings.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
DEFAULT_JSONL = BASE_DIR.parent.parent / "房源数据" / "data_for_agent" / "agent.jsonl"

FIELDS = [
    "id", "url", "city", "district", "community",
    "rent_cny_per_month", "rent_cny_per_sqm_month", "months_per_payment", "deposit_months",
    "area_sqm", "bedrooms", "living_rooms", "bathrooms", "layout_label",
    "floor_position", "total_floors", "orientation_primary", "is_south_facing",
    "decoration_tier", "decoration_level",
    "rent_type", "elevator", "move_in_type", "ownership_checked", "verify_code",
    "price_band_cny", "size_band_sqm", "price_position_label", "vs_cohort_pct",
    "quality_flags", "risk_flags", "tags", "amenities",
    "headline", "search_text",
    "has_watermark_free", "photo_total",
    "updated_at", "collected_at", "imported_at", "raw_json",
]


def _b(v):
    """bool/None -> 0/1/None"""
    return None if v is None else int(bool(v))


def flatten(rec: dict, now: str) -> dict:
    f = rec.get("facts", {})
    price = f.get("price", {})
    layout = f.get("layout", {})
    floor = f.get("floor", {})
    orient = f.get("orientation", {})
    deco = f.get("decoration", {})
    verified = f.get("verified", {})
    loc = rec.get("location", {})
    cls = rec.get("classification", {})
    pp = cls.get("price_position", {}) or {}
    media = rec.get("media", {})
    counts = media.get("counts", {})
    retr = rec.get("retrieval", {})
    prov = rec.get("provenance", {})

    return {
        "id": rec["id"],
        "url": rec.get("url"),
        "city": loc.get("city"),
        "district": loc["district"],
        "community": loc.get("community"),
        "rent_cny_per_month": price["rent_cny_per_month"],
        "rent_cny_per_sqm_month": price.get("rent_cny_per_sqm_month"),
        "months_per_payment": price.get("months_per_payment"),
        "deposit_months": price.get("deposit_months"),
        "area_sqm": f["area_sqm"],
        "bedrooms": layout["bedrooms"],
        "living_rooms": layout.get("living_rooms"),
        "bathrooms": layout.get("bathrooms"),
        "layout_label": layout.get("label"),
        "floor_position": floor.get("position"),
        "total_floors": floor.get("total_floors"),
        "orientation_primary": orient.get("primary"),
        "is_south_facing": _b(orient.get("is_south_facing")),
        "decoration_tier": deco.get("tier"),
        "decoration_level": deco.get("level"),
        "rent_type": f["rent_type"],
        "elevator": _b(f.get("elevator")),
        "move_in_type": (f.get("move_in") or {}).get("type"),
        "ownership_checked": _b(verified.get("ownership_checked")),
        "verify_code": verified.get("verify_code"),
        "price_band_cny": cls.get("price_band_cny"),
        "size_band_sqm": cls.get("size_band_sqm"),
        "price_position_label": pp.get("label"),
        "vs_cohort_pct": pp.get("vs_cohort_pct"),
        "quality_flags": json.dumps(cls.get("quality_flags", []), ensure_ascii=False),
        "risk_flags": json.dumps(cls.get("risk_flags", []), ensure_ascii=False),
        "tags": json.dumps(f.get("tags", []), ensure_ascii=False),
        "amenities": json.dumps(f.get("amenities", []), ensure_ascii=False),
        "headline": retr.get("headline"),
        "search_text": retr.get("search_text"),
        "has_watermark_free": _b(media.get("has_watermark_free")),
        "photo_total": counts.get("total"),
        "updated_at": f.get("updated_at"),
        "collected_at": prov.get("collected_at"),
        "imported_at": now,
        "raw_json": json.dumps(rec, ensure_ascii=False),
    }


def main():
    jsonl_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSONL
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

    records = {}
    with open(jsonl_path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rec = json.loads(line)
                records[rec["id"]] = rec  # 文件内重复 id 取最后一条

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # 旧库补列（幂等迁移）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    for col, ddl in (("pets_allowed", "INTEGER"),):
        if col not in cols:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {ddl}")

    placeholders = ", ".join(f":{c}" for c in FIELDS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in FIELDS if c != "id")
    upsert = (
        f"INSERT INTO listings ({', '.join(FIELDS)}, is_active) "
        f"VALUES ({placeholders}, 1) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}, is_active=1"
    )

    with conn:
        for rec in records.values():
            conn.execute(upsert, flatten(rec, now))
        # 本批次未出现的 id -> 下架
        cur = conn.execute("SELECT id FROM listings WHERE is_active=1")
        stale = [r[0] for r in cur if r[0] not in records]
        for sid in stale:
            conn.execute("UPDATE listings SET is_active=0, imported_at=? WHERE id=?", (now, sid))

    total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0]

    # 可选：宠物标注 CSV（id,pets_allowed），存在则覆盖写入
    pets_csv = BASE_DIR.parent.parent / "房源数据" / "pets.csv"
    labeled = 0
    if pets_csv.exists():
        import csv
        with open(pets_csv, encoding="utf-8-sig", newline="") as fp:
            for row in csv.DictReader(fp):
                v = (row.get("pets_allowed") or "").strip()
                if row.get("id") and v in ("0", "1"):
                    cur = conn.execute("UPDATE listings SET pets_allowed=? WHERE id=?",
                                       (int(v), row["id"].strip()))
                    labeled += cur.rowcount
        conn.commit()
    conn.close()

    print(f"数据源: {jsonl_path}")
    print(f"本批房源: {len(records)} 条 | 新标记下架: {len(stale)} 条")
    print(f"库内总数: {total} | 有效: {active}")
    if labeled:
        print(f"宠物标注已应用: {labeled} 条")
    n_pets = sqlite3.connect(DB_PATH).execute(
        "SELECT COUNT(*) FROM listings WHERE pets_allowed IS NOT NULL").fetchone()[0]
    print(f"已标注宠物政策: {n_pets}/{total}")


if __name__ == "__main__":
    main()
