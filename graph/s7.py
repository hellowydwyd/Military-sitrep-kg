"""S7：证据包推理环 + 引用硬校验。只读 S6，不回写事实。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reason.ask import answer_question, check_answer, run_seed

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="S7：证据包作答 + 引用硬校验")
    p.add_argument("--in", dest="src", default=str(DATA / "graph" / "sitrep.s6.json"))
    p.add_argument("--seed", default=str(DATA / "eval" / "event_merge_seed.json"))
    p.add_argument("--ask", default="", help="自然语言题；空则跑种子 10 题")
    args = p.parse_args(argv)

    src = Path(args.src)
    if not src.is_file():
        print(f"找不到 {src}，请先 python -m graph.s6")
        return 1
    g = json.loads(src.read_text(encoding="utf-8"))

    if args.ask:
        result = answer_question(g, {"ask": args.ask})
        ev = check_answer(result, {"id": result.get("id"), "answer": result.get("answer"), "ask": args.ask})
        print(result.get("text") or "")
        print(f"\nanswer={result.get('answer')}  cite_ok={ev['cite_ok']}  n_cite={ev['n_cite']}")
        return 0 if ev["cite_ok"] else 1

    seed = json.loads(Path(args.seed).read_text(encoding="utf-8"))
    ev = run_seed(g, seed)
    print(f"种子检查 {ev['n_ok']}/{ev['n']} pass={ev['pass']}")
    for c in ev["checks"]:
        print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")

    report = DATA / "reports" / "s7_ask.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# S7 证据包推理 / 引用校验",
        "",
        f"- 输入：`{src}`",
        f"- 种子：`{args.seed}`",
        f"- 检查 **{ev['n_ok']}/{ev['n']}**",
        "",
    ]
    for row in ev.get("rows") or []:
        q = row["q"]
        r = row["result"]
        e = row["eval"]
        mark = "通过" if e["ok"] else "失败"
        lines.append(f"## {q.get('id')} {mark}")
        lines.append("")
        lines.append(f"- 问：{q.get('ask')}")
        lines.append(f"- 金标：`{q.get('answer')}`  程序：`{r.get('answer')}`")
        lines.append(f"- 引用 {e.get('n_cite')} 条；cite_ok={e.get('cite_ok')}")
        lines.append("")
        lines.append(r.get("text") or "")
        lines.append("")
    for c in ev["checks"]:
        if c["name"] in {row["q"].get("id") for row in ev.get("rows") or []}:
            continue
        lines.append(f"- {'通过' if c['ok'] else '失败'} {c['name']} — {c['detail']}")
    lines += [
        "",
        "查询：`python -m graph.s7 --ask '8-10 美军机在贝兹梅尔?'`",
        "",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告 {report}")
    return 0 if ev["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
