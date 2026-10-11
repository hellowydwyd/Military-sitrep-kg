"""python -m graph.build"""

from __future__ import annotations

import argparse
from pathlib import Path

from graph.store import build_graph, load_openie, save_graph, stats

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="从 OpenIE JSONL 建图")
    p.add_argument("--in", dest="src", default=str(DATA / "extract" / "openie.jsonl"))
    p.add_argument("--out", default=str(DATA / "graph" / "sitrep.b1.json"))
    p.add_argument("--force", action="store_true", help="允许覆盖 sitrep.json（B0 基线）")
    args = p.parse_args(argv)
    out = Path(args.out)
    if out.resolve() == (DATA / "graph" / "sitrep.json").resolve() and not args.force:
        print("拒绝覆盖 data/graph/sitrep.json（B0）。请改 --out data/graph/sitrep.b1.json，或 --force。")
        return 2
    rows = load_openie(Path(args.src))
    g = build_graph(rows)
    path = save_graph(g, Path(args.out))
    s = stats(g)
    print(f"写入 {path}")
    print(
        f"Document {s['n_docs']} | Chunk {s['n_chunks']} | "
        f"Event {s['n_events']} | OPEN_REL {s['n_open_rels']}"
    )
    print("事件按 event_time：")
    for day, n in s["events_by_day"].items():
        print(f"  {day}: {n}")
    report = DATA / "reports" / "graph_smoke.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 写图验收",
        "",
        f"- 输入：`{args.src}`",
        f"- 输出：`{path}`",
        f"- Document **{s['n_docs']}** | Chunk **{s['n_chunks']}** | Event **{s['n_events']}** | OPEN_REL **{s['n_open_rels']}** | TLINK **{s['n_tlink']}** | NEXT_IN_TIME **{s.get('n_next_in_time') or 0}**",
        "",
        "## 事件按 event_time",
        "",
    ]
    for day, n in s["events_by_day"].items():
        lines.append(f"- `{day}`: {n}")
    lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告 {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
