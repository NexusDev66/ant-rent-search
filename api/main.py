# -*- coding: utf-8 -*-
"""
蚂蚁找房助手 API — 所有端（app/小程序/H5）的唯一对接入口
启动: python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""
import json
import re
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "rag"))

from chat import ask, ask_stream, new_history
from db import BACKEND, PH, TRUE, get_conn, is_postgres
from retrieve import search_listings
from rules import parse_filters, parse_ordinal, describe, LABEL

app = FastAPI(title="蚂蚁找房助手 API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 会话（内存版，单机够用；重启即清空） ----------
_sessions: dict[str, list] = {}
_last: dict[str, list] = {}          # session_id -> 上一次渲染的房源列表
_sessions_lock = threading.Lock()
MAX_SESSIONS = 500


def get_history(session_id: str):
    with _sessions_lock:
        if session_id not in _sessions:
            if len(_sessions) >= MAX_SESSIONS:          # 简单 LRU：丢最早的一个
                _sessions.pop(next(iter(_sessions)))
            _sessions[session_id] = new_history()
        return _sessions[session_id]


# ---------- 请求模型 ----------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    session_id: str = Field(default="default", max_length=64)
    stream: bool = False
    mode: str = Field(default="llm", description="llm=大模型；rules=确定性规则（演示兜底）")


OPEN_INTENT = re.compile(r"打开|点开|我要看|看看|看一下|查看")


def build_actions(user_text, trace, fallback=None):
    """把工具轨迹+用户话语转成前端可执行的页面动作（对应《方案》2.2 页面预设）
    fallback: 本轮没有搜索时，沿用会话里上一次的房源列表（支撑「打开第二套」）"""
    # 纯导航意图（"打开第二套"）不认模型这轮任何搜索，避免序号对不上
    if parse_ordinal(user_text) and OPEN_INTENT.search(user_text):
        trace = []
    actions, listings = [], []
    if trace:
        args, result = trace[-1]["args"], trace[-1]["result"]
        filt = {k: v for k, v in args.items() if k not in ("limit", "sort")}
        if filt:
            actions.append({"type": "update_filter", "payload": filt})
        listings = result.get("listings") or []
        actions.append({"type": "render_listings",
                        "payload": {"total": result.get("total", 0),
                                    "ids": [x["id"] for x in listings],
                                    "listings": listings}})
        points = [{"id": x["id"], "lng": x.get("lng"), "lat": x.get("lat")}
                  for x in listings if x.get("lng") is not None]
        if points:                                  # 有坐标才下发地图动作
            actions.append({"type": "locate_map", "payload": {"points": points}})
    else:
        listings = fallback or []
    n = parse_ordinal(user_text)
    if n:
        idx = n - 1
        if 0 <= idx < len(listings):
            actions.append({"type": "open_detail",
                            "payload": {"id": listings[idx]["id"], "index": n}})
        else:
            actions.append({"type": "open_detail",
                            "payload": {"id": None, "index": n,
                                        "error": f"当前列表只有 {len(listings)} 套，没有第 {n} 套"}})
    state = ({k: v for k, v in trace[-1]["args"].items() if k not in ("limit", "sort")}
             if trace else {})
    return actions, state, (trace[-1]["result"] if trace else {})


def _rules_answer(filters, result):
    n = result.get("total", 0)
    if n == 0:
        if filters.get("pets_allowed"):
            return ("按「" + describe(filters) + "」没有找到已核实的房源："
                    "宠物政策大多还在等房东确认，建议先放宽宠物条件看房，再和房东谈。")
        return f"按「{describe(filters)}」没有找到符合条件的房源，可以放宽预算或换个区域试试。"
    return f"按「{describe(filters)}」共找到 {n} 套房源，已为你更新列表。"


# ---------- 接口 ----------
@app.get("/health")
def health():
    with get_conn() as conn:
        n = conn.execute(f"SELECT COUNT(*) AS n FROM listings WHERE is_active = {TRUE}"
                         ).fetchone()["n"]
    return {"status": "ok", "active_listings": n, "db": BACKEND,
            "sessions": len(_sessions), "time": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    history = get_history(req.session_id)

    # ---- 规则模式：不调 LLM，确定性解析（演示兜底）----
    if req.mode == "rules":
        filters = parse_filters(req.message)
        if filters:
            result = search_listings(limit=10, **filters)
            trace = [{"args": filters, "result": result}]
            answer = _rules_answer(filters, result)
        else:
            trace = []
            answer = "你可以这样说：2500 以下、整租、能养猫；或者告诉我区域和户型。"
        actions, f_state, r_last = build_actions(req.message, trace,
                                                 fallback=_last.get(req.session_id))
        if r_last.get("listings"):
            _last[req.session_id] = r_last["listings"]
        return {"session_id": req.session_id, "answer": answer, "mode": "rules",
                "filters": f_state, "actions": actions,
                "listings": r_last.get("listings", [])}

    if req.stream:
        trace = []

        def gen():
            try:
                for piece in ask_stream(history, req.message, trace):
                    yield f"data: {json.dumps({'delta': piece}, ensure_ascii=False)}\n\n"
                actions, f_state, r_last = build_actions(req.message, trace,
                                                         fallback=_last.get(req.session_id))
                if r_last.get("listings"):
                    _last[req.session_id] = r_last["listings"]
                yield f"data: {json.dumps({'done': True, 'filters': f_state, 'actions': actions}, ensure_ascii=False)}\n\n"
            except Exception as e:                      # noqa: BLE001
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    trace = []
    try:
        answer = ask(history, req.message, trace)
    except Exception as e:                              # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"模型服务异常: {e}")
    actions, f_state, r_last = build_actions(req.message, trace,
                                             fallback=_last.get(req.session_id))
    if r_last.get("listings"):
        _last[req.session_id] = r_last["listings"]
    return {"session_id": req.session_id, "answer": answer, "mode": "llm",
            "filters": f_state, "actions": actions,
            "listings": r_last.get("listings", [])}


@app.post("/tool/search")
def tool_search(req: dict):
    """给 Dify Chatflow 的 HTTP 节点用：一次请求拿回"结论 + 卡片 + 上下文文本"

    body 传筛选条件（同 /listings），例：
      {"district": "高新区", "max_price": 2500, "rent_type": "整租", "pets_allowed": true}
    """
    allowed = {"district", "community", "min_price", "max_price", "bedrooms",
               "min_area", "max_area", "south_facing", "has_elevator", "rent_type",
               "pets_allowed", "include_non_residential", "sort"}
    filters = {k: v for k, v in (req or {}).items() if k in allowed and v is not None}
    limit = int((req or {}).get("limit", 10))
    r = search_listings(limit=limit, **filters)
    if r.get("error"):
        return {"ok": False, "error": r["error"], "total": 0, "items": [], "context": ""}

    items = []
    lines = []
    for i, x in enumerate(r["listings"], 1):
        items.append({"index": i, "id": x["id"], "community": x["community"],
                      "district": x["district"], "layout": x["layout_label"],
                      "area_sqm": x["area_sqm"], "rent": x["rent_cny_per_month"],
                      "rent_type": x["rent_type"], "pets_status": x["pets_status"],
                      "floor": f"{x.get('floor_position') or ''}/{x.get('total_floors') or ''}",
                      "orientation": x.get("orientation_primary"),
                      "elevator": x.get("elevator"), "headline": x["headline"],
                      "lng": x.get("lng"), "lat": x.get("lat")})
        lines.append(f"{i}. {x['rent_cny_per_month']}元/月 | {x['community']} | "
                     f"{x['district']} | {x['layout_label']} {x['area_sqm']}㎡ | "
                     f"{x['rent_type']} | 宠物:{x['pets_status']}")

    return {"ok": True, "total": r["total"], "filters": filters, "items": items,
            "context": "\n".join(lines) if lines else "无匹配房源"}


@app.get("/listings")
def listings(district: str | None = None, min_price: int | None = None,
             max_price: int | None = None, bedrooms: int | None = None,
             min_area: float | None = None, max_area: float | None = None,
             south_facing: bool | None = None, has_elevator: bool | None = None,
             rent_type: str | None = None, community: str | None = None,
             pets_allowed: bool | None = None,
             include_non_residential: bool = False,
             sort: str = "price_asc",
             limit: int = Query(default=20, ge=1, le=100)):
    r = search_listings(district=district, min_price=min_price, max_price=max_price,
                        bedrooms=bedrooms, min_area=min_area, max_area=max_area,
                        south_facing=south_facing, has_elevator=has_elevator,
                        rent_type=rent_type, community=community,
                        pets_allowed=pets_allowed,
                        include_non_residential=include_non_residential,
                        sort=sort, limit=limit)
    if r.get("error"):
        raise HTTPException(status_code=400, detail=r["error"])
    return r


@app.get("/listings/{listing_id}")
def listing_detail(listing_id: str):
    """详情页数据（对应「打开第二套」后的页面）"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM listings WHERE id = {PH} AND is_active = {TRUE}",
                    [listing_id])
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="房源不存在或已下架")
    d = dict(row)
    for k in ("pets_allowed", "elevator", "is_south_facing", "ownership_checked",
              "has_watermark_free"):
        d[k] = None if d.get(k) is None else bool(d[k])
    d["pets_status"] = {True: "可养宠", False: "不养宠",
                        None: "待与房东确认"}[d["pets_allowed"]]
    for k in ("quality_flags", "risk_flags", "tags", "amenities"):
        v = d.get(k)
        d[k] = v if isinstance(v, list) else json.loads(v or "[]")
    d.pop("raw_json", None)
    d.pop("url", None)
    d.pop("search_text", None)
    d.pop("geom", None)
    return d


@app.post("/session/reset")
def reset_session(session_id: str = "default"):
    with _sessions_lock:
        _sessions.pop(session_id, None)
    return {"session_id": session_id, "reset": True}


@app.get("/")
def root():
    return {"service": "蚂蚁搬家租房找房 · 蚂蚁找房助手",
            "endpoints": ["POST /chat", "GET /listings", "GET /listings/{id}",
                          "GET /health", "POST /session/reset", "GET /docs"]}
