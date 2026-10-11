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
from actor.relations import KIND_ZH, belong_country, classify_trigger


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


def build_slice(
    *,
    ta: str = DEFAULT_FROM,
    tb: str = DEFAULT_TO,
    mentions: list[dict] | None = None,
    use_llm: bool = False,
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
            "day": "",
            "source_name": "",
        }
    )
    for m in win:
        if (m.get("via") or "extract") != "extract":
            continue
        if m["role"] not in ("主体", "对象"):
            continue
        key = (m["doc_id"], m["chunk_i"], m["event_i"])
        rec = ev_map[key]
        rec["trig"] = m.get("trigger") or rec["trig"]
        rec["ekind"] = m.get("kind") or rec["ekind"]
        rec["doc"] = m["doc_id"]
        rec["title"] = m.get("title") or rec["title"]
        rec["day"] = m.get("day") or rec["day"]
        rec["source_name"] = m.get("source_name") or rec["source_name"]
        if m["role"] == "主体":
            rec["src"].add(m["entity_id"])
        else:
            rec["tgt"].add(m["entity_id"])
    for rec in ev_map.values():
        kind = classify_trigger(rec["trig"], rec.get("ekind") or "")
        if not kind:
            continue
        for a in rec["src"]:
            for b in rec["tgt"]:
                if a == b or a not in id_of or b not in id_of:
                    continue
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
                    },
                )
                slot["triggers"][rec["trig"] or KIND_ZH[kind]] += 1
                slot["docs"].add(rec["doc"])
                if rec["day"]:
                    slot["days"].add(rec["day"])
                titles = {x.get("title") for x in slot["examples"]}
                if rec["title"] and rec["title"] not in titles:
                    slot["examples"].append(
                        {
                            "day": rec["day"],
                            "title": rec["title"],
                            "source_name": rec.get("source_name") or "",
                        }
                    )

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

    edges = []
    deg: Counter = Counter()
    for slot in bag.values():
        n_docs = max(len(slot["docs"]), 1)
        trig = slot["triggers"].most_common(1)[0][0]
        days = sorted(d for d in slot["days"] if d)
        edges.append(
            {
                "source": slot["source"],
                "target": slot["target"],
                "kind": slot["kind"],
                "label": slot["label"],
                "trigger": trig,
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
    graph_nodes = [
        {k: ent[k] for k in ("id", "name", "type", "mass", "degree", "n_source", "n_target")}
        for ent in entities
        if ent["id"] in node_ids
    ]
    kind_counts = Counter(e["kind"] for e in graph_edges)

    ranks = {}
    for typ in TYPES:
        ranks[typ] = [
            {
                "id": e["id"],
                "name": e["name"],
                "mass": e["mass"],
                "degree": e["degree"],
                "n_source": e["n_source"],
                "n_target": e["n_target"],
            }
            for e in entities
            if e["type"] == typ
        ][:RANK_TOP]

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
