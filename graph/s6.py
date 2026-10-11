"""S6：同块 TLINK + 跨文档 NEXT_IN_TIME。不覆盖 S5。不写因果。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from graph.proximity import next_in_time_edges
from graph.s1 import save_graph
from graph.time import detect_tlink

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
        "sitrep.s4.json",
        "sitrep.s5.json",
        "sitrep.s7.json",
    }
)
MAX_DELTA = 7
KEEP_EDGE = frozenset(
    {
        "CONTAINS",
        "SOURCED_FROM",
        "REPORTS",
        "PARTICIPANT",
        "LOCATED_AT",
        "INVOLVES_PLATFORM",
        "STATE",
        "CONFLICTS_WITH",
        "SUPERSEDES",
        "CLAIM",
        "IN_DOCUMENT",
        "OPEN_REL",
    }
)


def _annotate(events: list[dict], reports: list[dict]) -> list[dict]:
    by_eid: dict[str, dict] = {e["event_id"]: dict(e) for e in events}
    for r in reports:
        eid = r.get("event_id") or ""
        if eid not in by_eid:
            continue
        ev = by_eid[eid]
        ev.setdefault("_chunk_ids", [])
        ev.setdefault("_doc_ids", [])
        cid, did = r.get("chunk_id") or "", r.get("doc_id") or ""
        if cid and cid not in ev["_chunk_ids"]:
            ev["_chunk_ids"].append(cid)
        if did and did not in ev["_doc_ids"]:
            ev["_doc_ids"].append(did)
    return list(by_eid.values())


def rebuild_tlinks(graph: dict) -> list[dict]:
    """同 Chunk、有线索才连。无线索过程句已并进主 Event，不会自环。"""
    by_chunk: dict[str, list[dict]] = defaultdict(list)
    for r in graph.get("reports") or []:
        cid = r.get("chunk_id") or ""
        if cid and r.get("event_id"):
            by_chunk[cid].append(r)

    by_ev = {e["event_id"]: e for e in (graph.get("events") or [])}

    def _eday(eid: str) -> str:
        ev = by_ev.get(eid) or {}
        raw = (ev.get("event_time") or "")[:10]
        return raw if len(raw) == 10 else ""

    seen: set[tuple[str, str, str, str]] = set()
    edges: list[dict] = []
    for cid, reps in by_chunk.items():
        for i, cur in enumerate(reps):
            prev = reps[i - 1] if i else None
            if not prev:
                continue
            e_cur, e_prev = cur.get("event_id") or "", prev.get("event_id") or ""
            if not e_cur or not e_prev or e_cur == e_prev:
                continue
            order, cue = detect_tlink(cur, prev)
            if not order or not cue:
                continue
            # 「当天」是语篇线索，不能把两个已锚定的不同日历日硬拧成同一天
            if order == "SAME_DAY":
                d1, d2 = _eday(e_cur), _eday(e_prev)
                if d1 and d2 and d1 != d2:
                    continue
            key = (e_cur, e_prev, order, cue)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "type": "TLINK",
                    "from": e_cur,
                    "to": e_prev,
                    "order": order,
                    "cue": cue,
                    "scope": "chunk",
                    "chunk_id": cid,
                }
            )
    return edges


def rebuild_next(graph: dict, tlinks: list[dict]) -> list[dict]:
    annotated = _annotate(list(graph.get("events") or []), list(graph.get("reports") or []))
    skip = {(e["from"], e["to"]) for e in tlinks}
    return next_in_time_edges(
        annotated,
        max_delta_days=MAX_DELTA,
        skip_pairs=skip,
        skip_same_chunk=False,
        same_doc_fallback=0.2,
    )


def apply_temporal_edges(graph: dict) -> dict:
    tlinks = rebuild_tlinks(graph)
    nxt = rebuild_next(graph, tlinks)
    kept = [e for e in (graph.get("edges") or []) if e.get("type") in KEEP_EDGE]
    graph["edges"] = kept + tlinks + nxt
    graph["layer"] = "s6_tlink"
    return graph


def _has_pair(edges: list[dict], typ: str, a: str, b: str) -> bool:
    for e in edges:
        if e.get("type") != typ:
            continue
        fr, to = e.get("from") or "", e.get("to") or ""
        if (fr == a and to == b) or (fr == b and to == a):
            return True
    return False


def _find(graph: dict, *, pred: str = "", day: str = "", actor: str = "", loc: str = "") -> list[dict]:
    out = []
    for ev in graph.get("events") or []:
        if pred and pred not in (ev.get("predicate") or "") and pred not in (ev.get("trigger_surface") or ""):
            continue
        if day and (ev.get("event_time") or "")[:10] != day:
            continue
        blob = " ".join(
            [
                " ".join(ev.get("actors") or []),
                " ".join(a.get("canonical") or a.get("mention") or "" for a in (ev.get("actors_aligned") or [])),
                " ".join(ev.get("locations") or []),
                " ".join(a.get("canonical") or a.get("mention") or "" for a in (ev.get("locations_aligned") or [])),
            ]
        )
        if actor and actor not in blob:
            continue
        if loc and loc not in blob:
            continue
        out.append(ev)
    return out


def s6_checks(graph: dict, src_states: list[dict], src_eids: set[str]) -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    edges = graph.get("edges") or []
    tlinks = [e for e in edges if e.get("type") == "TLINK"]
    nxt = [e for e in edges if e.get("type") == "NEXT_IN_TIME"]
    causes = [e for e in edges if e.get("type") == "CAUSES"]

    disc = _find(graph, pred="发现", day="2026-08-21", actor="以军")
    hit = _find(graph, pred="实施", day="2026-08-21", actor="以军") or _find(
        graph, pred="打击", day="2026-08-21", actor="以军"
    )
    tlink_ok = False
    if disc and hit:
        tlink_ok = any(
            e.get("order") == "AFTER"
            and e.get("cue") == "随后"
            and (
                (e.get("from") == hit[0]["event_id"] and e.get("to") == disc[0]["event_id"])
                or (e.get("from") == disc[0]["event_id"] and e.get("to") == hit[0]["event_id"])
            )
            for e in tlinks
        )
    add("E15 TLINK 发现→随后打击", tlink_ok, f"n_tlink={len(tlinks)} disc={bool(disc)} hit={bool(hit)}")

    e1 = _find(graph, pred="发动", day="2026-08-18", actor="以色列") or _find(
        graph, pred="空袭", day="2026-08-18", actor="以色列"
    )
    e2 = _find(graph, pred="反驳", day="2026-08-19", actor="土耳其")
    next_ok = bool(e1 and e2 and _has_pair(nxt, "NEXT_IN_TIME", e1[0]["event_id"], e2[0]["event_id"]))
    add("E1–E2 NEXT_IN_TIME 无因果", next_ok and not causes, f"next={next_ok} causes={len(causes)}")

    add("不写 CAUSES", len(causes) == 0, f"n={len(causes)}")

    tlink_ok2 = all(e.get("scope") == "chunk" and e.get("cue") for e in tlinks)
    add("TLINK 皆同块且有 cue", tlink_ok2 and len(tlinks) >= 1, f"n={len(tlinks)}")

    self_loop = [e for e in tlinks if e.get("from") == e.get("to")]
    add("TLINK 无自环", len(self_loop) == 0, f"n={len(self_loop)}")

    e18 = _find(graph, pred="达成", day="2026-06-26")
    far = False
    if e18 and hit:
        far = _has_pair(nxt, "NEXT_IN_TIME", e18[0]["event_id"], hit[0]["event_id"])
    add("E18–E15 不因时间远串线", not far, f"linked={far}")

    cur_states = graph.get("states") or []
    src_key = [
        (s.get("state_id"), s.get("t_valid_from"), s.get("t_valid_to"), s.get("inducing_event_id"), s.get("ending_event_id"))
        for s in src_states
    ]
    cur_key = [
        (s.get("state_id"), s.get("t_valid_from"), s.get("t_valid_to"), s.get("inducing_event_id"), s.get("ending_event_id"))
        for s in cur_states
    ]
    add("STATE 区间未改", src_key == cur_key, f"n={len(cur_states)}")

    cur_eids = {e.get("event_id") for e in graph.get("events") or []}
    add("Event 主键未改", cur_eids == src_eids, f"n={len(cur_eids)}")

    over = [e for e in nxt if (e.get("delta_days") or 0) > MAX_DELTA]
    add(f"NEXT Δt≤{MAX_DELTA}", len(over) == 0, f"n_next={len(nxt)} over={len(over)}")

    n_ok = sum(1 for c in checks if c["ok"])
    return {"n": len(checks), "n_ok": n_ok, "pass": n_ok == len(checks), "checks": checks, "n_tlink": len(tlinks), "n_next": len(nxt)}


def neighbors(graph: dict, event_id: str) -> dict:
    prefix = event_id
    eids = [e.get("event_id") for e in graph.get("events") or [] if (e.get("event_id") or "").startswith(prefix)]
    if not eids:
        return {"event_id": event_id, "tlink": [], "next": []}
    eid = eids[0]
    tlink, nxt = [], []
    for e in graph.get("edges") or []:
        if eid not in (e.get("from"), e.get("to")):
            continue
        if e.get("type") == "TLINK":
            tlink.append(e)
        elif e.get("type") == "NEXT_IN_TIME":
            nxt.append(e)
    nxt.sort(key=lambda x: -(x.get("score") or x.get("weight") or 0))
    seen_n: set[tuple[str, str]] = set()
    uniq = []
    for e in nxt:
        pair = tuple(sorted([e.get("from") or "", e.get("to") or ""]))
        if pair in seen_n:
            continue
        seen_n.add(pair)
        uniq.append(e)
    return {"event_id": eid, "tlink": tlink, "next": uniq[:12]}


def timeline_rows(graph: dict, *, actor: str = "", day_from: str = "", day_to: str = "") -> list[dict]:
    rows = []
    tlinks = [e for e in (graph.get("edges") or []) if e.get("type") == "TLINK"]
    by_from = defaultdict(list)
    for e in tlinks:
        by_from[e.get("from")].append(e)
    for ev in graph.get("events") or []:
        day = (ev.get("event_time") or "")[:10]
        if day_from and (not day or day < day_from):
            continue
        if day_to and (not day or day > day_to):
            continue
        blob = " ".join(
            [
                " ".join(ev.get("actors") or []),
                " ".join(a.get("canonical") or "" for a in (ev.get("actors_aligned") or [])),
                ev.get("predicate") or "",
            ]
        )
        if actor and actor not in blob:
            continue
        cues = [f"{e.get('order')}:{e.get('cue')}→{(e.get('to') or '')[:12]}" for e in by_from.get(ev.get("event_id"), [])]
        rows.append(
            {
                "event_id": ev.get("event_id"),
                "kind": ev.get("kind"),
                "predicate": ev.get("predicate"),
                "event_time": ev.get("event_time"),
                "tlink": cues,
            }
        )
    rows.sort(key=lambda r: (r.get("event_time") or "", r.get("event_id") or ""))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="S6：TLINK + NEXT_IN_TIME（非因果）")
    p.add_argument("--in", dest="src", default=str(DATA / "graph" / "sitrep.s5.json"))
    p.add_argument("--out", default=str(DATA / "graph" / "sitrep.s6.json"))
    p.add_argument("--neighbors", dest="nbr", default="")
    p.add_argument("--actor", default="")
    p.add_argument("--from", dest="day_from", default="")
    p.add_argument("--to", dest="day_to", default="")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    if args.nbr or args.actor or args.day_from:
        path = DATA / "graph" / "sitrep.s6.json"
        srcp = Path(args.src)
        if srcp.name.endswith("s6.json") and srcp.is_file():
            path = srcp
        if not path.is_file():
            print("请先 python -m graph.s6")
            return 1
        g = json.loads(path.read_text(encoding="utf-8"))
        if args.nbr:
            print(json.dumps(neighbors(g, args.nbr), ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(timeline_rows(g, actor=args.actor, day_from=args.day_from, day_to=args.day_to), ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    if out.name in PROTECTED and not args.force:
        print(f"拒绝覆盖 {out}")
        return 2
    src = Path(args.src)
    if not src.is_file():
        print(f"找不到 {src}，请先 python -m graph.s5")
        return 1
    g = json.loads(src.read_text(encoding="utf-8"))
    src_states = [dict(s) for s in (g.get("states") or [])]
    src_eids = {e.get("event_id") for e in (g.get("events") or [])}
    apply_temporal_edges(g)
    path = save_graph(g, out)
    ev = s6_checks(g, src_states, src_eids)
    print(f"写入 {path}")
    print(f"TLINK {ev['n_tlink']}  NEXT_IN_TIME {ev['n_next']}  CAUSES 0")
    print(f"种子检查 {ev['n_ok']}/{ev['n']} pass={ev['pass']}")
    for c in ev["checks"]:
        print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")

    report = DATA / "reports" / "s6_tlink.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# S6 TLINK / NEXT_IN_TIME 验收",
        "",
        f"- 输入：`{src}`",
        f"- 输出：`{path}`",
        f"- TLINK **{ev['n_tlink']}** | NEXT_IN_TIME **{ev['n_next']}** | CAUSES **0**",
        "",
        f"检查 **{ev['n_ok']}/{ev['n']}**",
        "",
    ]
    for c in ev["checks"]:
        lines.append(f"- {'通过' if c['ok'] else '失败'} {c['name']} — {c['detail']}")
    lines += [
        "",
        "查询：`python -m graph.s6 --neighbors 896c6d24`",
        "时间线：`python -m graph.s6 --actor 以军 --from 2026-08-18 --to 2026-08-24`",
        "",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告 {report}")
    return 0 if ev["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
