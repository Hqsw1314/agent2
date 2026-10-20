# -*- coding: utf-8 -*-
"""通用工具函数。"""
import json
import re


def extract_json(text):
    """从 LLM 输出文本中提取 JSON 对象。

    提取顺序:
      1) ```json ... ``` 代码块
      2) 文本中第一个花括号配平的 {...} 块
    都失败时返回 None，由调用方决定如何报错。
    """
    if not text:
        return None

    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        candidates.append(m.group(1))

    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break

    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            continue
    return None


def to_json_str(obj, limit=None):
    """安全地把对象转成 JSON 字符串（用于拼 Prompt / 日志）。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        s = str(obj)
    if limit and len(s) > limit:
        s = s[:limit] + "...(截断)"
    return s
