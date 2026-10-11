"""python -m graph.s2 — Report 合并为世界 Event。不覆盖 B1/S1。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from graph.merge import build_s2_graph, eval_against_seed, s2_stats
from graph.s1 import save_graph

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PROTECTED = frozenset({"sitrep.json", "sitrep.b1.json", "sitrep.b1_seq.json", "sitrep.s1.json"})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="S2：合并 Report 为 Event（不写 STATE）")
    p.add_argument("--in", dest="src", default=str(DATA / "graph" / "sitrep.s1.json"))
    p.add_argument("--out", default=str(DATA / "graph" / "sitrep.s2.json"))
    p.add_argument("--seed", default=str(DATA / "eval" / "event_merge_seed.json"))
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    out = Path(args.out)
    if out.name in PROTECTED and not args.force:
        print(f"拒绝覆盖 {out}。请用 --out data/graph/sitrep.s2.json")
        return 2
    src = Path(args.src)
    if not src.is_file():
        print(f"找不到 {src}，请先 python -m graph.s1")
        return 1
    s1 = json.loads(src.read_text(encoding="utf-8"))
    g = build_s2_graph(s1)
    path = save_graph(g, out)
    s = s2_stats(g)
    print(f"写入 {path}")
    print(
        f"Report {s['n_reports']}（已挂 {s['n_reports_linked']}）→ Event {s['n_events']}  "
        f"压缩 {s['compression']}  STATE {s['n_states']}"
    )
    print(f"REPORTS {s['n_reports_edges']}  CONFLICTS_WITH {s['n_conflicts']}")
    print(f"弱完备 {s['n_weak_complete']}  强完备 {s['n_strong_complete']}  status {s['by_status']}")

    seed_path = Path(args.seed)
    evres = None
    if seed_path.is_file():
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        evres = eval_against_seed(g, seed)
        print(f"种子验收 {evres['n_ok']}/{evres['n']}  pass={evres['pass']}")
        for c in evres["checks"]:
            mark = "OK" if c["ok"] else "FAIL"
            print(f"  [{mark}] {c['name']}  {c['detail']}")

    report = DATA / "reports" / "s2_merge.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# S2 Event 合并验收",
        "",
        f"- 输入：`{src}`",
        f"- 输出：`{path}`",
        f"- Report **{s['n_reports']}** → Event **{s['n_events']}**（压缩 {s['compression']}）",
        f"- REPORTS **{s['n_reports_edges']}** | CONFLICTS_WITH **{s['n_conflicts']}** | STATE **{s['n_states']}**",
        f"- 弱完备 **{s['n_weak_complete']}** | 强完备 **{s['n_strong_complete']}**",
        f"- status：{s['by_status']}",
        "",
    ]
    if evres:
        lines.append(f"种子检查 **{evres['n_ok']}/{evres['n']}**")
        lines.append("")
        for c in evres["checks"]:
            lines.append(f"- {'通过' if c['ok'] else '失败'} {c['name']} — {c['detail']}")
        lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告 {report}")
    if s["n_states"] != 0:
        return 1
    if evres and not evres["pass"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
