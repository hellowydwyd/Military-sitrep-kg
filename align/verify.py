"""验证写回后的对齐：小金标 + 按主体时间窗。

python -m align.verify
python -m align.verify --actor 以军 --from 2026-08-18 --to 2026-08-24
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from align.cluster import pair_key
from graph.store import load_graph

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GOLD = Path(__file__).resolve().parent / "gold_pairs.json"


def mention_canon(graph: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for ev in graph.get("events") or []:
        for a in ev.get("actors_aligned") or []:
            m = a.get("mention") or ""
            if m:
                out[m] = a.get("id") or a.get("canonical") or m
    return out


def gold_check(graph: dict, gold: list[dict]) -> dict:
    amap = mention_canon(graph)
    rows = []
    tp = fp = tn = fn = 0
    missing = 0
    for g in gold:
        a, b, lab = g["a"], g["b"], g["label"]
        ia, ib = amap.get(a), amap.get(b)
        if not ia or not ib:
            missing += 1
            rows.append({"a": a, "b": b, "gold": lab, "got": "图中缺名"})
            continue
        merged = ia == ib
        if lab == "same_as":
            if merged:
                tp += 1
            else:
                fn += 1
        else:
            if merged:
                fp += 1
            else:
                tn += 1
        rows.append({"a": a, "b": b, "gold": lab, "got": "same_as" if merged else "not_merged"})
    same = sum(1 for g in gold if g["label"] == "same_as")
    other = len(gold) - same
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / same if same else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "n": len(gold),
        "missing": missing,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "same": same,
        "other": other,
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "rows": rows,
    }


def resolve_actor(graph: dict, name: str) -> tuple[str, str]:
    """返回 (id, canonical 显示名)。"""
    for ev in graph.get("events") or []:
        for a in ev.get("actors_aligned") or []:
            if a.get("mention") == name or a.get("canonical") == name or a.get("id") == name:
                return a.get("id") or "", a.get("canonical") or name
    for node in graph.get("actors") or []:
        if name in (node.get("aliases") or []) or node.get("canonical") == name or node.get("id") == name:
            return node.get("id") or "", node.get("canonical") or name
    return "", name


def resolve_location(graph: dict, name: str) -> tuple[str, str]:
    """返回 (id, canonical 显示名)。"""
    for ev in graph.get("events") or []:
        for a in ev.get("locations_aligned") or []:
            if a.get("mention") == name or a.get("canonical") == name or a.get("id") == name:
                return a.get("id") or "", a.get("canonical") or name
    for node in graph.get("locations") or []:
        if name in (node.get("aliases") or []) or node.get("canonical") == name or node.get("id") == name:
            return node.get("id") or "", node.get("canonical") or name
    return "", name


def events_for_actor(graph: dict, actor_id: str, day_from: str, day_to: str) -> list[dict]:
    out = []
    for ev in graph.get("events") or []:
        day = ev.get("event_time") or ""
        if not day:
            continue
        if day_from and day < day_from:
            continue
        if day_to and day > day_to:
            continue
        hits = [a for a in ev.get("actors_aligned") or [] if a.get("id") == actor_id]
        if hits:
            item = dict(ev)
            item["_mentions"] = [a.get("mention") for a in hits]
            out.append(item)
    out.sort(key=lambda e: (e.get("event_time") or "", e.get("event_id") or ""))
    return out


def events_exact_mention(graph: dict, mention: str, day_from: str, day_to: str) -> list[dict]:
    out = []
    for ev in graph.get("events") or []:
        day = ev.get("event_time") or ""
        if not day:
            continue
        if day_from and day < day_from:
            continue
        if day_to and day > day_to:
            continue
        if mention in (ev.get("actors") or []):
            out.append(ev)
    return out


def render(gold: dict, actor: str, aid: str, canon: str, aligned: list[dict], exact: list[dict], day_from: str, day_to: str) -> str:
    lines = [
        "# 对齐验证",
        "",
        "## 1. 小金标（写回后的图）",
        "",
        f"- 对数 **{gold['n']}**（图中缺名 {gold['missing']}）",
        f"- same_as：TP {gold['tp']} / FN {gold['fn']}；不应合：TN {gold['tn']} / FP {gold['fp']}",
        f"- P={gold['precision']} R={gold['recall']} F1={gold['f1']}",
        "",
    ]
    bad = [r for r in gold["rows"] if (r["gold"] == "same_as" and r["got"] != "same_as") or (r["gold"] != "same_as" and r["got"] == "same_as") or r["got"] == "图中缺名"]
    if bad:
        lines.append("不一致：")
        lines.append("")
        for r in bad:
            lines.append(f"- `{r['a']}` ↔ `{r['b']}` 金标 {r['gold']}，图上 {r['got']}")
        lines.append("")
    lines += [
        f"## 2. 按主体时间窗 `{actor}` → **{canon}** `{aid}`",
        "",
        f"- 窗口 {day_from}～{day_to}",
        f"- 只匹配原文「{actor}」：**{len(exact)}** 条",
        f"- 按 canonical 收齐别名：**{len(aligned)}** 条",
        "",
    ]
    mentions = Counter()
    for ev in aligned:
        for m in ev.get("_mentions") or []:
            mentions[m] += 1
    if mentions:
        lines.append("窗口内 mention 分布：")
        lines.append("")
        for m, n in mentions.most_common():
            lines.append(f"- {n} `{m}`")
        lines.append("")
    lines.append("事件（最多 30 条）：")
    lines.append("")
    for ev in aligned[:30]:
        ms = "、".join(ev.get("_mentions") or [])
        lines.append(
            f"- `{ev.get('event_time')}` trigger=`{ev.get('trigger')}` mention={ms} "
            f"[{(ev.get('event_id') or '')[:8]}] {ev.get('title') or ''}"
        )
    if len(aligned) > 30:
        lines.append(f"- … 共 {len(aligned)} 条")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="验证 Actor 对齐写回")
    p.add_argument("--graph", default=str(DATA / "graph" / "sitrep.json"))
    p.add_argument("--actor", default="以军")
    p.add_argument("--from", dest="day_from", default="2026-08-18")
    p.add_argument("--to", dest="day_to", default="2026-08-24")
    p.add_argument("--out", default=str(DATA / "reports" / "align_verify.md"))
    args = p.parse_args(argv)

    graph = load_graph(Path(args.graph))
    if not any(ev.get("actors_aligned") for ev in graph.get("events") or []):
        print("图上没有 actors_aligned。请先：python -m align.apply")
        return 1
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    gstat = gold_check(graph, gold)
    aid, canon = resolve_actor(graph, args.actor)
    if not aid:
        print(f"找不到主体 {args.actor}")
        return 1
    aligned = events_for_actor(graph, aid, args.day_from, args.day_to)
    exact = events_exact_mention(graph, args.actor, args.day_from, args.day_to)
    text = render(gstat, args.actor, aid, canon, aligned, exact, args.day_from, args.day_to)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
