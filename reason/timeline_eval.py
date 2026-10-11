"""时间线邻近金标：表态不混挂、地点线、战役切片。

python -m reason.timeline_eval --graph data/graph/sitrep.b1.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from graph.store import load_graph
from reason.timeline import (
    MAX_DELTA_DAYS,
    NEIGHBOR_K,
    ROOT,
    attach_evidence,
    attach_next,
    attach_tlinks,
    collect_aligned,
    collect_campaign,
    collect_place,
    resolve_actor,
    resolve_location_query,
    _event_has_place,
    _has_entity,
)

GOLD = ROOT / "data" / "eval" / "timeline_gold.json"


def _links(events: list[dict]) -> list[dict]:
    rows = []
    for ev in events:
        for nb in ev.get("_neighbors") or []:
            rows.append(
                {
                    "src_day": (ev.get("event_time") or "")[:10],
                    "src_title": ev.get("title") or "",
                    "src_trigger": ev.get("trigger") or "",
                    "nb_day": (nb.get("event_time") or "")[:10],
                    "nb_title": nb.get("title") or "",
                    "nb_trigger": nb.get("trigger") or "",
                }
            )
    return rows


def _prep_entity(graph: dict, name: str, kind: str, day_from: str, day_to: str) -> list[dict]:
    if kind == "location":
        eid, canon = resolve_location_query(graph, name)
        if not eid:
            return []
        in_win, unknown, _bg = collect_place(graph, eid, canon, name, day_from, day_to)
        pred = lambda members, _eid=eid, _c=canon, _n=name: any(
            _event_has_place(m, _eid, _c, _n) for m in members
        )
        events = in_win + unknown
        attach_evidence(graph, events)
        attach_tlinks(graph, events)
        attach_next(
            graph,
            events,
            entity_id=eid,
            k=NEIGHBOR_K,
            max_delta=MAX_DELTA_DAYS,
            query_kind=kind,
            frag_pred=pred,
        )
        return events
    eid, _canon = resolve_actor(graph, name)
    if not eid:
        return []
    in_win, unknown, _bg = collect_aligned(graph, eid, day_from, day_to, field="actors_aligned")
    events = in_win + unknown
    attach_evidence(graph, events)
    attach_tlinks(graph, events)
    attach_next(
        graph,
        events,
        entity_id=eid,
        k=NEIGHBOR_K,
        max_delta=MAX_DELTA_DAYS,
        query_kind=kind,
    )
    return events


def _prep_campaign(graph: dict, actor: str, location: str, day_from: str, day_to: str) -> list[dict]:
    aid, _a = resolve_actor(graph, actor)
    lid, lcanon = resolve_location_query(graph, location)
    if not aid:
        return []
    in_win, unknown, _bg = collect_campaign(
        graph, aid, lid, lcanon, location, day_from, day_to
    )
    events = in_win + unknown
    pred = lambda members: (
        any(_has_entity(m, aid) for m in members)
        and any(_event_has_place(m, lid, lcanon, location) for m in members)
    )
    attach_evidence(graph, events)
    attach_tlinks(graph, events)
    attach_next(
        graph,
        events,
        entity_id=aid,
        k=NEIGHBOR_K,
        max_delta=MAX_DELTA_DAYS,
        query_kind="campaign",
        frag_pred=pred,
        shared_ids=[aid, lid] if lid else [aid],
    )
    return events


def check_case(graph: dict, case: dict, day_from: str, day_to: str) -> list[str]:
    errs: list[str] = []
    cid = case["id"]
    if case["kind"] == "campaign":
        events = _prep_campaign(graph, case["actor"], case["location"], day_from, day_to)
        seeds = events
        links = _links(events)
        for needle in case.get("must_seed_title_substr") or []:
            if not any(needle in (e.get("title") or "") for e in seeds):
                errs.append(f"{cid}: 窗口内没有标题含「{needle}」")
        nblob = " ".join(r["nb_title"] for r in links)
        for needle in case.get("forbid_neighbor_title_substr") or []:
            if needle in nblob:
                errs.append(f"{cid}: 邻近标题含不应连的「{needle}」")
        return errs

    if case["kind"] == "tlink":
        events = _prep_entity(graph, case["name"], "actor", day_from, day_to)
        needle = case.get("seed_title_substr") or ""
        parent_t = case.get("parent_trigger") or ""
        child_t = case.get("child_trigger") or ""
        cue = case.get("cue") or ""
        day = case.get("same_day") or ""
        parents = [
            e
            for e in events
            if parent_t in (e.get("trigger") or "") and needle in (e.get("title") or "")
        ]
        if not parents:
            if case.get("if_missing") == "skip":
                print(f"SKIP {cid}: 本图无「{parent_t}」")
                return errs
            errs.append(f"{cid}: 没有 trigger 含「{parent_t}」的种子")
            return errs
        ok = False
        for ev in parents:
            if day and (ev.get("event_time") or "")[:10] != day:
                continue
            for fol in ev.get("_followers") or []:
                if child_t not in (fol.get("trigger") or ""):
                    continue
                if cue and cue not in (fol.get("_tlink") or ""):
                    continue
                if day and (fol.get("event_time") or "")[:10] != day:
                    continue
                ok = True
        if not ok:
            got = [
                f"{e.get('trigger')}|fol={[f.get('trigger') for f in (e.get('_followers') or [])]}"
                for e in parents
            ]
            errs.append(f"{cid}: 未看到 {parent_t} → 其后 {child_t}（cue={cue}，日={day}）；{got}")
        return errs

    events = _prep_entity(graph, case["name"], case["kind"], day_from, day_to)
    links = _links(events)
    needle = case.get("seed_title_substr") or ""
    scoped = [r for r in links if needle in r["src_title"]] if needle else links
    for trig in case.get("forbid_neighbor_triggers") or []:
        if any(trig in r["nb_trigger"] for r in links):
            errs.append(f"{cid}: 邻近含表态 trigger「{trig}」")
    for day in case.get("forbid_neighbor_days") or []:
        if any(r["nb_day"] == day for r in scoped):
            errs.append(f"{cid}: 邻近挂到 {day}")
    for day in case.get("must_link_days") or []:
        if not any(r["nb_day"] == day for r in scoped):
            errs.append(f"{cid}: 未挂到 {day}")
    for seed_day, need_days in (case.get("seed_day_must_link_days") or {}).items():
        got = {r["nb_day"] for r in links if r["src_day"] == seed_day}
        for d in need_days:
            if d not in got:
                errs.append(f"{cid}: {seed_day} 未挂到 {d}（得 {sorted(got)}）")
    return errs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="时间线邻近金标")
    p.add_argument("--graph", default=str(ROOT / "data" / "graph" / "sitrep.b1.json"))
    p.add_argument("--gold", default=str(GOLD))
    args = p.parse_args(argv)
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    graph = load_graph(Path(args.graph))
    day_from, day_to = gold["from"], gold["to"]
    errs: list[str] = []
    for case in gold["cases"]:
        errs.extend(check_case(graph, case, day_from, day_to))
    if errs:
        print("FAIL")
        for e in errs:
            print("-", e)
        return 1
    print(f"PASS {len(gold['cases'])} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
