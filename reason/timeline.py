"""弱时间线小切片：程序按 canonical + 时间窗列出事件，不编因果。

python -m reason.timeline --actors 以军,日本,日本防卫省 --from 2026-08-18 --to 2026-08-24
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from align.verify import resolve_actor, resolve_location
from extract.validate import evidence_sentence
from graph.proximity import _anchor_day, _entities, _jaccard
from graph.store import load_graph
from graph.time import overlaps_window

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MAX_DELTA_DAYS = 7
NEIGHBOR_K = 6


def _preview(text: str, n: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _pub_day(ev: dict) -> str:
    return (ev.get("publish_time") or "")[:10]


def collect_aligned(
    graph: dict,
    entity_id: str,
    day_from: str,
    day_to: str,
    *,
    field: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """窗口内 / 见报在窗内但日期不清 / 背景（早于见报 30 天）。"""
    in_win, unknown, background = [], [], []
    for ev in graph.get("events") or []:
        hits = [a for a in ev.get(field) or [] if a.get("id") == entity_id]
        if not hits:
            continue
        item = dict(ev)
        item["_mentions"] = [a.get("mention") for a in hits]
        if ev.get("time_background"):
            background.append(item)
            continue
        hit = overlaps_window(
            ev.get("event_time") or "",
            ev.get("time_precision") or "",
            day_from,
            day_to,
            ev.get("event_time_end") or "",
        )
        if hit is None:
            pub = _pub_day(ev)
            if (not day_from or pub >= day_from) and (not day_to or pub <= day_to):
                unknown.append(item)
            continue
        if hit:
            in_win.append(item)
    in_win.sort(key=lambda e: (e.get("event_time") or "", e.get("event_id") or ""))
    return in_win, unknown, background


def collect_actor(graph: dict, actor_id: str, day_from: str, day_to: str) -> tuple[list[dict], list[dict], list[dict]]:
    return collect_aligned(graph, actor_id, day_from, day_to, field="actors_aligned")


def collect_by_mention(
    graph: dict,
    name: str,
    day_from: str,
    day_to: str,
    *,
    slot: str = "actors",
) -> tuple[list[dict], list[dict], list[dict]]:
    """无对齐时按原文槽 / 标题 / 摘录子串召回。"""
    in_win, unknown, background = [], [], []
    for ev in graph.get("events") or []:
        blob = " ".join(
            [
                " ".join(ev.get(slot) or []),
                ev.get("title") or "",
                ev.get("evidence") or "",
                ev.get("trigger") or "",
            ]
        )
        if name not in blob:
            continue
        item = dict(ev)
        item["_mentions"] = [name]
        if ev.get("time_background"):
            background.append(item)
            continue
        hit = overlaps_window(
            ev.get("event_time") or "",
            ev.get("time_precision") or "",
            day_from,
            day_to,
            ev.get("event_time_end") or "",
        )
        if hit is None:
            pub = _pub_day(ev)
            if (not day_from or pub >= day_from) and (not day_to or pub <= day_to):
                unknown.append(item)
            continue
        if hit:
            in_win.append(item)
    in_win.sort(key=lambda e: (e.get("event_time") or "", e.get("event_id") or ""))
    return in_win, unknown, background


def attach_tlinks(graph: dict, events: list[dict]) -> None:
    """把同块 AFTER/SAME_DAY 后件挂到前件下（不编因果）。"""
    by_id = {e.get("event_id"): e for e in events}
    followers: dict[str, list[dict]] = defaultdict(list)
    for edge in graph.get("edges") or []:
        if edge.get("type") != "TLINK" or edge.get("order") not in ("AFTER", "SAME_DAY"):
            continue
        parent = by_id.get(edge.get("to"))
        child = by_id.get(edge.get("from"))
        if not parent or not child:
            continue
        hooked = dict(child)
        hooked["_tlink"] = f"{edge.get('order')} cue=`{edge.get('cue')}`"
        followers[parent["event_id"]].append(hooked)
    for ev in events:
        ev["_followers"] = followers.get(ev.get("event_id") or "") or []


def _shared_label(token: str) -> str:
    t = (token or "").strip()
    if t.startswith("actor:") or t.startswith("loc:"):
        return t.split(":", 1)[1]
    return t


STATEMENT_MARKERS = ("表示", "透露", "举行", "指出", "强调", "谴责", "警告", "声称", "宣称")
TRIGGER_PREFIXES = ("发动了", "发动", "实施了", "实施", "开展了", "开展", "进行了", "进行")


def _has_entity(ev: dict, entity_id: str) -> bool:
    if not entity_id:
        return False
    for slot in ("actors_aligned", "locations_aligned"):
        for a in ev.get(slot) or []:
            if (a.get("id") or "") == entity_id:
                return True
    return False


def _actor_ids(ev: dict) -> set[str]:
    return {str(a.get("id")) for a in (ev.get("actors_aligned") or []) if a.get("id")}


def _is_statement(ev: dict) -> bool:
    kind = (ev.get("kind") or "").strip()
    if kind == "表态":
        return True
    if kind in ("发生", "计划"):
        return False
    trig = ev.get("trigger") or ""
    if trig in ("称", "表示"):
        return True
    return any(m in trig for m in STATEMENT_MARKERS)


def _trigger_family(trig: str) -> str:
    t = (trig or "").strip()
    for p in TRIGGER_PREFIXES:
        if t.startswith(p) and len(t) > len(p):
            return t[len(p):]
    return t


def resolve_location_query(graph: dict, name: str) -> tuple[str, str]:
    """精确命中；否则用别名前缀（黎南 → 黎巴嫩南部）。"""
    eid, canon = resolve_location(graph, name)
    if eid:
        return eid, canon
    if len(name) < 2:
        return "", name
    for node in graph.get("locations") or []:
        aliases = [node.get("canonical") or ""] + [str(x) for x in (node.get("aliases") or [])]
        if any(a == name or a.startswith(name) or (name in a) for a in aliases if a):
            return node.get("id") or "", node.get("canonical") or name
    for ev in graph.get("events") or []:
        for a in ev.get("locations_aligned") or []:
            cname = a.get("canonical") or ""
            mention = a.get("mention") or ""
            if name in (cname, mention) or cname.startswith(name) or mention.startswith(name):
                return a.get("id") or "", cname or name
    return "", name


def _place_blob(ev: dict) -> str:
    parts = [str(x) for x in (ev.get("locations") or [])]
    for a in ev.get("locations_aligned") or []:
        parts += [a.get("mention") or "", a.get("canonical") or "", a.get("id") or ""]
    return " ".join(parts)


def _event_has_place(ev: dict, loc_id: str, loc_canon: str, loc_query: str) -> bool:
    if loc_id and _has_entity(ev, loc_id):
        return True
    blob = _place_blob(ev)
    if loc_canon and loc_canon in blob:
        return True
    if loc_query and len(loc_query) >= 2 and loc_query in blob:
        return True
    return False


def _fragment_key(ev: dict) -> tuple[str, str]:
    return (str(ev.get("doc_id") or ""), (ev.get("event_time") or "")[:10])


def _pick_rep(members: list[dict]) -> dict:
    """同篇同日优先用发生类代表，避免表态 trigger 更长把整组判成表态。"""
    return max(
        members,
        key=lambda e: (
            0 if _is_statement(e) else 1,
            len(e.get("trigger") or ""),
            len(e.get("evidence") or ""),
            e.get("event_id") or "",
        ),
    )


def _calendar_fragments(
    graph: dict,
    *,
    entity_id: str = "",
    pred=None,
) -> list[tuple[tuple[str, str], object, dict, list[dict]]]:
    """同篇+同日合并后再连邻近。pred 为战役/地点过滤。"""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ev in graph.get("events") or []:
        day = _anchor_day(ev)
        if day is None:
            continue
        if pred is None and entity_id and not _has_entity(ev, entity_id):
            continue
        buckets[_fragment_key(ev)].append(ev)
    out: list[tuple[tuple[str, str], object, dict, list[dict]]] = []
    for key, members in buckets.items():
        if pred is not None and not pred(members):
            continue
        dated = [m for m in members if _anchor_day(m) is not None]
        if not dated:
            continue
        rep = _pick_rep(dated)
        day = _anchor_day(rep)
        if day is None:
            continue
        out.append((key, day, rep, members))
    return out


def _neighbor_allowed(src: dict, rep: dict, *, query_kind: str) -> bool:
    """表态与发生不混挂。地点线：表态还须再共享一个 Actor。"""
    src_s, dst_s = _is_statement(src), _is_statement(rep)
    if src_s != dst_s:
        return False
    if query_kind == "location" and src_s and dst_s:
        return bool(_actor_ids(src) & _actor_ids(rep))
    return True


def attach_next(
    graph: dict,
    events: list[dict],
    *,
    entity_id: str = "",
    k: int = NEIGHBOR_K,
    max_delta: int = MAX_DELTA_DAYS,
    query_kind: str = "actor",
    frag_pred=None,
    shared_ids: list[str] | None = None,
) -> None:
    """查询时重算邻近：共享实体、Δt 硬切、同篇同日先合并。不读写图 top-6，不是因果。"""
    if not entity_id:
        for ev in events:
            ev["_neighbors"] = []
        return
    frags = _calendar_fragments(graph, entity_id=entity_id, pred=frag_pred)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ev in events:
        groups[_fragment_key(ev)].append(ev)
    lead = list(shared_ids or [entity_id])

    for group in groups.values():
        src = next(
            (e for e in group if _anchor_day(e) is not None and not _is_statement(e)),
            None,
        )
        if src is None:
            src = next((e for e in group if _anchor_day(e) is not None), None)
        if src is None:
            for ev in group:
                ev["_neighbors"] = []
            continue
        src_key = _fragment_key(src)
        src_day = _anchor_day(src)
        if src_day is None:
            for ev in group:
                ev["_neighbors"] = []
            continue
        tlink_ids = {f.get("event_id") for ev in group for f in (ev.get("_followers") or [])}
        chunk_ids = {ev.get("chunk_id") for ev in group if ev.get("chunk_id")}
        cands: list[tuple] = []
        for key, day, rep, members in frags:
            if key == src_key:
                continue
            if any(m.get("event_id") in tlink_ids for m in members):
                continue
            if chunk_ids & {m.get("chunk_id") for m in members if m.get("chunk_id")}:
                continue
            if not _neighbor_allowed(src, rep, query_kind=query_kind):
                continue
            dt = abs((src_day - day).days)
            if dt > max_delta:
                continue
            sim = _jaccard(_entities(src), _entities(rep))
            cands.append((dt, -sim, rep.get("event_id") or "", rep, members, day))
        cands.sort()
        neigh: list[dict] = []
        seen_fam: set[tuple] = set()
        for dt, neg_sim, _eid, rep, members, day in cands:
            fam = (str(day), _trigger_family(rep.get("trigger") or ""))
            if fam in seen_fam:
                continue
            seen_fam.add(fam)
            shared = [str(x) for x in sorted(_entities(src) & _entities(rep)) if x]
            ordered = [x for x in lead if x in shared or x == lead[0]]
            ordered += [x for x in shared if x not in ordered]
            hooked = dict(rep)
            hooked["_next"] = {
                "delta_days": dt,
                "weight": round(float(-neg_sim), 4),
                "shared": ordered[:4],
                "merged": len(members),
            }
            neigh.append(hooked)
            if len(neigh) >= k:
                break
        group[0]["_neighbors"] = neigh
        for ev in group[1:]:
            ev["_neighbors"] = []


def collect_campaign(
    graph: dict,
    actor_id: str,
    loc_id: str,
    loc_canon: str,
    loc_query: str,
    day_from: str,
    day_to: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """同篇同日只要有一条带地点，整组都进战役切片。"""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ev in graph.get("events") or []:
        if not _has_entity(ev, actor_id):
            continue
        item = dict(ev)
        item["_mentions"] = [
            a.get("mention") for a in (ev.get("actors_aligned") or []) if a.get("id") == actor_id
        ]
        buckets[_fragment_key(ev)].append(item)
    in_win, unknown, background = [], [], []
    for members in buckets.values():
        if not any(_event_has_place(m, loc_id, loc_canon, loc_query) for m in members):
            continue
        for item in members:
            if item.get("time_background"):
                background.append(item)
                continue
            hit = overlaps_window(
                item.get("event_time") or "",
                item.get("time_precision") or "",
                day_from,
                day_to,
                item.get("event_time_end") or "",
            )
            if hit is None:
                pub = _pub_day(item)
                if (not day_from or pub >= day_from) and (not day_to or pub <= day_to):
                    unknown.append(item)
                continue
            if hit:
                in_win.append(item)
    in_win.sort(key=lambda e: (e.get("event_time") or "", e.get("event_id") or ""))
    return in_win, unknown, background


def collect_place(
    graph: dict,
    loc_id: str,
    loc_canon: str,
    loc_query: str,
    day_from: str,
    day_to: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """地点用规范名或原文子串；同篇同日有一条即可。"""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ev in graph.get("events") or []:
        buckets[_fragment_key(ev)].append(ev)
    in_win, unknown, background = [], [], []
    for members in buckets.values():
        if not any(_event_has_place(m, loc_id, loc_canon, loc_query) for m in members):
            continue
        for item0 in members:
            item = dict(item0)
            hits = [
                a.get("mention")
                for a in (item.get("locations_aligned") or [])
                if (loc_id and a.get("id") == loc_id)
                or (loc_canon and loc_canon in (a.get("mention") or ""))
                or (loc_query and loc_query in (a.get("mention") or ""))
            ]
            item["_mentions"] = hits or [loc_query or loc_canon]
            if item.get("time_background"):
                background.append(item)
                continue
            hit = overlaps_window(
                item.get("event_time") or "",
                item.get("time_precision") or "",
                day_from,
                day_to,
                item.get("event_time_end") or "",
            )
            if hit is None:
                pub = _pub_day(item)
                if (not day_from or pub >= day_from) and (not day_to or pub <= day_to):
                    unknown.append(item)
                continue
            if hit:
                in_win.append(item)
    in_win.sort(key=lambda e: (e.get("event_time") or "", e.get("event_id") or ""))
    return in_win, unknown, background


def attach_evidence(graph: dict, events: list[dict]) -> None:
    chunks = {c["chunk_id"]: c for c in graph.get("chunks") or []}
    for ev in events:
        if ev.get("evidence"):
            continue
        ch = chunks.get(ev.get("chunk_id") or "") or {}
        ev["evidence"] = evidence_sentence(ch.get("text") or "", ev.get("trigger") or "")


def collapse(events: list[dict], actor_id: str) -> list[tuple[tuple, list[dict]]]:
    """同 chunk+trigger 去重；同篇+同日收成一张卡片。"""
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for ev in events:
        k = (ev.get("chunk_id"), ev.get("trigger"), actor_id)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(ev)
    buckets: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for ev in uniq:
        key = (ev.get("doc_id"), ev.get("event_time") or "日期不清", ev.get("title") or "")
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(ev)
    return [(k, buckets[k]) for k in order]


def render_cards(cards: list[tuple[tuple, list[dict]]]) -> list[str]:
    lines: list[str] = []
    by_day: dict[str, list[tuple[tuple, list[dict]]]] = {}
    for key, evs in cards:
        by_day.setdefault(key[1], []).append((key, evs))
    for day in sorted(by_day, key=lambda d: (d == "日期不清", d)):
        n = sum(len(evs) for _, evs in by_day[day])
        lines.append(f"### {day}（{len(by_day[day])} 篇 / {n} 个 trigger）")
        lines.append("")
        for key, evs in by_day[day]:
            title = key[2]
            lines.append(f"**{title}**")
            shown_nb: set[str] = set()
            for ev in evs:
                ms = "、".join(ev.get("_mentions") or [])
                raw = ev.get("raw_time") or ""
                prec = ev.get("time_precision") or ""
                extra = f" raw=`{raw}`" if raw else ""
                if prec and prec not in ("day",):
                    extra += f" precision=`{prec}`"
                cite = f"[{(ev.get('event_id') or '')[:8]}|{ev.get('chunk_id')}]"
                lines.append(f"- {cite} `{ev.get('trigger')}` mention=`{ms}`{extra}")
                evd = ev.get("evidence") or ""
                if evd:
                    lines.append(f"  - {_preview(evd)}")
                for fol in ev.get("_followers") or []:
                    cue = fol.get("_tlink") or ""
                    extra = f" {cue}" if cue else ""
                    fc = f"[{(fol.get('event_id') or '')[:8]}|{fol.get('chunk_id')}]"
                    lines.append(f"  - 其后 {fc} `{fol.get('trigger')}`{extra}")
                    if fol.get("evidence"):
                        lines.append(f"    - {_preview(fol.get('evidence') or '')}")
                for nb in ev.get("_neighbors") or []:
                    nid = nb.get("event_id") or ""
                    if nid and nid in shown_nb:
                        continue
                    meta = nb.get("_next") or {}
                    shared = "、".join(_shared_label(x) for x in (meta.get("shared") or []) if x) or "实体"
                    dt = meta.get("delta_days")
                    dt_s = f"{dt} 天" if dt is not None else "日期近"
                    nb_day = (nb.get("event_time") or "")[:10] or "日期不清"
                    nc = f"[{nid[:8]}|{nb.get('chunk_id')}]"
                    merged = meta.get("merged") or 1
                    extra_m = f" 同篇同日已合并 {merged} 个 trigger" if merged > 1 else ""
                    lines.append(
                        f"  - 邻近（共享 {shared}，间隔 {dt_s}）{nc} `{nb.get('trigger')}` `{nb_day}`{extra_m}"
                    )
                    if nid:
                        shown_nb.add(nid)
            lines.append("")
    return lines


def events_exact_slot(graph: dict, mention: str, day_from: str, day_to: str, *, slot: str) -> list[dict]:
    out = []
    for ev in graph.get("events") or []:
        day = ev.get("event_time") or ""
        if not day:
            continue
        if day_from and day < day_from:
            continue
        if day_to and day > day_to:
            continue
        if mention in (ev.get(slot) or []):
            out.append(ev)
    return out


def render_entity(
    graph: dict,
    name: str,
    day_from: str,
    day_to: str,
    *,
    kind: str,
    max_delta: int = MAX_DELTA_DAYS,
    neighbor_k: int = NEIGHBOR_K,
) -> str:
    if kind == "location":
        eid, canon = resolve_location_query(graph, name)
        field, slot, missing = "locations_aligned", "locations", "图上没有该地点。"
    else:
        eid, canon = resolve_actor(graph, name)
        field, slot, missing = "actors_aligned", "actors", "图上没有该主体。"
    lines = [f"## {name} → **{canon}** `{eid or '未命中'}`", ""]
    place_pred = None
    if kind == "location" and eid:
        in_win, unknown, background = collect_place(graph, eid, canon, name, day_from, day_to)
        place_pred = lambda members, _eid=eid, _c=canon, _n=name: any(
            _event_has_place(m, _eid, _c, _n) for m in members
        )
    elif eid:
        in_win, unknown, background = collect_aligned(graph, eid, day_from, day_to, field=field)
    else:
        in_win, unknown, background = collect_by_mention(graph, name, day_from, day_to, slot=slot)
        if not in_win and not unknown:
            return "\n".join(lines + [missing, ""])
        lines.append("- 本图无对齐层，按原文 mention 召回。")
        lines.append("")
    attach_evidence(graph, in_win + unknown)
    attach_tlinks(graph, in_win + unknown)
    attach_next(
        graph,
        in_win + unknown,
        entity_id=eid or "",
        k=neighbor_k,
        max_delta=max_delta,
        query_kind=kind,
        frag_pred=place_pred,
    )
    n_next = sum(len(e.get("_neighbors") or []) for e in in_win + unknown)
    exact = events_exact_slot(graph, name, day_from, day_to, slot=slot)
    cards = collapse(in_win, eid)
    unk_cards = collapse(unknown, eid)
    mentions = Counter(m for e in in_win for m in (e.get("_mentions") or []))
    lines += [
        f"- 窗口内 OpenIE 事件 **{len(in_win)}** → 折叠 **{len(cards)}** 张卡片（只搜原文「{name}」{len(exact)} 条）",
        f"- 见报在窗口内、日期不清 **{len(unknown)}** 条 → {len(unk_cards)} 张（不进按日线）",
        f"- 背景（早于见报 ≥30 天，未列入）**{len(background)}** 条",
        f"- 查询重算邻近（共享实体，间隔 ≤{max_delta} 天，表态/发生不混挂，同 trigger 族每日 1 条）**{n_next}** 条，不是因果",
        "",
    ]
    if mentions:
        dist = "、".join(f"{m}({n})" for m, n in mentions.most_common())
        lines.append(f"- 窗口内 mention：{dist}")
        lines.append("")
    lines += render_cards(cards)
    if unk_cards:
        lines += render_cards(unk_cards)
    return "\n".join(lines)


def render_campaign(
    graph: dict,
    actor_name: str,
    loc_name: str,
    day_from: str,
    day_to: str,
    *,
    max_delta: int = MAX_DELTA_DAYS,
    neighbor_k: int = NEIGHBOR_K,
) -> str:
    aid, acanon = resolve_actor(graph, actor_name)
    lid, lcanon = resolve_location_query(graph, loc_name)
    lines = [
        f"## 战役 {actor_name} × {loc_name} → **{acanon}** `{aid or '未命中'}` × **{lcanon}** `{lid or '未命中'}`",
        "",
    ]
    if not aid:
        return "\n".join(lines + ["图上没有该主体。", ""])
    in_win, unknown, background = collect_campaign(
        graph, aid, lid, lcanon, loc_name, day_from, day_to
    )
    if not in_win and not unknown:
        return ""
    pred = lambda members: (
        any(_has_entity(m, aid) for m in members)
        and any(_event_has_place(m, lid, lcanon, loc_name) for m in members)
    )
    attach_evidence(graph, in_win + unknown)
    attach_tlinks(graph, in_win + unknown)
    attach_next(
        graph,
        in_win + unknown,
        entity_id=aid,
        k=neighbor_k,
        max_delta=max_delta,
        query_kind="campaign",
        frag_pred=pred,
        shared_ids=[aid, lid] if lid else [aid],
    )
    n_next = sum(len(e.get("_neighbors") or []) for e in in_win + unknown)
    cards = collapse(in_win, aid)
    unk_cards = collapse(unknown, aid)
    lines += [
        "- 须同时命中主体与地点（地点可用原文子串，同篇同日有一条即可）",
        f"- 窗口内 **{len(in_win)}** 条 → 折叠 **{len(cards)}** 张；日期不清 **{len(unknown)}**；背景 **{len(background)}**",
        f"- 邻近 **{n_next}** 条（战役切片，不是因果）",
        "",
    ]
    lines += render_cards(cards)
    if unk_cards:
        lines += render_cards(unk_cards)
    return "\n".join(lines)


def render_actor(graph: dict, name: str, day_from: str, day_to: str) -> str:
    return render_entity(graph, name, day_from, day_to, kind="actor")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="弱时间线小切片（查询重算邻近，不写因果）")
    p.add_argument("--graph", default=str(DATA / "graph" / "sitrep.json"))
    p.add_argument("--actors", default="")
    p.add_argument("--locations", default="")
    p.add_argument("--from", dest="day_from", default="2026-08-18")
    p.add_argument("--to", dest="day_to", default="2026-08-24")
    p.add_argument("--max-delta", type=int, default=MAX_DELTA_DAYS, help="邻近硬切天数")
    p.add_argument("--neighbor-k", type=int, default=NEIGHBOR_K, help="每张卡片最多邻近条数")
    p.add_argument("--out", default=str(DATA / "reports" / "timeline_slice.md"))
    args = p.parse_args(argv)
    graph = load_graph(Path(args.graph))
    actors = [x.strip() for x in args.actors.split(",") if x.strip()]
    locations = [x.strip() for x in args.locations.split(",") if x.strip()]
    if not actors and not locations:
        actors = ["以军", "日本", "日本防卫省"]
    parts = [
        "# 弱时间线小切片（折叠 + 句级摘录 + 相对时间）",
        "",
        f"窗口 `{args.day_from}`～`{args.day_to}`。同一篇同一日收成一张卡片；摘录为 trigger 所在句。",
        f"邻近在查询时重算：共享查询实体、间隔 ≤{args.max_delta} 天、同篇同日先合并；表态与发生不混挂。不读写图 NEXT top-6。都不写成「导致」。",
        f"主体：{'、'.join(actors) or '（无）'}；地点：{'、'.join(locations) or '（无）'}",
        "",
    ]
    if actors and locations:
        for an in actors:
            for ln in locations:
                block = render_campaign(
                    graph,
                    an,
                    ln,
                    args.day_from,
                    args.day_to,
                    max_delta=args.max_delta,
                    neighbor_k=args.neighbor_k,
                )
                if block:
                    parts.append(block)
    for name in actors:
        parts.append(
            render_entity(
                graph,
                name,
                args.day_from,
                args.day_to,
                kind="actor",
                max_delta=args.max_delta,
                neighbor_k=args.neighbor_k,
            )
        )
    for name in locations:
        parts.append(
            render_entity(
                graph,
                name,
                args.day_from,
                args.day_to,
                kind="location",
                max_delta=args.max_delta,
                neighbor_k=args.neighbor_k,
            )
        )
    text = "\n".join(parts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
