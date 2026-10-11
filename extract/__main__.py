"""python -m extract --from 2026-08-20 --to 2026-08-24"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from extract.io import load_docs
from extract.pipeline import extract_doc
from extract.report import write_report

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="军事新闻开放事件抽取（单轨 Event，不覆盖旧 openie.jsonl）")
    p.add_argument("--from", dest="date_from", default="2026-08-20")
    p.add_argument("--to", dest="date_to", default="2026-08-24")
    p.add_argument("--max-docs", type=int, default=40, help="0 表示不截断")
    p.add_argument("--out", default=str(DATA / "extract" / "openie_event.jsonl"))
    p.add_argument("--report", default="")
    p.add_argument("--ids-from", default="", help="只抽该 JSONL 里出现的 doc_id（用于 A/B）")
    p.add_argument("--resume", action="store_true", help="跳过输出里已成功的 doc_id，追加写入")
    p.add_argument("--force", action="store_true", help="允许覆盖 openie.jsonl（默认禁止）")
    p.add_argument("--source", default="huanqiu_mil", help="raw 里的 source；空字符串表示不限")
    p.add_argument("--include-undated", action="store_true", help="纳入无 publish_time 的文档（如 CMNEE）")
    args = p.parse_args(argv)

    out_path = Path(args.out)
    if out_path.resolve() == (DATA / "extract" / "openie.jsonl").resolve() and not args.force:
        print(
            "拒绝写入 data/extract/openie.jsonl（旧全量基线）。"
            "请改用 --out data/extract/openie_event.jsonl，或显式 --force。",
            file=sys.stderr,
        )
        return 2

    docs = load_docs(
        DATA / "raw",
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source or None,
        include_undated=args.include_undated,
    )
    if args.ids_from:
        want = set()
        for line in Path(args.ids_from).read_text(encoding="utf-8").splitlines():
            if line.strip():
                want.add(json.loads(line)["doc_id"])
        docs = [d for d in docs if d.doc_id in want]
    if args.max_docs:
        docs = docs[: args.max_docs]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_jsonl(out_path) if args.resume else []
    done_ok = {r["doc_id"] for r in existing if r.get("doc_id") and not r.get("error")}
    todo = [d for d in docs if d.doc_id not in done_ok]
    print(
        f"待抽 {len(todo)} 篇（范围内 {len(docs)}，已成功跳过 {len(done_ok)}）  "
        f"{args.date_from} ~ {args.date_to}",
        flush=True,
    )

    mode = "a" if args.resume and out_path.is_file() else "w"
    rows = list(existing) if mode == "a" else []
    with out_path.open(mode, encoding="utf-8") as f:
        for i, doc in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {doc.publish_time[:10]} {doc.title[:40]}", flush=True)
            try:
                row = extract_doc(doc)
            except Exception as exc:
                print(f"  [error] {exc}", file=sys.stderr)
                row = {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "publish_time": doc.publish_time,
                    "url": doc.url,
                    "source": doc.source,
                    "n_chunks": 0,
                    "chunks": [],
                    "error": str(exc),
                }
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            n_t = sum(len(c.get("triples") or []) for c in row.get("chunks") or [])
            n_e = sum(len(c.get("events") or []) for c in row.get("chunks") or [])
            print(f"  triples={n_t} events={n_e}", flush=True)

    report_path = Path(args.report) if args.report else DATA / "reports" / "extract_smoke.md"
    sample_limit = 25 if len(rows) > 40 else 0
    report = write_report(rows, report_path, sample_limit=sample_limit)
    n_err = sum(1 for r in rows if r.get("error"))
    print(f"JSONL: {out_path}  共 {len(rows)} 篇  失败 {n_err}")
    print(f"报告: {report}")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
