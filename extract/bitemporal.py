"""双时态与事实取代：给关系边加时间有效性。

现状：一条边只有「证据文本」，没有时间维度。这导致两类问题：
1. 「某国在 A 地驻军 5000」→ 后来「增兵至 8000」，图上两条边并列，看起来矛盾
2. 「3 月 5 日报道说 X」和「3 月 8 日报道改口说 Y」，无法区分是矛盾还是更新

双时态（bitemporal）的两个时间维度：
- **valid_from / valid_to**：事实在现实世界中生效的区间（事件何时发生）
- **recorded_at**：这条记录入库的时间（稿件何时发布）

事实取代（supersession）：同 (head, relation) 的两条边，若 valid_from 不同且
关系本身是「单值」的（如驻地、兵力、任职），后者取代前者，前者标 invalidated。

哪些关系是单值时序的（会随时间变化，新值取代旧值）：
  驻地、部署方、去向、任职、隶属、辖属
哪些是累积的（可同时成立，不取代）：
  搭载、位于、敌对、会晤、所有事件论元

    python3 -m extract.bitemporal --src data/extract/kgv10_....jsonl --out /tmp/bt.jsonl
    python3 -m extract.bitemporal --src ... --stats
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 单值时序关系：同一主体在同一时间只能有一个值，新值取代旧值。
# 注意「任职」不是单值——一个人可以同时担任多个职务（普京既是总统也是武装力量统帅），
# 「隶属」也不是单值——一个机构可能同时隶属于多个上级（如受国防部与总参谋部双重领导）。
SINGLE_VALUED = {
    "驻地",   # 一支部队同一时刻只有一个驻地
    "部署方",  # 一件装备同一时刻只由一个机构部署
    "去向",   # 一次移动只有一个目的地
}

# 累积关系：可同时成立，不取代
CUMULATIVE = {
    "搭载", "位于", "敌对", "会晤", "任职", "隶属", "辖属",
}


def parse_day(s: str) -> Optional[date]:
    """解析各种时间格式到日期。"""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt))], fmt).date()
        except Exception:
            continue
    # 兜底：截前 10 位
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def load(src: Path) -> list[dict]:
    rows = []
    for line in src.open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def collect_edges(rows: list[dict]) -> list[dict]:
    """把结构关系与事件论元统一整理成带时间的边。"""
    out = []
    for row in rows:
        doc_id = row.get("doc_id") or ""
        pub = parse_day(row.get("publish_time") or "")
        evs = {e["text"]: e for e in (row.get("events") or [])}
        for rel in row.get("relations") or []:
            name = rel.get("name") or ""
            head, tail = rel.get("head") or "", rel.get("tail") or ""
            if not head or not tail:
                continue
            is_arg = head in evs
            ev = evs.get(head) or {}
            # 事实生效时间：事件有 time_norm 用它，否则用发稿日
            valid = parse_day(ev.get("time_norm") or "") if is_arg else None
            if valid is None:
                valid = pub
            out.append({
                "head": head,
                "tail": tail,
                "rel": name,
                "kind": "arg" if is_arg else "struct",
                "valid_from": valid.isoformat() if valid else "",
                "recorded_at": pub.isoformat() if pub else "",
                "doc": doc_id,
                "evidence": rel.get("evidence") or "",
                "source": rel.get("source") or "",
            })
    return out


def apply_supersession(edges: list[dict]) -> tuple[list[dict], dict]:
    """对单值关系做事实取代。

    同一 (head, rel) 下按 valid_from 排序，非最后一条标记
    invalidated_by 指向后继边，valid_to 设为后继的 valid_from。
    """
    stat = Counter()
    # 只对结构关系且单值的做取代
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in edges:
        if e["kind"] == "struct" and e["rel"] in SINGLE_VALUED:
            groups[(e["head"], e["rel"])].append(e)

    for key, group in groups.items():
        if len(group) < 2:
            continue
        # 值都相同则不算取代，只是重复记录
        values = {e["tail"] for e in group}
        if len(values) == 1:
            stat["duplicate_same_value"] += len(group) - 1
            continue
        group.sort(key=lambda e: e["valid_from"] or "")
        for i in range(len(group) - 1):
            cur, nxt = group[i], group[i + 1]
            if not cur["valid_from"] or not nxt["valid_from"]:
                stat["skip_no_time"] += 1
                continue
            if cur["valid_from"] == nxt["valid_from"]:
                # 同一天的不同值，无法判断先后，都保留
                stat["same_day_conflict"] += 1
                continue
            cur["valid_to"] = nxt["valid_from"]
            cur["invalidated_by"] = {"tail": nxt["tail"],
                                     "valid_from": nxt["valid_from"],
                                     "doc": nxt["doc"]}
            stat["superseded"] += 1

    for e in edges:
        if e["rel"] in CUMULATIVE:
            stat["cumulative_kept"] += 1
    return edges, dict(stat)


def stats(edges: list[dict], stat: dict) -> str:
    by_rel = Counter(e["rel"] for e in edges)
    invalid = [e for e in edges if e.get("invalidated_by")]
    lines = [
        "=== 双时态 ===",
        f"边 {len(edges)} 条",
        "",
        f"有效时间覆盖：{sum(1 for e in edges if e['valid_from'])} 条"
        f"（{sum(1 for e in edges if e['valid_from'])/max(len(edges),1):.1%}）",
        f"记录时间覆盖：{sum(1 for e in edges if e['recorded_at'])} 条",
        "",
        "取代统计：",
    ]
    for k, v in sorted(stat.items()):
        lines.append(f"  {k:22s} {v:5d}")
    lines.append("")
    lines.append(f"被取代的边 {len(invalid)} 条，示例：")
    for e in invalid[:5]:
        nxt = e["invalidated_by"]
        lines.append(f"  {e['head']} · {e['rel']}")
        lines.append(f"    {e['valid_from']} → {e['tail']}  "
                     f"（{nxt['valid_from']} 起改为 {nxt['tail']}）")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="双时态与事实取代")
    ap.add_argument("--src", default=str(DATA / "extract" / "kgv10_huanqiu_mil_2026-01.jsonl"))
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    rows = load(Path(a.src))
    edges = collect_edges(rows)
    edges, stat = apply_supersession(edges)
    print(stats(edges, stat))

    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as g:
            for e in edges:
                g.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
