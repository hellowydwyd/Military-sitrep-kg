"""从环球网 jsonl 建可复现语料清单。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ingest.envelope import MIN_BODY_CHARS, SOURCE_NAMES
from theme.config import (
    DATA,
    GOLD_FROM,
    GOLD_TO,
    RAW_DIR,
    REPORT_DIR,
    SOURCE,
    SOURCES,
    TEST_FROM,
    TEST_TO,
    THEME_DIR,
    TUNE_FROM,
    TUNE_TO,
    in_gold,
    split_of,
)


def _body_chars(body: str) -> int:
    return len(re.sub(r"\s+", "", body or ""))


def iter_raw(source: str | None = None) -> list[dict]:
    rows: list[dict] = []
    srcs = (source,) if source else SOURCES
    for src in srcs:
        src_dir = RAW_DIR / src
        if not src_dir.is_dir():
            continue
        for path in sorted(src_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def _source_name(raw: dict, source: str) -> str:
    extra = raw.get("extra") or {}
    name = (raw.get("source_name") or extra.get("outlet") or "").strip()
    if name:
        return name
    return SOURCE_NAMES.get(source) or source or "未知"


def catalog_docs(source: str | None = None) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()
    for raw in iter_raw(source):
        doc_id = (raw.get("doc_id") or "").strip()
        title = (raw.get("title") or "").strip()
        body = raw.get("body") or ""
        pub = raw.get("publish_time") or ""
        day = pub[:10]
        src = raw.get("source") or source or ""
        lang = (raw.get("lang") or "").strip()
        if not lang and src == "huanqiu_mil":
            lang = "zh"
        reasons: list[str] = []
        if not doc_id:
            reasons.append("no_doc_id")
        if not title:
            reasons.append("no_title")
        if len(day) != 10:
            reasons.append("no_publish_day")
        if _body_chars(body) < MIN_BODY_CHARS:
            reasons.append("body_too_short")
        if doc_id and doc_id in seen:
            reasons.append("dup_doc_id")
        rec = {
            "doc_id": doc_id,
            "title": title,
            "publish_time": pub,
            "day": day,
            "body_len": len(body),
            "body_chars": _body_chars(body),
            "url": raw.get("url") or "",
            "source": src,
            "source_name": _source_name(raw, src),
            "lang": lang,
            "file_day": None,
        }
        if reasons:
            rec["drop"] = reasons
            dropped.append(rec)
            continue
        seen.add(doc_id)
        rec["split"] = split_of(day)
        rec["gold"] = in_gold(day)
        rec["title_preview"] = title
        kept.append(rec)
    kept.sort(key=lambda r: (r["day"], r["doc_id"]))
    return kept, dropped


def write_catalog(kept: list[dict], dropped: list[dict]) -> dict:
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    cat_path = THEME_DIR / "catalog.jsonl"
    drop_path = THEME_DIR / "catalog_dropped.jsonl"
    gold_path = THEME_DIR / "gold_window.jsonl"
    cfg_path = THEME_DIR / "splits.json"
    with cat_path.open("w", encoding="utf-8") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with drop_path.open("w", encoding="utf-8") as f:
        for row in dropped:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    gold_rows = [
        {
            "doc_id": r["doc_id"],
            "day": r["day"],
            "title": r["title"],
            "url": r["url"],
        }
        for r in kept
        if r.get("gold")
    ]
    with gold_path.open("w", encoding="utf-8") as f:
        for row in gold_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    splits = Counter(r["split"] for r in kept)
    drop_reasons = Counter()
    for r in dropped:
        for x in r.get("drop") or []:
            drop_reasons[x] += 1
    cfg = {
        "tune": [TUNE_FROM, TUNE_TO],
        "test": [TEST_FROM, TEST_TO],
        "gold": [GOLD_FROM, GOLD_TO],
        "source": SOURCE,
        "sources": list(SOURCES),
        "min_body_chars": MIN_BODY_CHARS,
        "n_kept": len(kept),
        "n_dropped": len(dropped),
        "splits": dict(splits),
        "n_gold": len(gold_rows),
    }
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    days = [r["day"] for r in kept]
    md = [
        "# 主题演化语料清单",
        "",
        f"- 源：`{'`、`'.join(SOURCES)}`",
        f"- 保留 **{len(kept)}** 篇；剔除 **{len(dropped)}** 篇 {dict(drop_reasons) or '{}'}",
        f"- 见报日：`{min(days) if days else ''}` ～ `{max(days) if days else ''}`",
        f"- 切分（冻结）：调参 `{TUNE_FROM}`–`{TUNE_TO}` = **{splits.get('tune', 0)}**；"
        f"测试 `{TEST_FROM}`–`{TEST_TO}` = **{splits.get('test', 0)}**；"
        f"金标窗 `{GOLD_FROM}`–`{GOLD_TO}` = **{len(gold_rows)}**（测试子集，仅标题）",
        f"- 清单：`{cat_path.relative_to(DATA.parent)}`",
        "",
        "## 切分计数",
        "",
        "| 段 | 篇数 |",
        "|----|------|",
        f"| tune | {splits.get('tune', 0)} |",
        f"| test | {splits.get('test', 0)} |",
        f"| gold ⊂ test | {len(gold_rows)} |",
        f"| other/gap | {splits.get('other', 0) + splits.get('gap', 0)} |",
        "",
    ]
    (REPORT_DIR / "theme_catalog.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return cfg
