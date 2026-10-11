"""S4：时间纯洁。unknown ≠ 见报日；当日清单过滤；不覆盖 S3。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from graph.merge import pred_class
from graph.s1 import save_graph
from graph.time import (
    event_meta,
    inherit_anchor,
    parse_pub_date,
    same_sentence_abs_day,
    usable_occur_anchor,
)
from graph.time_eval import run_t1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PROTECTED = frozenset(
    {
        "sitrep.json",
        "sitrep.b1.json",
        "sitrep.b1_seq.json",
        "sitrep.s1.json",
        "sitrep.s2.json",
        "sitrep.s3.json",
        "sitrep.s5.json",
        "sitrep.s6.json",
    }
)
SAME_DAY_CUES = ("当天", "同日")


def retime_reports(graph: dict) -> dict:
    chunks = {c["chunk_id"]: c for c in graph.get("chunks") or []}
    by_chunk: dict[str, list[dict]] = defaultdict(list)
    for r in graph.get("reports") or []:
        by_chunk[r.get("chunk_id") or ""].append(r)

    for cid, reps in by_chunk.items():
        text = (chunks.get(cid) or {}).get("text") or (reps[0].get("text") if reps else "") or ""
        prev_abs = None
        for r in reps:
            pub = r.get("publish_time")
            raw = str(r.get("raw_time") or "")
            kind = r.get("kind") or ""
            evidence = r.get("evidence") or ""
            if any(k in raw for k in SAME_DAY_CUES) and kind != "发生":
                anchor = same_sentence_abs_day(evidence, pub)
            else:
                anchor = inherit_anchor(prev_abs, text, evidence, raw, pub)
            meta = event_meta(
                {"raw_time": raw, "arguments": r.get("arguments") or []},
                pub,
                None,
                anchor,
            )
            r["t_event_hint"] = meta.get("day") or ""
            r["t_event_end_hint"] = meta.get("end") or ""
            r["time_precision"] = meta.get("precision") or "unknown"
            r["time_background"] = bool(meta.get("background"))
            r["tlink_cue"] = meta.get("cue") or ""
            if usable_occur_anchor(kind, r["t_event_hint"], r["time_precision"], pub):
                prev_abs = r["t_event_hint"]
    return graph


def _recompute_event(ev: dict, members: list[dict]) -> None:
    occur = [
        r
        for r in members
        if (r.get("kind") == "发生")
        and pred_class(r.get("trigger") or "", "发生") in ("strike", "happen", "tlink_left")
    ]
    pool = occur or [r for r in members if r.get("kind") != "计划"] or members
    days = sorted(
        {
            (r.get("t_event_hint") or "")[:10]
            for r in pool
            if (r.get("time_precision") == "day") and len((r.get("t_event_hint") or "")) >= 10
        }
    )
    ends = [r.get("t_event_end_hint") or "" for r in pool if r.get("t_event_end_hint")]
    if len(days) > 1:
        ev["event_time"] = days[0]
        ev["event_time_end"] = days[-1]
        ev["time_precision"] = "interval"
    elif len(days) == 1:
        ev["event_time"] = days[0]
        ev["event_time_end"] = ends[0] if ends else ""
        ev["time_precision"] = "day"
    else:
        # 月/区间精度：保留非 unknown
        prec_hits = [
            r
            for r in pool
            if (r.get("time_precision") or "unknown") not in ("unknown", "fuzzy", "")
            and r.get("t_event_hint")
        ]
        if prec_hits:
            r0 = prec_hits[0]
            ev["event_time"] = r0.get("t_event_hint") or ""
            ev["event_time_end"] = r0.get("t_event_end_hint") or ""
            ev["time_precision"] = r0.get("time_precision")
        else:
            ev["event_time"] = ""
            ev["event_time_end"] = ""
            ev["time_precision"] = "unknown"
    pubs = [parse_pub_date(r.get("publish_time")) for r in members]
    pubs = [p for p in pubs if p]
    ev["time_background"] = False
    day = ev.get("event_time") or ""
    if day and pubs:
        from datetime import date

        try:
            if ev.get("time_precision") == "month" and len(day) == 7:
                d = date.fromisoformat(day + "-01")
            else:
                d = date.fromisoformat(day[:10])
            ev["time_background"] = min((p - d).days for p in pubs) >= 30
        except ValueError:
            pass


def recompute_events(graph: dict) -> dict:
    by_ev: dict[str, list[dict]] = defaultdict(list)
    for r in graph.get("reports") or []:
        if r.get("event_id"):
            by_ev[r["event_id"]].append(r)
    for ev in graph.get("events") or []:
        _recompute_event(ev, by_ev.get(ev["event_id"]) or [])
    return graph


def events_on_day(graph: dict, day: str, *, include_plan: bool = False) -> list[dict]:
    """精确日清单：day、非 background、默认排除计划；unknown 不进。"""
    out = []
    for e in graph.get("events") or []:
        if e.get("time_background"):
            continue
        if not include_plan and (e.get("kind") or "") == "计划":
            continue
        if (e.get("time_precision") or "") != "day":
            continue
        if (e.get("event_time") or "")[:10] != day:
            continue
        out.append(e)
    return out


def pub_impersonate_count(graph: dict) -> int:
    n = 0
    for r in graph.get("reports") or []:
        raw = str(r.get("raw_time") or "").strip()
        hint = (r.get("t_event_hint") or "")[:10]
        pub = (r.get("publish_time") or "")[:10]
        if not raw and hint and pub and hint == pub and r.get("time_precision") == "day":
            n += 1
    for e in graph.get("events") or []:
        if (e.get("time_precision") or "") != "day":
            continue
        et = (e.get("event_time") or "")[:10]
        if not et:
            continue
        pubs = {
            (r.get("publish_time") or "")[:10]
            for r in graph.get("reports") or []
            if r.get("event_id") == e["event_id"]
        }
        raws = [
            str(r.get("raw_time") or "").strip()
            for r in graph.get("reports") or []
            if r.get("event_id") == e["event_id"]
        ]
        if et in pubs and not any(raws):
            n += 1
    return n


def s4_checks(graph: dict) -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    n_imp = pub_impersonate_count(graph)
    add("见报日冒充=0", n_imp == 0, f"n={n_imp}")

    d20 = events_on_day(graph, "2026-08-20")
    d18 = events_on_day(graph, "2026-08-18")
    preds20 = {(e.get("predicate"), e.get("kind")) for e in d20}
    add("08-20 清单不含计划", all(e.get("kind") != "计划" for e in d20), f"n={len(d20)}")
    syria_ids = {
        r.get("event_id")
        for r in graph.get("reports") or []
        if (r.get("chunk_id") or "").startswith("2e5e3e0593fb") and r.get("kind") == "发生" and r.get("trigger") in ("发动", "空袭")
    }
    syria = [e for e in graph.get("events") or [] if e.get("event_id") in syria_ids]
    add("叙机场空袭不进 08-20 精确日", all(e not in d20 for e in syria) and bool(syria), f"syria={[e.get('event_time') for e in syria]} daily20={len(d20)}")
    add("叙机场可在 08-18 或 interval 锚定", any((e.get("event_time") or "").startswith("2026-08-18") for e in syria), f"{[(e.get('event_time'), e.get('time_precision')) for e in syria]}")

    falcon = [e for e in graph.get("events") or [] if "猎鹰" in json.dumps(e, ensure_ascii=False) or e.get("predicate") == "举行"]
    add("猎鹰盾牌不进 08-21 已发生", all(e not in events_on_day(graph, "2026-08-21") for e in falcon), f"{[(e.get('kind'), e.get('event_time'), e.get('time_precision')) for e in falcon]}")

    ru_mod = [
        r
        for r in graph.get("reports") or []
        if r.get("chunk_id") == "d79bfeecb9b8_00" and r.get("trigger") == "通报" and "国防部" in json.dumps(r.get("arguments"), ensure_ascii=False)
    ]
    add(
        "俄国防部通报当天不继承哀悼日 08-21",
        all((r.get("t_event_hint") or "")[:10] != "2026-08-21" for r in ru_mod) and bool(ru_mod),
        f"{[(r.get('t_event_hint'), r.get('time_precision')) for r in ru_mod]}",
    )

    n_unk_daily = sum(1 for e in d20 if (e.get("time_precision") or "") in ("unknown", "fuzzy"))
    add("精确日清单无 unknown", n_unk_daily == 0, f"n={n_unk_daily}")

    n_ok = sum(1 for c in checks if c["ok"])
    return {"n": len(checks), "n_ok": n_ok, "pass": n_ok == len(checks), "checks": checks, "n_daily_0820": len(d20), "n_daily_0818": len(d18)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="S4：时间纯洁 + 当日清单过滤")
    p.add_argument("--in", dest="src", default=str(DATA / "graph" / "sitrep.s3.json"))
    p.add_argument("--out", default=str(DATA / "graph" / "sitrep.s4.json"))
    p.add_argument("--day", default="", help="打印该日精确清单")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    if args.day:
        path = Path(args.src) if Path(args.src).name.endswith("s4.json") and Path(args.src).is_file() else DATA / "graph" / "sitrep.s4.json"
        if not path.is_file():
            print("请先 python -m graph.s4")
            return 1
        g = json.loads(path.read_text(encoding="utf-8"))
        evs = events_on_day(g, args.day)
        print(json.dumps([{"event_id": e["event_id"], "kind": e.get("kind"), "predicate": e.get("predicate"), "event_time": e.get("event_time")} for e in evs], ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    if out.name in PROTECTED and not args.force:
        print(f"拒绝覆盖 {out}")
        return 2
    src = Path(args.src)
    if not src.is_file():
        print(f"找不到 {src}，请先 python -m graph.s3")
        return 1
    g = json.loads(src.read_text(encoding="utf-8"))
    retime_reports(g)
    recompute_events(g)
    g["layer"] = "s4_time"
    path = save_graph(g, out)
    ev = s4_checks(g)
    t1_ok, t1_lines = run_t1(json.loads((DATA / "eval" / "time_gold.json").read_text(encoding="utf-8")))
    print(f"写入 {path}")
    print(f"T1 短语金标 {'通过' if t1_ok else '失败'}")
    print(f"S4 检查 {ev['n_ok']}/{ev['n']} pass={ev['pass']}  daily08-20={ev['n_daily_0820']}")
    for c in ev["checks"]:
        print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")

    report = DATA / "reports" / "s4_time.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# S4 时间纯洁验收",
        "",
        f"- 输入：`{src}`",
        f"- 输出：`{path}`",
        f"- 08-20 精确日清单 **{ev['n_daily_0820']}** 条（排除计划 / background / unknown）",
        "",
        *t1_lines,
        f"图级检查 **{ev['n_ok']}/{ev['n']}**",
        "",
    ]
    for c in ev["checks"]:
        lines.append(f"- {'通过' if c['ok'] else '失败'} {c['name']} — {c['detail']}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告 {report}")
    return 0 if ev["pass"] and t1_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
