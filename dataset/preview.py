"""把抽取结果导出成人类可读的校对稿，供人工过一遍再定稿数据集。

两种形态：
- --format md   Markdown，每篇一节，事件与论元逐条列出，便于通读与批注
- --format tsv  每行一个「事件-论元」，可直接导入表格筛选

都会标出可疑项：必填槽位缺失、论元值不在正文里、槽位是否合法。

    python3 -m dataset.preview --src data/extract/kgv9_huanqiu_mil_2026-01.jsonl
    python3 -m dataset.preview --src ... --format tsv --out data/reports/ee_check.tsv
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Optional

from extract.slots import ROLE_NAMES, slots_of

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reports"


def load(src: Path) -> list[dict]:
    rows = []
    for line in src.open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def args_of(row: dict, ev_text: str) -> list[tuple[str, str]]:
    out = []
    for x in row.get("relations") or []:
        if x.get("head") != ev_text:
            continue
        if x["name"] == "包含" or x["name"] not in ROLE_NAMES:
            continue
        out.append((x["name"], x.get("tail") or ""))
    return out


def issues_of(row: dict, ev: dict, args: list[tuple[str, str]]) -> list[str]:
    """返回该事件的可疑点，空列表表示通过。"""
    out = []
    body = row.get("body") or ""
    # 归一后的实体：规范名可能不在正文里，但它记录的原别名在，也算合法
    alias_of: dict[str, list[str]] = {}
    for e in row.get("entities") or []:
        al = e.get("aliases") or []
        if al:
            alias_of[e.get("text") or ""] = al
    t, sub = ev.get("event_type") or "", ev.get("subtype") or ""
    names = [a[0] for a in args]

    allowed = {s.name for s in slots_of(t, sub)}
    if t and t != "其他":
        if not allowed:
            out.append(f"类型 {t}·{sub} 未在槽位表中定义")
        unknown = [n for n in names if n not in allowed]
        if unknown:
            out.append(f"槽位越界: {'/'.join(unknown)}")

    for s in slots_of(t, sub):
        if s.fill == "req" and s.name not in names:
            out.append(f"缺必填 {s.name}")

    for role, text in args:
        if not text or not body:
            continue
        if text in body:
            continue
        # 规范名不在正文，但它的别名在，说明只是归一，不算错
        if any(a in body for a in alias_of.get(text, [])):
            continue
        out.append(f"值不在正文: {role}={text[:16]}")

    if not args:
        out.append("零论元")
    return out


def to_md(rows: list[dict]) -> str:
    lines = ["# 事件抽取人工校对稿", ""]
    total_ev = total_arg = 0
    flagged = 0
    for i, row in enumerate(rows, 1):
        evs = row.get("events") or []
        lines.append(f"## {i}. {row.get('title') or ''}")
        lines.append("")
        lines.append(f"`{row.get('doc_id','')[:16]}`　{row.get('publish_time','')}　"
                     f"事件 {len(evs)}")
        lines.append("")
        body = (row.get("body") or "").replace("\n", " ")
        lines.append(f"> {body[:180]}{'…' if len(body) > 180 else ''}")
        lines.append("")
        for j, ev in enumerate(evs, 1):
            total_ev += 1
            args = args_of(row, ev["text"])
            total_arg += len(args)
            bad = issues_of(row, ev, args)
            if bad:
                flagged += 1
            mark = " ⚠" if bad else ""
            lines.append(f"**事件 {j}**{mark} `{ev.get('event_type')}·{ev.get('subtype') or '-'}` "
                         f"触发词「{ev.get('trigger') or ''}」")
            lines.append("")
            lines.append(f"- 文本：{ev['text']}")
            for role, text in args:
                lines.append(f"  - {role} → {text}")
            if not args:
                lines.append("  - （无论元）")
            if bad:
                lines.append(f"  - ⚠ {'；'.join(bad)}")
            lines.append("")
        lines.append("")
    head = (f"共 {len(rows)} 篇，{total_ev} 事件，{total_arg} 论元，"
            f"可疑事件 {flagged} 个（{flagged/max(total_ev,1):.1%}）")
    lines.insert(1, head)
    lines.insert(2, "")
    return "\n".join(lines)


def to_tsv(rows: list[dict]) -> str:
    cols = ["doc_idx", "title", "event_idx", "text", "event_type", "subtype",
            "trigger", "role", "arg", "issues"]
    out = ["\t".join(cols)]
    for i, row in enumerate(rows, 1):
        for j, ev in enumerate(row.get("events") or [], 1):
            args = args_of(row, ev["text"])
            bad = "；".join(issues_of(row, ev, args))
            if not args:
                out.append("\t".join([
                    str(i), row.get("title") or "", str(j), ev["text"],
                    ev.get("event_type") or "", ev.get("subtype") or "",
                    ev.get("trigger") or "", "(无)", "", bad,
                ]))
                continue
            for role, text in args:
                out.append("\t".join([
                    str(i), row.get("title") or "", str(j), ev["text"],
                    ev.get("event_type") or "", ev.get("subtype") or "",
                    ev.get("trigger") or "", role, text, bad,
                ]))
    return "\n".join(out)


def summary(rows: list[dict]) -> dict:
    ev_n = Counter()
    role_n = Counter()
    flagged = 0
    total = 0
    zero = 0
    for row in rows:
        for ev in row.get("events") or []:
            total += 1
            t = ev.get("event_type") or ""
            ev_n[t] += 1
            args = args_of(row, ev["text"])
            if not args:
                zero += 1
            if issues_of(row, ev, args):
                flagged += 1
            for r, _ in args:
                role_n[r] += 1
    return {
        "docs": len(rows),
        "events": total,
        "arguments": sum(role_n.values()),
        "avg_args": round(sum(role_n.values()) / max(total, 1), 2),
        "zero_arg_events": zero,
        "flagged": flagged,
        "by_type": dict(ev_n.most_common()),
        "by_role": dict(role_n.most_common()),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="导出人工校对稿")
    ap.add_argument("--src", required=True)
    ap.add_argument("--format", choices=("md", "tsv"), default="md")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    rows = load(Path(a.src))
    if a.limit:
        rows = rows[: a.limit]

    text = to_md(rows) if a.format == "md" else to_tsv(rows)
    out = Path(a.out) if a.out else OUT / f"ee_check.{a.format}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    print(json.dumps(summary(rows), ensure_ascii=False, indent=2))
    print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
