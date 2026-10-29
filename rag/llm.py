# -*- coding: utf-8 -*-
"""
LLM 客户端 + 工具定义 + 系统提示词（D 组纪律）
凭据从环境变量读取: RAG_API_KEY / RAG_API_BASE / RAG_MODEL
"""
import json
import os
import requests


def _env(name, default=""):
    """环境变量优先；Windows 下回退读用户注册表（兼容旧终端会话）"""
    v = os.environ.get(name)
    if v:
        return v
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except Exception:
        return default


API_BASE = _env("RAG_API_BASE", "https://api.agnes-ai.cn/v1")
API_KEY = _env("RAG_API_KEY")
MODEL = _env("RAG_MODEL", "agnes-2.5-flash")

if not API_KEY:
    raise RuntimeError("缺少环境变量 RAG_API_KEY，请先设置后再运行")

SYSTEM_PROMPT = """你是「蚂蚁找房助手」，隶属于「蚂蚁搬家租房找房」，只覆盖成都地区的租房房源。

## 身份与措辞（硬性）
- 服务方名称：蚂蚁搬家租房找房。自我介绍时说"我是蚂蚁找房助手"
- 房源全部来自我们自有的房源库。**严禁提及任何第三方找房平台、房源平台、中介品牌的名字**，也严禁使用"平台""数据来源""其他平台"这类字眼
- 对"房源信息哪来的"这类提问，只答"来自蚂蚁搬家租房找房自建的房源库"，不加任何对比或补充说明
- 凡是要表达"我们没有这项信息"，说"我们暂未收录"或"暂无该信息"，不要说"平台未提供"

## 工作方式
- 用户描述找房需求时，调用 search_listings 工具筛选，不要凭记忆编造房源
- **只要用户给了区域、户型、预算中的任意一项，就直接搜索并给出结果，不要反问**。条件不全没关系，先给结果再让用户缩小范围
- 只有用户一句话里完全没有任何条件（如"我想找房"）时才追问
- 用户说"打开第二套""看第三个"这类翻页/查看意图时，**不要调用 search_listings**，只回一句确认（如"好的，为你打开第 2 套"），由前端负责跳转；不要重新搜索、不要重新列房源
- 用户说"这套""这个小区"但当前对话里没有出现过具体房源时，**不要猜小区名**，直接问"您指的是哪一套？可以说小区名或刚才列表里的第几套"
- 一次回答最多推荐 3-5 套，用 headline 形式列出关键信息（小区、户型、面积、租金、亮点）
- 报告数量时必须使用工具返回的 total 字段（如"共找到 N 套"），展示条数不等于总数

## 多轮条件继承（重要，硬性规则）
**每次搜索只使用当前这一轮问题里出现的条件，问题没提的条件一律不带。**
- 例：上轮聊"高新区两居"，这轮问"3000以下三居朝南有电梯的有吗"→ 只能传 bedrooms=3, max_price=3000, south_facing=true, has_elevator=true，**禁止**带 district=高新区
- 例：上轮聊整租，这轮问"有合租的吗"→ 只能传 rent_type=合租，其他什么都不带
- 类别转换问题（"有合租的吗""有四居吗"）即使上一轮在聊某个具体小区，也按全市范围搜索；回答末尾可附一句"如果您是想问 XX 小区的合租，该小区目前没有"
- 例：这轮问"回到高新区两居，太贵了有便宜点的吗"→ 问题里提到了高新区+两居+价格比较，传 district=高新区, bedrooms=2，并把 max_price 收紧到上一轮最低价之下
- "这套""这个小区"→ 用 community 限定到该小区
- 用户只说了比较词（如"太贵了""便宜点的"）而没提区域/户型 → **继承上一轮的全部筛选条件**，只收紧价格（max_price 设为上轮最低价以下）；上一轮没有搜索条件时才追问

## 纪律（必须遵守）
1. 商圈、房源描述两项我们暂未收录。用户问到时如实说"我们暂未收录该信息"，严禁编造
2. 评价某套房"贵不贵"时，必须引用 price_position_label 和 vs_cohort_pct（相对同区域中位价的偏离），不能只看月租绝对值下结论
3. 合租房源的口径（必须原样遵守，不得反转）：合租的**面积和租金都是整套口径，不是单间价格**。推荐合租房时必须完整说明这一点；严禁说"租金为单间价格""单间出租"之类的话
4. 房源的 risk_flags 若包含水印相关提示，告知用户"图片带水印，仅供内部参考"，不要主动外发图片
5. 宠物政策：用户说"能养猫/养狗/带宠物"时，**必须传 pets_allowed=true**。pets_status 为「可养宠/不养宠」时按该结论说；为「待与房东确认」时说"该项我们暂未核实，需与房东确认"，**严禁说成"不能养"**。不要声称我们已与房东核实过
6. 房源信息未经实勘，仅供参考，不得作为合同依据；用户表现出签约意向时提示实地核验
7. 用户问成都以外城市、买房、或其他与租房无关的问题，明确告知当前仅提供成都租房服务
8. 房源的权属核验状态可作为信任信号向用户说明，但不要说"政府平台"以外的第三方名称
"""

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_listings",
        "description": "按条件精确筛选成都在架租房房源。所有条件取交集。商圈不可查（暂未收录）。",
        "parameters": {
            "type": "object",
            "properties": {
                "district": {"type": "string", "description": "行政区",
                             "enum": ["锦江区", "青羊区", "金牛区", "武侯区", "成华区",
                                      "高新区", "天府新区", "龙泉驿区", "双流区", "温江区", "郫都区"]},
                "community": {"type": "string",
                              "description": "小区名，精确匹配；用户明确说出小区名时才传"},
                "min_price": {"type": "integer", "description": "月租下限（元），含本数"},
                "max_price": {"type": "integer", "description": "月租上限（元），含本数"},
                "bedrooms": {"type": "integer", "description": "卧室数：1=一居 2=两居 3=三居 4=四居"},
                "min_area": {"type": "number", "description": "面积下限（㎡）"},
                "max_area": {"type": "number", "description": "面积上限（㎡）"},
                "south_facing": {"type": "boolean", "description": "true=只要朝南（含东南/西南）"},
                "has_elevator": {"type": "boolean", "description": "true=只要有电梯"},
                "rent_type": {"type": "string", "enum": ["整租", "合租"]},
                "pets_allowed": {"type": "boolean",
                                 "description": "true=只找可养宠的（用户说能养猫/养狗/带宠物时必传）"},
                "sort": {"type": "string", "enum": ["price_asc", "price_desc", "area_desc"],
                         "description": "默认 price_asc（便宜的在前）"},
                "limit": {"type": "integer", "description": "返回条数，默认 10"}
            }
        }
    }
}]


def chat(messages, tools=None, temperature=0.3, timeout=60, max_retries=4):
    """调用 chat/completions，返回 message 对象；429 限流时指数退避重试"""
    import time
    payload = {"model": MODEL, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
    for attempt in range(max_retries + 1):
        r = requests.post(f"{API_BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {API_KEY}"},
                          json=payload, timeout=timeout)
        if r.status_code == 429 and attempt < max_retries:
            time.sleep(min(2 ** attempt * 5, 30))
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


def chat_stream(messages, tools=None, temperature=0.3, timeout=120, max_retries=2):
    """流式调用，逐块 yield delta dict（含 content / tool_calls）"""
    import time
    payload = {"model": MODEL, "messages": messages, "temperature": temperature,
               "stream": True}
    if tools:
        payload["tools"] = tools
    for attempt in range(max_retries + 1):
        r = requests.post(f"{API_BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {API_KEY}"},
                          json=payload, stream=True, timeout=timeout)
        if r.status_code == 429 and attempt < max_retries:
            r.close()
            time.sleep(min(2 ** attempt * 5, 30))
            continue
        r.raise_for_status()
        for raw in r.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if choices:
                yield choices[0].get("delta") or {}
        r.close()
        return
