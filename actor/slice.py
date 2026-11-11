"""查询窗 ActorSlice：排行、共现、稿列表。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
import calendar
from datetime import date, timedelta
from pathlib import Path

from actor.config import (
    ACTOR_DIR,
    ARTS_CAP,
    DEFAULT_FROM,
    DEFAULT_TO,
    GRAPH_TOP,
    RANK_TOP,
    REL_EDGE_CAP,
    REPORT_DIR,
    TYPES,
    WEB_DIR,
)
from actor.harvest import load_mentions
from actor.link import link, write_link
from actor.person import STATE_FIELDS, build_person_states, elements_from_mentions
from actor.relations import KIND_ZH, belong_country, classify_trigger


def equipment_by_event() -> dict[tuple, list[str]]:
    """事件里的装备原文。收提及时被滤掉的型号也要留在关系上。"""
    cached = getattr(equipment_by_event, "_cache", None)
    if cached is not None:
        return cached
    from actor.harvest import default_extract_paths, iter_extract

    out: dict[tuple, list[str]] = {}
    for path in default_extract_paths():
        if not path.is_file():
            continue
        for rec in iter_extract(path):
            doc_id = str(rec.get("doc_id") or "")
            for ci, ch in enumerate(rec.get("chunks") or []):
                for ei, ev in enumerate(ch.get("events") or []):
                    names: list[str] = []
                    for arg in ev.get("arguments") or []:
                        if not isinstance(arg, dict) or arg.get("role") != "装备":
                            continue
                        text = str(arg.get("text") or "").strip()
                        if text and text not in names:
                            names.append(text)
                    if names:
                        out[(doc_id, ci, ei)] = names
    equipment_by_event._cache = out
    return out


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _days(a: str, b: str) -> list[str]:
    cur, end = _d(a), _d(b)
    out = []
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def slice_key(window: dict) -> str:
    a, b = window["from"][:10], window["to"][:10]
    if a == b:
        return a
    am, bm = a[:7], b[:7]
    if am == bm:
        last = calendar.monthrange(int(a[:4]), int(a[5:7]))[1]
        if a.endswith("-01") and b == f"{am}-{last:02d}":
            return am
        return f"{a}~{b}"
    last_b = calendar.monthrange(int(b[:4]), int(b[5:7]))[1]
    if a.endswith("-01") and b == f"{bm}-{last_b:02d}":
        return f"{am}~{bm}"
    return f"{a}~{b}"


def _profile_as_of(state: dict, day_to: str) -> dict:
    current = {field: "" for field in STATE_FIELDS}
    as_of = {field: "" for field in STATE_FIELDS}
    for rev in state.get("revisions") or []:
        day = str(rev.get("day") or "")
        if day_to and day > day_to:
            continue
        field = str(rev.get("field") or "")
        if field not in current:
            continue
        current[field] = str(rev.get("value") or "")
        as_of[field] = day
    return {
        "post": current["post"],
        "post_as_of": as_of["post"],
        "nationality": current["nationality"],
        "nationality_as_of": as_of["nationality"],
        "org": current["org"],
        "org_as_of": as_of["org"],
    }


def _person_views(
    mentions: list[dict],
    *,
    ta: str,
    tb: str,
    elements: list[dict] | None,
    states: list[dict] | None,
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    if elements is None:
        elements = elements_from_mentions(mentions)
    if states is None:
        states = build_person_states(elements)
    state_of = {s["name"]: s for s in states}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in elements:
        day = str(row.get("day") or "")
        if ta <= day <= tb:
            by_name[str(row.get("name") or "")].append(row)
    return state_of, by_name


def _attach_person(ent: dict, state_of: dict[str, dict], by_name: dict[str, list[dict]], tb: str) -> None:
    if ent.get("type") != "Person":
        return
    names = [ent["name"], *(ent.get("aliases") or [])]
    state = next((state_of[n] for n in names if n in state_of), None)
    if state:
        ent["profile"] = _profile_as_of(state, tb)
    rows = []
    seen = set()
    for name in names:
        for row in by_name.get(name) or []:
            key = (
                row.get("day"),
                row.get("role"),
                row.get("action"),
                row.get("post"),
                row.get("counterpart"),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("day") or ""), reverse=True)
    ent["person_elements"] = [
        {
            "day": r.get("day") or "",
            "role": r.get("role") or "",
            "action": r.get("action") or "",
            "kind": r.get("kind") or "",
            "event_time": r.get("event_time") or "",
            "place": r.get("place") or "",
            "counterpart": r.get("counterpart") or "",
            "post": r.get("post") or "",
            "nationality": r.get("nationality") or "",
            "org": r.get("org") or "",
        }
        for r in rows[:8]
    ]


def build_slice(
    *,
    ta: str = DEFAULT_FROM,
    tb: str = DEFAULT_TO,
    mentions: list[dict] | None = None,
    use_llm: bool = False,
    elements: list[dict] | None = None,
    states: list[dict] | None = None,
) -> dict:
    if mentions is None:
        path = ACTOR_DIR / "mentions.jsonl"
        if not path.is_file():
            from actor.harvest import harvest, write_mentions

            write_mentions(harvest())
        mentions = load_mentions()
        if mentions and "entity_id" not in mentions[0]:
            packed = link(mentions, use_llm=use_llm)
            write_link(packed)
            mentions = packed["mentions"]
    win = [m for m in mentions if m.get("entity_id") and ta <= (m.get("day") or "") <= tb]
    by_ent: dict[str, dict] = {}
    for m in win:
        rec = by_ent.setdefault(
            m["entity_id"],
            {
                "id": m["entity_id"],
                "name": m["entity_name"],
                "type": m["entity_type"] or "Org",
                "docs": {},
                "n_source": 0,
                "n_target": 0,
                "n_equip": 0,
                "aliases": set(),
            },
        )
        rec["aliases"].add(m["surface"])
        doc = rec["docs"].setdefault(
            m["doc_id"],
            {
                "doc_id": m["doc_id"],
                "day": m["day"],
                "title": m["title"],
                "url": m["url"],
                "source_name": m.get("source_name") or m.get("source") or "",
            },
        )
        if m["role"] == "主体":
            rec["n_source"] += 1
        elif m["role"] == "对象":
            rec["n_target"] += 1
        elif m["role"] == "装备":
            rec["n_equip"] += 1
        doc.setdefault("roles", Counter())[m["role"]] += 1

    entities = []
    for rec in by_ent.values():
        arts = sorted(rec["docs"].values(), key=lambda x: (x["day"], x["title"]))
        media_n = Counter(a.get("source_name") or "未知" for a in arts)
        entities.append(
            {
                "id": rec["id"],
                "name": rec["name"],
                "type": rec["type"],
                "mass": len(rec["docs"]),
                "n_mentions": rec["n_source"] + rec["n_target"] + rec["n_equip"],
                "n_source": rec["n_source"],
                "n_target": rec["n_target"],
                "aliases": sorted(a for a in rec["aliases"] if a != rec["name"]),
                "articles": [
                    {
                        "doc_id": a["doc_id"],
                        "day": a["day"],
                        "title": a["title"],
                        "url": a["url"],
                        "source_name": a.get("source_name") or "",
                    }
                    for a in arts[:ARTS_CAP]
                ],
                "n_articles": len(arts),
                "media": [{"name": k, "n": v} for k, v in media_n.most_common()],
            }
        )
    entities.sort(key=lambda e: (-e["mass"], e["name"]))
    state_of, elem_by_name = _person_views(mentions, ta=ta, tb=tb, elements=elements, states=states)
    for ent in entities:
        _attach_person(ent, state_of, elem_by_name, tb)

    id_of = {e["id"]: e for e in entities}
    name_of = {e["name"]: e["id"] for e in entities}
    bag: dict[tuple, dict] = {}
    ev_map: dict[tuple, dict] = defaultdict(
        lambda: {
            "src": set(),
            "tgt": set(),
            "trig": "",
            "ekind": "",
            "doc": "",
            "title": "",
            "evidence": "",
            "day": "",
            "source_name": "",
            "url": "",
            "equips": set(),
            "equip_names": [],
        }
    )
    for m in win:
        if (m.get("via") or "extract") != "extract":
            continue
        if m["role"] not in ("主体", "对象", "装备"):
            continue
        key = (m["doc_id"], m["chunk_i"], m["event_i"])
        rec = ev_map[key]
        rec["trig"] = m.get("trigger") or rec["trig"]
        rec["ekind"] = m.get("kind") or rec["ekind"]
        rec["doc"] = m["doc_id"]
        rec["title"] = m.get("title") or rec["title"]
        rec["evidence"] = m.get("evidence") or rec.get("evidence") or ""
        rec["day"] = m.get("day") or rec["day"]
        rec["source_name"] = m.get("source_name") or rec["source_name"]
        rec["url"] = m.get("url") or rec.get("url") or ""
        place = (m.get("place") or "").strip()
        if place:
            got = rec.setdefault("places", [])
            for p in place.split("、"):
                p = p.strip()
                if p and p not in got:
                    got.append(p)
        for name in equipment_by_event().get((m["doc_id"], m.get("chunk_i"), m.get("event_i")), []):
            if name not in rec["equip_names"]:
                rec["equip_names"].append(name)
        if m["role"] == "装备":
            rec["equips"].add(m["entity_id"])
            name = m.get("entity_name") or m.get("surface") or ""
            if name and name not in rec["equip_names"]:
                rec["equip_names"].append(name)
            continue
        if m["role"] == "主体":
            rec["src"].add(m["entity_id"])
        else:
            rec["tgt"].add(m["entity_id"])
    def _add_edge(a: str, b: str, kind: str, rec: dict, payload: str = "", places: list | None = None) -> None:
        if a == b or a not in id_of or b not in id_of:
            return
        slot = bag.setdefault(
            (a, b, kind),
            {
                "source": a,
                "target": b,
                "kind": kind,
                "label": KIND_ZH[kind],
                "triggers": Counter(),
                "docs": set(),
                "days": set(),
                "examples": [],
                "payloads": Counter(),
                "places": Counter(),
            },
        )
        slot["triggers"][rec["trig"] or KIND_ZH[kind]] += 1
        if payload:
            slot["payloads"][payload] += 1
        for p in places or []:
            if p:
                slot["places"][p] += 1
        slot["docs"].add(rec["doc"])
        if rec["day"]:
            slot["days"].add(rec["day"])
        titles = {x.get("title") for x in slot["examples"]}
        if rec["title"] and rec["title"] not in titles:
            slot["examples"].append(
                {
                    "day": rec["day"],
                    "title": rec["title"],
                    "url": rec.get("url") or "",
                    "source_name": rec.get("source_name") or "",
                    "payload": payload,
                }
            )

    for rec in ev_map.values():
        kind = classify_trigger(rec["trig"], rec.get("ekind") or "", rec.get("evidence") or "")
        if not kind:
            continue
        payload = "、".join(rec["equip_names"])
        if kind == "launches":
            # 发射的是装备，不是另一个国家。射向的国家改记为打击，并带上武器名。
            if rec["equips"]:
                for a in rec["src"]:
                    for b in rec["equips"]:
                        _add_edge(a, b, "launches", rec, payload)
            for a in rec["src"]:
                for b in rec["tgt"]:
                    tgt = id_of.get(b)
                    if not tgt or tgt["type"] == "Country":
                        if tgt and tgt["type"] == "Country":
                            _add_edge(a, b, "strikes", rec, payload)
                        continue
                    if b not in rec["equips"]:
                        _add_edge(a, b, "launches", rec, payload)
            continue
        if kind == "deploys":
            # 部署的是装备或部队。有装备时，国家是去向，不是被部署的对象。
            has_gear = bool(rec["equips"] or rec["equip_names"])
            places = list(rec.get("places") or [])
            for b in rec["tgt"]:
                tgt = id_of.get(b)
                if tgt and tgt["type"] in ("Country", "Location") and tgt["name"] not in places:
                    places.append(tgt["name"])
            where = "、".join(places)
            shown = " · ".join(x for x in (payload, where) if x)
            if rec["equips"]:
                for a in rec["src"]:
                    for b in rec["equips"]:
                        _add_edge(a, b, "deploys", rec, shown, places)
            for a in rec["src"]:
                for b in rec["tgt"]:
                    tgt = id_of.get(b)
                    if not tgt or b in rec["equips"]:
                        continue
                    if has_gear and tgt["type"] in ("Country", "Location"):
                        continue
                    _add_edge(a, b, "deploys", rec, shown if has_gear else payload, places if has_gear else None)
            continue
        if kind == "signs":
            # 签署的是文件，不把两个国家连成签署。
            for a in rec["src"]:
                for b in rec["tgt"]:
                    src = id_of.get(a)
                    tgt = id_of.get(b)
                    if not src or not tgt:
                        continue
                    if src["type"] == "Country" and tgt["type"] == "Country":
                        continue
                    if tgt["type"] == "Country":
                        continue
                    _add_edge(a, b, "signs", rec, tgt["name"])
            continue
        for a in rec["src"]:
            for b in rec["tgt"]:
                _add_edge(a, b, kind, rec, payload)

    for ent in entities:
        host = belong_country(ent["name"], ent["type"])
        hid = name_of.get(host or "")
        if not hid or hid == ent["id"]:
            continue
        slot = bag.setdefault(
            (ent["id"], hid, "belongs_to"),
            {
                "source": ent["id"],
                "target": hid,
                "kind": "belongs_to",
                "label": "从属",
                "triggers": Counter({"隶属": 1}),
                "docs": set(),
                "days": set(),
                "examples": [],
            },
        )
        slot["docs"].update(a["doc_id"] for a in ent["articles"][:12])
        slot["days"].update(a["day"] for a in ent["articles"][:12] if a.get("day"))
        titles = {x.get("title") for x in slot["examples"]}
        for art in ent["articles"][:4]:
            if art.get("title") and art["title"] not in titles:
                titles.add(art["title"])
                slot["examples"].append(
                    {
                        "day": art.get("day") or "",
                        "title": art["title"],
                        "url": art.get("url") or "",
                        "source_name": art.get("source_name") or "",
                        "payload": "",
                    }
                )

    edges = []
    deg: Counter = Counter()
    for slot in bag.values():
        n_docs = max(len(slot["docs"]), 1)
        trig = slot["triggers"].most_common(1)[0][0]
        days = sorted(d for d in slot["days"] if d)
        payloads = slot.get("payloads") or Counter()
        payload = payloads.most_common(1)[0][0] if payloads else ""
        if slot["kind"] == "deploys":
            extra = [p for p, _n in (slot.get("places") or Counter()).most_common(4) if p and p not in payload]
            if extra:
                payload = " · ".join(x for x in (payload, "、".join(extra)) if x)
        edges.append(
            {
                "source": slot["source"],
                "target": slot["target"],
                "kind": slot["kind"],
                "label": slot["label"],
                "trigger": trig,
                "payload": payload,
                "n_docs": n_docs,
                "weight": n_docs,
                "first_day": days[0] if days else "",
                "last_day": days[-1] if days else "",
                "examples": slot["examples"][:4],
            }
        )
        deg[slot["source"]] += 1
        deg[slot["target"]] += 1
    edges.sort(key=lambda x: (x["kind"] == "belongs_to", -x["weight"], x["source"]))
    for e in entities:
        e["degree"] = int(deg.get(e["id"], 0))

    picked: list[dict] = []
    node_ids: set[str] = set()
    for edge in edges:
        grow = {edge["source"], edge["target"]} - node_ids
        if grow and len(node_ids) + len(grow) > GRAPH_TOP:
            if edge["source"] in node_ids and edge["target"] in node_ids:
                picked.append(edge)
            continue
        picked.append(edge)
        node_ids.update((edge["source"], edge["target"]))
        if len(picked) >= REL_EDGE_CAP:
            break
    graph_edges = picked[:REL_EDGE_CAP]
    node_ids = {e["source"] for e in graph_edges} | {e["target"] for e in graph_edges}
    have = {(e["source"], e["target"], e["kind"]) for e in graph_edges}
    for edge in edges:
        if edge["kind"] != "belongs_to":
            continue
        key = (edge["source"], edge["target"], edge["kind"])
        if key in have:
            continue
        if edge["source"] in node_ids:
            graph_edges.append(edge)
            node_ids.add(edge["target"])
            have.add(key)
    # 国家不因节点上限被丢掉。国家之间的边全部保留；还没进图的国家再补一条最重的边。
    id_type = {e["id"]: e["type"] for e in entities}
    country_ids = {i for i, t in id_type.items() if t == "Country"}
    for edge in edges:
        if id_type.get(edge["source"]) != "Country" or id_type.get(edge["target"]) != "Country":
            continue
        key = (edge["source"], edge["target"], edge["kind"])
        if key in have:
            continue
        graph_edges.append(edge)
        node_ids.add(edge["source"])
        node_ids.add(edge["target"])
        have.add(key)
    for edge in edges:
        fresh = [i for i in (edge["source"], edge["target"]) if i in country_ids and i not in node_ids]
        if not fresh:
            continue
        key = (edge["source"], edge["target"], edge["kind"])
        if key in have:
            node_ids.update(fresh)
            continue
        graph_edges.append(edge)
        node_ids.add(edge["source"])
        node_ids.add(edge["target"])
        have.add(key)
    # 已经在图上的国家，部署到某地的装备也留下，去向在 payload 里。
    for edge in edges:
        if edge["kind"] != "deploys" or " · " not in (edge.get("payload") or ""):
            continue
        if id_type.get(edge["source"]) != "Country" or edge["source"] not in node_ids:
            continue
        if id_type.get(edge["target"]) != "MilitaryEntity":
            continue
        key = (edge["source"], edge["target"], edge["kind"])
        if key in have:
            continue
        graph_edges.append(edge)
        node_ids.add(edge["target"])
        have.add(key)
    graph_nodes = []
    for ent in entities:
        if ent["id"] not in node_ids:
            continue
        node = {k: ent[k] for k in ("id", "name", "type", "mass", "degree", "n_source", "n_target")}
        node["articles"] = ent["articles"][:ARTS_CAP]
        node["aliases"] = ent.get("aliases") or []
        node["media"] = ent.get("media") or []
        if ent.get("profile"):
            node["profile"] = ent["profile"]
        if ent.get("person_elements"):
            node["person_elements"] = ent["person_elements"]
        graph_nodes.append(node)
    kind_counts = Counter(e["kind"] for e in graph_edges)

    ranks = {}
    for typ in TYPES:
        ranks[typ] = []
        for e in entities:
            if e["type"] != typ:
                continue
            row = {
                "id": e["id"],
                "name": e["name"],
                "type": e["type"],
                "mass": e["mass"],
                "degree": e["degree"],
                "n_source": e["n_source"],
                "n_target": e["n_target"],
                "articles": e["articles"][:ARTS_CAP],
                "aliases": e.get("aliases") or [],
                "media": e.get("media") or [],
            }
            if e.get("profile"):
                row["profile"] = e["profile"]
            if e.get("person_elements"):
                row["person_elements"] = e["person_elements"]
            ranks[typ].append(row)
            if len(ranks[typ]) >= RANK_TOP:
                break

    by_doc = {
        m["doc_id"]: m.get("source_name") or m.get("source") or "未知" for m in win
    }
    media = Counter(by_doc.values())
    docs = set(by_doc)
    out = {
        "window": {"from": ta, "to": tb},
        "metrics": {
            "n_docs": len(docs),
            "n_mentions": len(win),
            "n_entities": len(entities),
            "n_edges": len(graph_edges),
            "n_rel": dict(kind_counts),
            "n_media": len(media),
            "media": [{"name": k, "n": v} for k, v in media.most_common()],
        },
        "ranks": ranks,
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "entities": entities[:120],
        "days": _days(ta, tb),
    }
    return out


def _slim(out: dict) -> dict:
    ents = []
    for e in out["entities"]:
        ents.append(
            {
                **{k: e[k] for k in ("id", "name", "type", "mass", "degree", "n_source", "n_target", "n_articles", "aliases")},
                "media": e.get("media") or [],
                "articles": e["articles"][:ARTS_CAP],
                "profile": e.get("profile") or {},
                "person_elements": e.get("person_elements") or [],
            }
        )
    return {
        "window": out["window"],
        "metrics": out["metrics"],
        "ranks": out["ranks"],
        "graph": out["graph"],
        "entities": ents,
    }


def export_viz(out: dict) -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    slices: dict[str, dict] = {}
    for p in sorted(ACTOR_DIR.glob("slice_*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "window" in data:
            slices[slice_key(data["window"])] = _slim(data)
    key = slice_key(out["window"])
    slices[key] = _slim(out)
    payload = (
        "window.ACTOR_SLICES = "
        + json.dumps(slices, ensure_ascii=False)
        + ";\nwindow.ACTOR_SLICE = window.ACTOR_SLICES["
        + json.dumps(key)
        + "];\n"
    )
    (WEB_DIR / "actor-data.js").write_text(payload, encoding="utf-8")


def write_slice(out: dict) -> tuple[Path, Path]:
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ta, tb = out["window"]["from"], out["window"]["to"]
    json_path = ACTOR_DIR / f"slice_{ta}_{tb}.json"
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    m = out["metrics"]
    lines = [
        f"# ActorSlice `{ta}`–`{tb}`",
        "",
        f"- 关系边 **{m['n_edges']}**：" + "、".join(f"{k} {v}" for k, v in (m.get("n_rel") or {}).items()),
        f"- 信源 {m['n_media']}：" + "、".join(f"{x['name']} {x['n']}" for x in (m.get('media') or [])[:5]),
        "",
        "## 排行（按见报篇数）",
        "",
    ]
    for typ in TYPES:
        rows = out["ranks"].get(typ) or []
        if not rows:
            continue
        lines.append(f"### {typ}")
        lines.append("")
        lines.append("| 篇 | 度数 | 主体/对象 | 名称 |")
        lines.append("|----|------|-----------|------|")
        for r in rows[:15]:
            lines.append(
                f"| {r['mass']} | {r['degree']} | {r['n_source']}/{r['n_target']} | {r['name']} |"
            )
        lines.append("")
    md = REPORT_DIR / f"actor_slice_{ta}_{tb}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    export_viz(out)
    return json_path, md
