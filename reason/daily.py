"""按日取证据包，生成带引用的清单（程序组装，不编事实）。

python -m reason.daily --date 2026-08-20
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

from graph.store import load_graph

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _preview(text: str, n: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def events_on_day(graph: dict, day: str) -> list[dict]:
    """新图只收 precision=day；旧图仍把 pub/triple 回填算进当日（B0 基线）。"""
    layer = graph.get("layer") or ""
    if layer.startswith(("s4", "s5", "s6", "s7")):
        from graph.s4 import events_on_day as s4_day

        return s4_day(graph, day)
    out = []
    for e in graph.get("events") or []:
        if e.get("time_background"):
            continue
        prec = e.get("time_precision") or ""
        if prec in ("pub", "triple", ""):
            if (e.get("event_time") or "") == day:
                out.append(e)
            continue
        if prec == "day" and (e.get("event_time") or "") == day:
            out.append(e)
    return out


def docs_published_on(graph: dict, day: str) -> list[dict]:
    return [d for d in graph.get("documents") or [] if (d.get("publish_time") or "")[:10] == day]


def evidence_pack(graph: dict, day: str) -> dict[str, Any]:
    layer = graph.get("layer") or ""
    if layer.startswith(("s1", "s2", "s3", "s4", "s5", "s6", "s7")) and graph.get("reports"):
        from reason.ask import make_pack

        return make_pack(graph, events_on_day(graph, day), day=day)

    chunks = {c["chunk_id"]: c for c in graph.get("chunks") or []}
    events = events_on_day(graph, day)
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        item = dict(ev)
        ch = chunks.get(ev.get("chunk_id") or "")
        item["chunk_text"] = (ch or {}).get("text") or ""
        by_doc[ev.get("doc_id") or ""].append(item)
    docs = {d["doc_id"]: d for d in graph.get("documents") or []}
    groups = []
    for doc_id, evs in by_doc.items():
        doc = docs.get(doc_id) or {}
        groups.append({"document": doc, "events": evs})
    groups.sort(key=lambda g: g["document"].get("publish_time") or "")
    pub_only = [
        d
        for d in docs_published_on(graph, day)
        if d["doc_id"] not in by_doc
    ]
    return {
        "day": day,
        "n_events": len(events),
        "n_docs": len(groups),
        "groups": groups,
        "published_without_event": pub_only,
    }


def render_brief(pack: dict) -> str:
    day = pack["day"]
    lines = [
        f"# {day} 态势清单",
        "",
        f"- 规范事件 **{pack['n_events']}** 条，来自 **{pack['n_docs']}** 篇报道",
        "- 每条结论只依据证据包；`single_source` 未做多源合并，不得写成「已确认」。",
        "",
    ]
    for i, g in enumerate(pack["groups"], 1):
        doc = g["document"]
        lines.append(f"## {i}. {doc.get('title')}")
        lines.append("")
        lines.append(f"- 报道时间：`{doc.get('publish_time')}`")
        lines.append(f"- URL：{doc.get('url')}")
        lines.append("")
        for ev in g["events"]:
            args = "，".join(f"{a.get('role')}={a.get('text')}" for a in ev.get("arguments") or [])
            cite = f"[{ev['event_id'][:8]}|{ev.get('chunk_id')}]"
            loc = "、".join(ev.get("locations") or []) or "地点未抽取"
            actors = "、".join(ev.get("actors") or []) or "主体未抽取"
            lines.append(
                f"- {cite} trigger=`{ev.get('trigger')}`；{actors}；{loc}"
                + (f"；论元：{args}" if args else "")
            )
            lines.append(f"  - 原文：{_preview(ev.get('evidence') or ev.get('chunk_text') or '')}")
        lines.append("")
    if pack.get("published_without_event"):
        lines.append("## 当日见报、但事件日不在本日的稿件")
        lines.append("")
        for d in pack["published_without_event"]:
            lines.append(f"- {d.get('title')}（`{d.get('publish_time')}`）")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="按日态势清单（证据包 + 引用）")
    p.add_argument("--date", required=True, help="YYYY-MM-DD，按事件日过滤")
    p.add_argument("--graph", default=str(DATA / "graph" / "sitrep.json"))
    p.add_argument("--out", default="")
    p.add_argument("--llm", action="store_true", help="基于证据包做 Time-CoT 简报并校验引用")
    args = p.parse_args(argv)
    graph = load_graph(Path(args.graph))
    pack = evidence_pack(graph, args.date)
    text = render_brief(pack)
    out = Path(args.out) if args.out else DATA / "reports" / f"daily_{args.date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n已写 {out}")
    if args.llm:
        from reason.cot import generate_brief

        cot_text, report = generate_brief(pack)
        cot_out = out.with_name(f"daily_{args.date}.cot.md")
        if args.out:
            cot_out = Path(str(out).replace(".md", ".cot.md")) if str(out).endswith(".md") else Path(str(out) + ".cot.md")
        cot_out.write_text(cot_text, encoding="utf-8")
        print("\n----- Time-CoT -----\n")
        print(cot_text)
        print(
            f"\n已写 {cot_out}  cite_rate={report.get('cite_rate')} "
            f"kept={report.get('n_claim_kept')}/{report.get('n_claim')} "
            f"ok={report.get('ok')}"
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
