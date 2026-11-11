"""复合事件：为原子事件不足的稿件补一个概括层事件。

设计：
- 一篇稿件有 ≥2 个原子事件时，用它们概括出 1-3 个复合事件，通过「包含」边挂接。
- 原子事件为 0 或 1 时，用标题与导语直接生成一个复合事件，不挂原子事件。
- 复合事件带 event_type 与 setting，与原子事件同构，便于同一套查询。

    python3 -m extract.composite --src data/extract/kg_full_huanqiu_mil_2026-01.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from extract.kg_extract import (
    EVENT_TYPES,
    SETTINGS,
    guess_event_type,
    guess_kind,
    guess_setting,
)
from extract.llm import chat_json
from extract.prompt import parse_llm_json

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

SYSTEM = """你把同一篇稿件里的多个事件概括成一到三个复合事件。
复合事件是对整件事的一句话概括，能回答「这篇报道讲的是什么」。
只输出 JSON，不要 Markdown 围栏。"""

USER = """下面是一篇新闻的标题、导语和已抽出的原子事件。请概括出 1 到 3 个复合事件。

要求：
1. 复合事件要覆盖多个原子事件，不是简单重述其中一个。
2. 每个复合事件写清主语和主要动作，长度 10 到 40 字。
3. 给出每个复合事件包含哪些原子事件编号。
4. 如果原子事件之间没有共同主题，只输出一个最能代表全文的。
5. 每个复合事件给 event_type 和 setting 两个标签。
   event_type 从这些里选：{event_types}
   setting 从这些里选：{settings}

标题：{title}
导语：{lede}
原子事件：
{events}

JSON：
{{
  "composites": [
    {{"text": "复合事件表述", "event_type": "枚举之一", "setting": "枚举之一", "covers": [0, 2]}}
  ]
}}"""


def _lede(body: str, limit: int = 220) -> str:
    t = " ".join((body or "").split())
    return t[:limit]


def compose_events(row: dict) -> dict:
    """对单篇生成复合事件。返回 {'composites': [...], 'relations': [...]} """
    events = row.get("events") or []
    title = row.get("title") or ""
    lede = _lede(row.get("body") or "") or title

    # 原子事件为空或只有一个：用标题生成兜底复合事件
    if len(events) < 2:
        text = title.strip()
        if not text or len(text) < 6:
            return {"composites": [], "relations": []}
        comp = {
            "text": text[:40],
            "event_type": guess_event_type(text),
            "setting": guess_setting(text),
            "kind": guess_kind(text),
            "covers": [0] if events else [],
            "fallback": True,
        }
        return {"composites": [comp], "relations": []}

    listing = "\n".join(f"[{i}] {e['text']}" for i, e in enumerate(events[:24]))
    user = USER.format(
        event_types="、".join(EVENT_TYPES),
        settings="、".join(SETTINGS),
        title=title,
        lede=lede,
        events=listing,
    )
    try:
        parsed = parse_llm_json(chat_json(SYSTEM, user))
    except Exception:
        # 调用失败时退回标题兜底
        comp = {
            "text": title[:40],
            "event_type": guess_event_type(title),
            "setting": guess_setting(title),
            "kind": guess_kind(title),
            "covers": [],
            "fallback": True,
        }
        return {"composites": [comp], "relations": []}

    out = []
    rels = []
    for item in parsed.get("composites") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if len(text) < 8:
            continue
        etype = str(item.get("event_type") or "").strip()
        setting = str(item.get("setting") or "").strip()
        covers = item.get("covers") if isinstance(item.get("covers"), list) else []
        idxs = []
        for c in covers:
            try:
                k = int(c)
            except (TypeError, ValueError):
                continue
            if 0 <= k < len(events):
                idxs.append(k)
        out.append({
            "text": text[:60],
            "event_type": etype if etype in EVENT_TYPES else guess_event_type(text),
            "setting": setting if setting in SETTINGS else guess_setting(text),
            "kind": guess_kind(text),
            "covers": idxs,
        })
        for k in idxs:
            rels.append({
                "name": "包含",
                "head": text[:60],
                "tail": events[k]["text"],
                "evidence": "",
            })
    return {"composites": out, "relations": rels}


def run(src: Path, out: Optional[Path] = None, limit: int = 0) -> dict:
    if out is None:
        out = src.with_name(src.stem + "_comp.jsonl")
    rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    if limit:
        rows = rows[:limit]
    stats = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            if row.get("errors"):
                continue
            n = len(row.get("events") or [])
            if n >= 2:
                stats["docs_with_atoms"] += 1
            else:
                stats["docs_fallback"] += 1
            res = compose_events(row)
            comps = res["composites"]
            row["composites"] = comps
            # 复合事件单独存，不混入 events；包含边追加到 relations
            if res["relations"]:
                row["relations"] = list(row.get("relations") or []) + res["relations"]
            stats["composites"] += len(comps)
            if any(c.get("fallback") for c in comps):
                stats["fallback_used"] += 1
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"docs": len(rows), **dict(stats), "out": str(out)}


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="复合事件概括")
    p.add_argument("--src", default=str(DATA / "extract" / "kg_full_huanqiu_mil_2026-01.jsonl"))
    p.add_argument("--out", default="")
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args(argv)
    print(json.dumps(run(Path(a.src), Path(a.out) if a.out else None, a.limit),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
