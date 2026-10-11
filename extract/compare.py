"""对比两份 OpenIE JSONL 的粒度/时间/摘录指标。

python -m extract.compare --old data/extract/openie_slice40.jsonl --new data/extract/openie_ab12.jsonl --ids data/extract/ab_ids12.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

TALK = ("称", "表示", "透露", "指出", "强调", "警告", "声称", "认为", "说", "谴责", "批评")


def load_rows(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("doc_id"):
            out[row["doc_id"]] = row
    return out


def metrics(rows: list[dict]) -> dict:
    n_chunk = n_ev = n_talk = empty_raw = dup = no_actor = evd_ok = 0
    ev_per = []
    kinds: Counter = Counter()
    for r in rows:
        for c in r.get("chunks") or []:
            evs = c.get("events") or []
            n_chunk += 1
            n_ev += len(evs)
            ev_per.append(len(evs))
            ct = Counter(e.get("trigger") for e in evs)
            dup += sum(v - 1 for v in ct.values() if v > 1)
            text = c.get("text") or ""
            for e in evs:
                t = e.get("trigger") or ""
                if any(k in t for k in TALK):
                    n_talk += 1
                rt = (e.get("raw_time") or "").strip()
                if not rt or rt in ("空", "无"):
                    empty_raw += 1
                if not any(k in str(a.get("role")) for k in ("主体", "发起", "参与") for a in e.get("arguments") or []):
                    no_actor += 1
                evd = e.get("evidence") or ""
                if evd and t and t in evd:
                    evd_ok += 1
                k = e.get("kind") or ""
                if k:
                    kinds[k] += 1
    total = n_ev or 1
    p90 = sorted(ev_per)[int(len(ev_per) * 0.9)] if ev_per else 0
    return {
        "docs": len(rows),
        "chunks": n_chunk,
        "events": n_ev,
        "ev_per_chunk_mean": round(sum(ev_per) / len(ev_per), 2) if ev_per else 0,
        "ev_per_chunk_p90": p90,
        "ev_per_chunk_max": max(ev_per) if ev_per else 0,
        "talk_pct": round(100 * n_talk / total, 1),
        "empty_raw_pct": round(100 * empty_raw / total, 1),
        "dup_extra": dup,
        "no_actor": no_actor,
        "evidence_has_trigger_pct": round(100 * evd_ok / total, 1),
        "kinds": dict(kinds),
    }


def per_doc(old: dict, new: dict, ids: list[str]) -> list[str]:
    lines = ["## 分篇事件数", ""]
    for i in ids:
        o = sum(len(c.get("events") or []) for c in (old.get(i) or {}).get("chunks") or [])
        n = sum(len(c.get("events") or []) for c in (new.get(i) or {}).get("chunks") or [])
        title = (new.get(i) or old.get(i) or {}).get("title") or i
        mark = "↓" if n < o else ("=" if n == o else "↑")
        lines.append(f"- {mark} {o}→{n}  {title[:48]}")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--ids", default="")
    p.add_argument("--out", default="data/reports/openie_ab.md")
    args = p.parse_args(argv)
    old, new = load_rows(Path(args.old)), load_rows(Path(args.new))
    ids = list(new.keys())
    if args.ids:
        ids = [json.loads(l)["doc_id"] for l in Path(args.ids).read_text(encoding="utf-8").splitlines() if l.strip()]
    old_rows = [old[i] for i in ids if i in old]
    new_rows = [new[i] for i in ids if i in new]
    mo, mn = metrics(old_rows), metrics(new_rows)
    lines = [
        "# OpenIE 小规模 A/B",
        "",
        "对照同一批文档：旧 prompt/校验 vs 一句一事一时 + 事件摘录 + raw_time 原文。",
        "",
        "| 指标 | 旧 | 新 |",
        "|------|----|----|",
    ]
    keys = [
        ("docs", "篇数"),
        ("chunks", "块数"),
        ("events", "事件条数"),
        ("ev_per_chunk_mean", "每块均值"),
        ("ev_per_chunk_p90", "每块 P90"),
        ("ev_per_chunk_max", "每块最大"),
        ("talk_pct", "言论 trigger %"),
        ("empty_raw_pct", "空 raw_time %"),
        ("dup_extra", "同块重复 trigger 多余条"),
        ("no_actor", "无主体"),
        ("evidence_has_trigger_pct", "摘录含 trigger %"),
    ]
    for k, lab in keys:
        lines.append(f"| {lab} | {mo[k]} | {mn[k]} |")
    if mn.get("kinds"):
        lines.append(f"| kind 分布 | — | {mn['kinds']} |")
    lines += ["", *per_doc(old, new, ids), ""]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
