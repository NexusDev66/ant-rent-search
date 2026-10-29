-- 蚂蚁搬家租房找房 房源库 schema v1
-- 数据来源: 房源数据/data_for_agent/agent.jsonl (成都租房)

CREATE TABLE IF NOT EXISTS listings (
    id                      TEXT PRIMARY KEY,        -- 房源编号
    url                     TEXT,
    -- 位置
    city                    TEXT,
    district                TEXT NOT NULL,
    community               TEXT,
    -- 价格
    rent_cny_per_month      INTEGER NOT NULL,
    rent_cny_per_sqm_month  REAL,
    months_per_payment      INTEGER,
    deposit_months          INTEGER,
    -- 户型与面积
    area_sqm                REAL NOT NULL,
    bedrooms                INTEGER NOT NULL,
    living_rooms            INTEGER,
    bathrooms               INTEGER,
    layout_label            TEXT,                    -- 一居/两居/三居...
    -- 楼层朝向装修
    floor_position          TEXT,                    -- 低/中/高
    total_floors            INTEGER,
    orientation_primary     TEXT,
    is_south_facing         INTEGER,                 -- 0/1
    decoration_tier         TEXT,
    decoration_level        INTEGER,
    -- 租赁属性
    rent_type               TEXT NOT NULL,           -- 整租/合租
    elevator                INTEGER,                 -- 0/1, NULL=暂无数据
    pets_allowed            INTEGER,                 -- 1=可养宠 0=不可 NULL=暂未收录
    move_in_type            TEXT,                    -- immediate/scheduled/other
    ownership_checked       INTEGER,                 -- 政府权属核验
    verify_code             TEXT,
    -- 分类与评价 (脚本推导)
    price_band_cny          TEXT,
    size_band_sqm           TEXT,
    price_position_label    TEXT,
    vs_cohort_pct           REAL,
    quality_flags           TEXT,                    -- JSON 数组
    risk_flags              TEXT,                    -- JSON 数组
    tags                    TEXT,                    -- JSON 数组
    amenities               TEXT,                    -- JSON 数组
    -- 检索
    headline                TEXT,
    search_text             TEXT,
    -- 媒体
    has_watermark_free      INTEGER,
    photo_total             INTEGER,
    -- 状态与溯源
    is_active               INTEGER NOT NULL DEFAULT 1,  -- 0=已下架
    updated_at              TEXT,                    -- 平台更新时间
    collected_at            TEXT,                    -- 采集时间
    imported_at             TEXT NOT NULL,           -- 本次导入时间
    raw_json                TEXT NOT NULL            -- 完整原始 JSON
);

CREATE INDEX IF NOT EXISTS idx_listings_filter
    ON listings (is_active, district, bedrooms, rent_cny_per_month);
CREATE INDEX IF NOT EXISTS idx_listings_area   ON listings (area_sqm);
CREATE INDEX IF NOT EXISTS idx_listings_flags  ON listings (is_south_facing, elevator);
