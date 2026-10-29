# -*- coding: utf-8 -*-
"""
对话层：多轮对话 + function calling 路由到 search_listings
用法:
  python chat.py            进入交互式对话
  python chat.py --test     跑验收测试题集（非交互）
"""
import json
import os
import sys
from pathlib import Path

DEBUG = bool(os.environ.get("RAG_DEBUG"))

sys.path.insert(0, str(Path(__file__).resolve().parent / "rag"))
from llm import chat, chat_stream, SYSTEM_PROMPT, TOOLS
from retrieve import search_listings

MAX_TOOL_ROUNDS = 3


# 回指词：用户消息含这些词时，保留 community/district 参数合法
REFER_BACK = ("这套", "这个小区", "该小区", "这个房", "这套房", "该房源")
# 比较/追问词：在上一轮结果上继续筛，允许继承上轮的区域/小区条件
CONTINUE_WORDS = ("太贵", "贵了", "便宜", "低点", "再", "还有", "其他", "换个", "换一")


def _sanitize_args(args, user_text):
    """硬性规则兜底：当前问题未提及的区域/小区条件一律剥掉（对应 D5 验收）
    返回 (净化后参数, 被剥掉的条件说明列表)"""
    dropped = []
    if any(w in user_text for w in REFER_BACK + CONTINUE_WORDS):
        return args, dropped
    for key in ("community", "district"):
        v = args.get(key)
        if v and v not in user_text:
            if DEBUG:
                print(f"[guard] 剥离未提及的条件 {key}={v}", file=sys.stderr)
            args.pop(key)
            dropped.append(f"{key}={v}")
    return args, dropped


def _exec_tool(name, args_json, user_text="", trace=None):
    if name != "search_listings":
        return {"error": f"未知工具: {name}"}
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        return {"error": "工具参数解析失败"}
    clean, dropped = _sanitize_args(args, user_text)
    result = search_listings(**clean)
    if trace is not None:
        trace.append({"args": clean, "result": result})
    if dropped:
        result["note"] = (f"注意：用户本轮问题未提及 {'、'.join(dropped)}，"
                          f"已按全市范围搜索，以下结果不限于该小区/区域。"
                          f"回答时请按全市结果叙述，并可补充说明该小区/区域本身是否有匹配。")
    return result


def ask(history, user_text, trace=None):
    """单轮提问（含工具调用循环），返回助手最终文本；trace 收集工具调用轨迹"""
    history.append({"role": "user", "content": user_text})
    for _ in range(MAX_TOOL_ROUNDS):
        msg = chat([{"role": "system", "content": SYSTEM_PROMPT}] + history, tools=TOOLS)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            history.append({"role": "assistant", "content": msg.get("content") or ""})
            return msg.get("content") or ""
        history.append(msg)
        for tc in tool_calls:
            result = _exec_tool(tc["function"]["name"], tc["function"].get("arguments"),
                                user_text, trace)
            if DEBUG:
                print(f"[tool] {tc['function']['name']}({tc['function'].get('arguments')})"
                      f" -> total={result.get('total')}", file=sys.stderr)
            history.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
    # 兜底：轮次用尽仍想调工具时，禁用工具强制出答案
    msg = chat([{"role": "system", "content": SYSTEM_PROMPT}] + history)
    history.append({"role": "assistant", "content": msg.get("content") or ""})
    return msg.get("content") or "（暂时没找到答案，请换个问法）"


def ask_stream(history, user_text, trace=None):
    """流式版 ask：逐段 yield 文本；工具轮次静默执行"""
    history.append({"role": "user", "content": user_text})
    for _ in range(MAX_TOOL_ROUNDS):
        parts, calls = [], {}
        for delta in chat_stream([{"role": "system", "content": SYSTEM_PROMPT}] + history,
                                 tools=TOOLS):
            if delta.get("content"):
                parts.append(delta["content"])
                yield delta["content"]
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0),
                                        {"id": "", "type": "function",
                                         "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""

        if not calls:
            history.append({"role": "assistant", "content": "".join(parts)})
            return

        tool_calls = [calls[i] for i in sorted(calls)]
        history.append({"role": "assistant", "content": "".join(parts) or None,
                        "tool_calls": tool_calls})
        for tc in tool_calls:
            result = _exec_tool(tc["function"]["name"], tc["function"]["arguments"],
                                user_text, trace)
            if DEBUG:
                print(f"[tool] {tc['function']['name']}({tc['function']['arguments']})"
                      f" -> total={result.get('total')}", file=sys.stderr)
            history.append({"role": "tool", "tool_call_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False)})
    # 兜底：轮次用尽仍想调工具时，禁用工具强制出答案
    for delta in chat_stream([{"role": "system", "content": SYSTEM_PROMPT}] + history):
        if delta.get("content"):
            yield delta["content"]


def new_history():
    return []


TEST_SCRIPT = [
    "高新区有哪些两居？",
    "3000以下三居朝南有电梯的有吗？",
    "锦江区最贵的房子是哪个？",
    "檀悦雅筑那套9800的贵不贵？",
    "有合租的吗？",
    "这个小区在哪个商圈？",
    "北京有房源吗？",
    # 多轮追问（接第1条高新区两居的语境）
    "回到高新区两居，太贵了，有便宜点的吗？",
]


def run_test():
    history = new_history()
    for i, q in enumerate(TEST_SCRIPT, 1):
        print(f"\n{'='*60}\n[题{i}] {q}\n{'-'*60}")
        print(ask(history, q))


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_test()
    else:
        print("蚂蚁找房助手（成都）| 输入 quit 退出")
        history = new_history()
        while True:
            try:
                q = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("quit", "exit", ""):
                break
            print("助手:", ask(history, q))
