# -*- coding: utf-8 -*-
"""
房源结构化查询层 — 对应验收标准 B 组
所有筛选走 SQL 精确匹配，不依赖 LLM 即兴写 SQL。
CLI 自测: python retrieve.py
"""
import json

from db import PH, TRUE, get_conn, is_postgres

# 数值型字段（PG 返回 Decimal，需转 float 才能 JSON 序列化）
NUM_FIELDS = ("rent_cny_per_sqm_month", "area_sqm", "vs_cohort_pct", "deposit_months")

# 结果返回给 LLM/前端的字段（url 不返回：会暴露第三方平台域名）
RESULT_COLS = [
    "id", "district", "community", "headline",
    "rent_cny_per_month", "rent_cny_per_sqm_month", "area_sqm",
    "bedrooms", "living_rooms", "bathrooms", "layout_label",
    "orientation_primary", "is_south_facing",
    "floor_position", "total_floors", "elevator",
    "decoration_tier", "rent_type", "move_in_type", "ownership_checked",
    "pets_allowed",
    "price_position_label", "vs_cohort_pct", "risk_flags",
]

# 坐标：仅 PostgreSQL 有 geom；SQLite 回退时返回 NULL，前端按「无坐标」处理
COORD_COLS = (["ST_Y(geom) AS lat", "ST_X(geom) AS lng"]
              if is_postgres() else ["NULL AS lat", "NULL AS lng"])

# 允许的词表（防注入，白名单校验）
DISTRICTS = {"锦江区", "高新区", "龙泉驿区", "成华区", "武侯区", "双流区",
             "温江区", "天府新区", "金牛区", "青羊区", "郫都区"}
SORTS = {"price_asc": "rent_cny_per_month ASC",
         "price_desc": "rent_cny_per_month DESC",
         "area_desc": "area_sqm DESC"}

# 宠物政策三态：None=未核实，前端不要当成"不可养"
PETS_STATUS = {True: "可养宠", False: "不养宠", None: "待与房东确认"}


def search_listings(district=None, max_price=None, min_price=None,
                    bedrooms=None, min_area=None, max_area=None,
                    south_facing=None, has_elevator=None, rent_type=None,
                    pets_allowed=None, community=None, sort="price_asc", limit=20,
                    include_non_residential=False):
    """
    精确筛选房源。所有条件取交集，仅返回在架房源。
    主库 PostgreSQL+PostGIS（RAG_DB=sqlite 可回退）
    - max_price/min_price: 含本数（月租，元）
    - bedrooms: 卧室数精确匹配（3 不会返回 4 居）
    - south_facing / has_elevator: 传 True 才筛，None 不筛
    - include_non_residential: 默认 False，排除 tags 含「非居住房屋」的商办公寓
    返回: {"total": 总数, "listings": [...]}
    """
    where, params = [f"is_active = {TRUE}"], []

    # LIKE 模式走参数绑定：SQL 源码里出现裸 % 会被 psycopg 当成占位符解析
    if not include_non_residential:
        tags_txt = "COALESCE(tags::text, '')" if is_postgres() else "COALESCE(tags, '')"
        where.append(f"{tags_txt} NOT LIKE {PH}")
        params.append("%非居住房屋%")

    if district is not None:
        if district not in DISTRICTS:
            return {"total": 0, "listings": [], "error": f"未知行政区: {district}，当前仅覆盖成都"}
        where.append(f"district = {PH}"); params.append(district)
    if community:
        where.append(f"community = {PH}"); params.append(community)
    if max_price is not None:
        where.append(f"rent_cny_per_month <= {PH}"); params.append(int(max_price))
    if min_price is not None:
        where.append(f"rent_cny_per_month >= {PH}"); params.append(int(min_price))
    if bedrooms is not None:
        where.append(f"bedrooms = {PH}"); params.append(int(bedrooms))
    if min_area is not None:
        where.append(f"area_sqm >= {PH}"); params.append(float(min_area))
    if max_area is not None:
        where.append(f"area_sqm <= {PH}"); params.append(float(max_area))
    if south_facing:
        where.append(f"is_south_facing = {TRUE}")
    if has_elevator:
        where.append(f"elevator = {TRUE}")
    if rent_type is not None:
        if rent_type not in ("整租", "合租"):
            return {"total": 0, "listings": [], "error": "rent_type 仅支持 整租/合租"}
        where.append(f"rent_type = {PH}"); params.append(rent_type)
    if pets_allowed is not None:
        where.append(f"pets_allowed = {PH}")
        params.append(bool(pets_allowed) if is_postgres()
                      else (1 if pets_allowed else 0))

    order = SORTS.get(sort, SORTS["price_asc"]) + ", id"  # 同价时按 id 稳定排序
    limit = max(1, min(int(limit), 100))
    cond = " AND ".join(where)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(RESULT_COLS + COORD_COLS)} FROM listings "
                    f"WHERE {cond} ORDER BY {order} LIMIT {PH}", params + [limit])
        rows = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) AS n FROM listings WHERE {cond}", params)
        total = cur.fetchone()["n"]

    listings = []
    for r in rows:
        d = dict(r)
        for k in NUM_FIELDS:
            if k in d and d[k] is not None:
                d[k] = float(d[k])
        d["is_south_facing"] = bool(d["is_south_facing"])
        d["elevator"] = None if d["elevator"] is None else bool(d["elevator"])
        d["pets_allowed"] = None if d["pets_allowed"] is None else bool(d["pets_allowed"])
        d["pets_status"] = PETS_STATUS[d["pets_allowed"]]
        d["ownership_checked"] = bool(d["ownership_checked"])
        if not isinstance(d["risk_flags"], list):
            d["risk_flags"] = json.loads(d["risk_flags"] or "[]")
        listings.append(d)
    return {"total": total, "listings": listings}


if __name__ == "__main__":
    # B 组验收自测
    checks = [
        ("B1 高新区", dict(district="高新区"), 13),
        ("B1 三居", dict(bedrooms=3), 54),
        ("B1 合租", dict(rent_type="合租"), 4),
        ("B1 朝南", dict(south_facing=True), 96),   # 南49+东南36+西南11
        ("B1 有电梯", dict(has_elevator=True), 93),
        ("B1 3000以下", dict(max_price=3000), None),
        ("B3 边界 max_price=2300(中位数)", dict(max_price=2300), None),
        ("B2 组合 3000以下+三居+朝南+电梯",
         dict(max_price=3000, bedrooms=3, south_facing=True, has_elevator=True), None),
        ("B4 空结果", dict(district="郫都区", bedrooms=4, max_price=500), 0),
    ]
    for name, kw, expect in checks:
        r = search_listings(**kw)
        mark = "" if expect is None else ("PASS" if r["total"] == expect else f"FAIL(期望{expect})")
        print(f"{name}: total={r['total']} {mark}")
    # B7 排序
    r = search_listings(district="锦江区", sort="price_desc", limit=1)
    top = r["listings"][0]
    print(f"B7 锦江最贵: {top['community']} {top['rent_cny_per_month']}元 "
          f"(期望 朗基望今缘 12000)")
