# -*- coding: utf-8 -*-
"""
规则解析（确定性兜底）：不调 LLM，把一句话解析成筛选条件
用途：周五演示现场 LLM 不稳定时，切 mode=rules 保证页面联动可用
"""
import re

DISTRICTS = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区", "高新区",
             "天府新区", "龙泉驿区", "双流区", "温江区", "郫都区"]
DISTRICT_ALIAS = {"锦江": "锦江区", "青羊": "青羊区", "金牛": "金牛区", "武侯": "武侯区",
                  "成华": "成华区", "高新": "高新区", "天府新区": "天府新区",
                  "龙泉驿": "龙泉驿区", "双流": "双流区", "温江": "温江区", "郫都": "郫都区"}

CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

ORDINAL_RE = re.compile(r"第\s*([一二两三四五六七八九十\d]+)\s*[套个间]")


def _num(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    return CN_NUM.get(s)


def parse_ordinal(text):
    """'打开第二套' -> 2（1-based），无则 None"""
    m = ORDINAL_RE.search(text)
    return _num(m.group(1)) if m else None


def parse_filters(text):
    """解析筛选条件，返回 search_listings 可用的 kwargs"""
    f = {}
    m = re.search(r"(\d{3,6})\s*(?:元|块)?\s*(?:以下|以内|之内|内|不超过|低于)", text)
    if m:
        f["max_price"] = int(m.group(1))
    if "整租" in text:
        f["rent_type"] = "整租"
    elif "合租" in text:
        f["rent_type"] = "合租"
    if re.search(r"养宠|可养宠|能养|猫|狗|宠物", text):
        f["pets_allowed"] = True
    for d in DISTRICTS:
        if d in text:
            f["district"] = d
            break
    else:
        for k, v in DISTRICT_ALIAS.items():
            if k in text:
                f["district"] = v
                break
    m = re.search(r"([一二两三四五六])\s*居", text)
    if m:
        f["bedrooms"] = CN_NUM[m.group(1)]
    if "朝南" in text:
        f["south_facing"] = True
    if "电梯" in text:
        f["has_elevator"] = True
    return f


LABEL = {"district": "区域", "max_price": "预算上限", "min_price": "预算下限",
         "rent_type": "租赁方式", "pets_allowed": "可养宠", "bedrooms": "卧室数",
         "south_facing": "朝南", "has_elevator": "有电梯"}


def describe(filters):
    if not filters:
        return "全部在架房源"
    parts = []
    for k, v in filters.items():
        name = LABEL.get(k, k)
        if k in ("max_price", "min_price"):
            parts.append(f"{name}{v}元")
        elif k == "bedrooms":
            parts.append(f"{v}居")
        elif isinstance(v, bool):
            parts.append(name)
        else:
            parts.append(f"{name}：{v}")
    return "、".join(parts)
