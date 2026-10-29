-- 蚂蚁搬家租房找房 · 房源库（PostgreSQL + PostGIS）
-- 与 SQLite 版字段一致，额外增加 geom（经纬度，暂无数据，留空）

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS listings (
    id                      TEXT PRIMARY KEY,
    url                     TEXT,
    city                    TEXT,
    district                TEXT NOT NULL,
    community               TEXT NOT NULL,

    rent_cny_per_month      INTEGER NOT NULL,
    rent_cny_per_sqm_month  NUMERIC(10,2),
    months_per_payment      INTEGER,
    deposit_months          NUMERIC(5,2),

    area_sqm                NUMERIC(8,2) NOT NULL,
    bedrooms                INTEGER NOT NULL,
    living_rooms            INTEGER,
    bathrooms               INTEGER,
    layout_label            TEXT,
    floor_position          TEXT,
    total_floors            INTEGER,
    orientation_primary     TEXT,
    is_south_facing         BOOLEAN,
    decoration_tier         TEXT,
    decoration_level        INTEGER,
    rent_type               TEXT NOT NULL,
    elevator                BOOLEAN,
    move_in_type            TEXT,

    ownership_checked       BOOLEAN,
    verify_code             TEXT,
    pets_allowed            BOOLEAN,

    price_band_cny          TEXT,
    size_band_sqm           TEXT,
    price_position_label    TEXT,
    vs_cohort_pct           NUMERIC(6,2),

    quality_flags           JSONB DEFAULT '[]'::jsonb,
    risk_flags              JSONB DEFAULT '[]'::jsonb,
    tags                    JSONB DEFAULT '[]'::jsonb,
    amenities               JSONB DEFAULT '[]'::jsonb,

    headline                TEXT,
    search_text             TEXT,
    has_watermark_free      BOOLEAN,
    photo_total             INTEGER,

    -- 空间字段：暂未回填坐标，拿到地理编码结果后批量 UPDATE
    geom                    geometry(Point, 4326),

    is_active               BOOLEAN DEFAULT TRUE,
    updated_at              TIMESTAMP,
    collected_at            TIMESTAMP,
    imported_at             TIMESTAMP DEFAULT NOW(),
    raw_json                JSONB
);

CREATE INDEX IF NOT EXISTS idx_listings_filter
    ON listings (is_active, district, rent_type, rent_cny_per_month, bedrooms);
CREATE INDEX IF NOT EXISTS idx_listings_community ON listings (community);
CREATE INDEX IF NOT EXISTS idx_listings_geom ON listings USING GIST (geom);
