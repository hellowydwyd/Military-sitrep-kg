"""字符串归一：只去引号和空白，不做语义合并。"""

from __future__ import annotations

import re

_QUOTES = "\"'“”‘’「」『』"


def normalize(text: str) -> str:
    t = (text or "").strip()
    t = t.replace(" ", "").replace("\u3000", "")
    t = t.translate(str.maketrans("", "", _QUOTES))
    t = re.sub(r"[（(].*?[）)]", "", t)
    return t
