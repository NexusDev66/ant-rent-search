# -*- coding: utf-8 -*-
"""按小区名回填房源坐标（高德 / 百度 POI 搜索）

房源原始数据只有小区名与行政区，没有经纬度，因此需要地理编码。
同一个小区的多次请求会复用缓存，只查一次。

坐标统一转换为 WGS84（EPSG:4326）入库：
  高德返回 GCJ02，百度返回 BD09，两者都与 WGS84 有偏移，必须先转换。

用法：
  set AMAP_KEY=你的高德Key
  python db/backfill_geom.py --dry

  set BAIDU_AK=你的百度AK
  python db/backfill_geom.py --source baidu --dry
  python db/backfill_geom.py --source baidu

  python db/backfill_geom.py --limit 10   # 小批量试
  python db/backfill_geom.py --all        # 忽略已有坐标，全部重算

环境变量：
  AMAP_KEY   高德 Web 服务 Key（服务平台需选「Web服务」）
  BAIDU_AK   百度地图服务端 AK
  PG_DSN     默认 host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev
"""
import argparse
import json
import math
import os
import socket
import sys
import time
import urllib.parse
import urllib.request

import psycopg
from psycopg.rows import dict_row

DSN = os.getenv(
    "PG_DSN",
    "host=127.0.0.1 port=5433 dbname=antrent user=antrent password=ant_rent_dev",
)

AMAP_API = "https://restapi.amap.com/v3/place/text"
BAIDU_API = "https://api.map.baidu.com/place/v2/search"

# ---------- 坐标系转换：GCJ02 / BD09 -> WGS84 ----------
PI = 3.141592653589793
AXIS = 6378245.0
EE = 0.00669342162296594323
X_PI = PI * 3000.0 / 180.0


def _out_of_china(lng, lat):
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _dlat(x, y):
    r = (
        -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    r += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    r += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0
    r += (160.0 * math.sin(y / 12.0 * PI) + 320 * math.sin(y * PI / 30.0)) * 2.0 / 3.0
    return r


def _dlng(x, y):
    r = (
        300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
        + 0.1 * math.sqrt(abs(x))
    )
    r += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    r += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0
    r += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0
    return r


def gcj02_to_wgs84(lng, lat):
    if _out_of_china(lng, lat):
        return lng, lat
    d_lat = _dlat(lng - 105.0, lat - 35.0)
    d_lng = _dlng(lng - 105.0, lat - 35.0)
    rad = lat / 180.0 * PI
    magic = math.sin(rad)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((AXIS * (1 - EE)) / (magic * sqrt_magic) * PI)
    d_lng = (d_lng * 180.0) / (AXIS / sqrt_magic * math.cos(rad) * PI)
    return lng - d_lng, lat - d_lat


def bd09_to_gcj02(bd_lng, bd_lat):
    x = bd_lng - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    return z * math.cos(theta), z * math.sin(theta)


# ---------- 网络 ----------
def _force_ipv4():
    """部分网络环境优先走 IPv6，而百度的 IP 白名单只校验 IPv4，因此强制走 IPv4。"""
    original = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [
        x for x in original(*a, **k) if x[0] == socket.AF_INET
    ]


# ---------- 地理编码 ----------
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ant-rent-backfill/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode_amap(key, community, district):
    qs = urllib.parse.urlencode(
        {"key": key, "keywords": community, "city": district or "成都",
         "citylimit": "true", "offset": 1, "page": 1}
    )
    data = _get_json(f"{AMAP_API}?{qs}")
    if str(data.get("status")) != "1":
        return None, f"api:{data.get('info') or data.get('infocode')}"
    pois = data.get("pois") or []
    if not pois:
        return None, "no_poi"
    loc = (pois[0].get("location") or "").strip()
    if "," not in loc:
        return None, "no_location"
    lng, lat = (float(x) for x in loc.split(",")[:2])
    return gcj02_to_wgs84(lng, lat), None


def geocode_baidu(key, community, district):
    qs = urllib.parse.urlencode(
        {"query": community, "region": district or "成都", "city_limit": "true",
         "output": "json", "scope": 1, "page_size": 1, "ak": key}
    )
    data = _get_json(f"{BAIDU_API}?{qs}")
    if data.get("status") != 0:
        return None, f"api:{data.get('message') or data.get('status')}"
    results = data.get("results") or []
    if not results:
        return None, "no_poi"
    loc = results[0].get("location") or {}
    lng, lat = loc.get("lng"), loc.get("lat")
    if lng is None or lat is None:
        return None, "no_location"
    return gcj02_to_wgs84(*bd09_to_gcj02(float(lng), float(lat))), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["amap", "baidu"], default="amap")
    ap.add_argument("--all", action="store_true", help="忽略已有坐标，全部重算")
    ap.add_argument("--dry", action="store_true", help="只统计命中率，不写库")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条")
    args = ap.parse_args()

    _force_ipv4()

    key = (os.getenv("BAIDU_AK") if args.source == "baidu" else os.getenv("AMAP_KEY") or "").strip()
    if not key:
        name = "BAIDU_AK" if args.source == "baidu" else "AMAP_KEY"
        print(f"缺少 {name} 环境变量")
        sys.exit(1)

    geocode = (geocode_baidu if args.source == "baidu" else geocode_amap)

    where = "" if args.all else "where geom is null"
    sql = f"select id, community, district from listings {where} order by district, community"
    if args.limit:
        sql += f" limit {args.limit}"

    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        rows = conn.execute(sql).fetchall()
        print(f"数据源 {args.source}，待处理 {len(rows)} 条")

        cache, ok, fail, failures = {}, 0, 0, []

        for i, row in enumerate(rows, 1):
            name = (row["community"] or "").strip()
            district = (row["district"] or "").strip()
            if not name:
                fail += 1
                failures.append(("", "empty_community"))
                continue

            cache_key = (name, district)
            if cache_key not in cache:
                cache[cache_key] = geocode(key, name, district)
                time.sleep(0.25)

            point, err = cache[cache_key]
            if not point:
                fail += 1
                failures.append((name, err))
                continue

            if not args.dry:
                conn.execute(
                    "update listings set geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326), "
                    "updated_at = now() where id = %s",
                    (point[0], point[1], row["id"]),
                )
            ok += 1
            if i % 20 == 0:
                print(f"  进度 {i}/{len(rows)}  成功 {ok}  失败 {fail}")

        if not args.dry:
            conn.commit()

        print(f"\n完成：成功 {ok}，失败 {fail}，去重小区 {len(cache)} 个")
        if failures:
            print("\n未命中明细：")
            for name, err in failures[:30]:
                print(f"  {name or '(空)'} -> {err}")
            if len(failures) > 30:
                print(f"  ... 另有 {len(failures) - 30} 条")


if __name__ == "__main__":
    main()
