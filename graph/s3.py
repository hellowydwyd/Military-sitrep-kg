"""S3：对齐 PARTICIPANT / LOCATED_AT，并提供经 Event 的共现查询。"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from align.apply import apply_aliases, apply_location_aliases, load_groups
from graph.s1 import save_graph

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PROTECTED = frozenset(
    {"sitrep.json", "sitrep.b1.json", "sitrep.b1_seq.json", "sitrep.s1.json", "sitrep.s2.json"}
)

GARBAGE_ACTOR = {
    "此次袭击",
    "这项行动",
    "行动",
    "其成员",
    "声明",
    "报道",
    "飞机",
    "军方",
    "该建筑",
    "该基地",
}

# 试点补层：B1 别名表未把「俄军」并进「俄罗斯」
EXTRA_ACTOR = [
    {"id": "actor:俄罗斯", "canonical": "俄罗斯", "aliases": ["俄军", "俄军的弹道导弹", "俄联邦武装力量"]},
    {"id": "actor:以军", "canonical": "以军", "aliases": ["以战机", "以军士兵"]},
    {"id": "actor:乌军", "canonical": "乌军", "aliases": ["乌克兰武装部队", "乌克兰空军", "乌空军"]},
]
EXTRA_LOC = [
    {"id": "loc:贝兹梅尔空军基地", "canonical": "贝兹梅尔空军基地", "aliases": ["空军基地", "保加利亚贝兹梅尔空军基地"]},
    {"id": "loc:黎巴嫩南部", "canonical": "黎巴嫩南部", "aliases": ["黎南部“安全缓冲区”内巴拉希特地区的一栋建筑", "黎巴嫩南部所谓“安全缓冲区”内"]},
]


def _merge_groups(base: list[dict], extra: list[dict]) -> list[dict]:
    by_id = {g["id"]: dict(g) for g in base}
    for g in extra:
        cur = by_id.get(g["id"])
        if not cur:
            by_id[g["id"]] = dict(g)
            continue
        aliases = list(cur.get("aliases") or [])
        for a in g.get("aliases") or []:
            if a not in aliases:
                aliases.append(a)
        cur["aliases"] = aliases
        by_id[g["id"]] = cur
    return list(by_id.values())


def restore_mentions(graph: dict) -> dict:
    """用 Report 槽位还原 mention，替换 S2 的 RU/IL 代码。"""
    by_ev: dict[str, dict] = defaultdict(lambda: {"actors": [], "locations": [], "roles": []})
    for r in graph.get("reports") or []:
        eid = r.get("event_id")
        if not eid:
            continue
        for a in r.get("arguments") or []:
            role, text = a.get("role"), str(a.get("text") or "").strip()
            if not text:
                continue
            if role == "地点":
                by_ev[eid]["locations"].append(text)
            elif role == "主体":
                if text not in GARBAGE_ACTOR:
                    by_ev[eid]["actors"].append(text)
                    by_ev[eid]["roles"].append(("主体", text))
            elif role == "对象":
                if text not in GARBAGE_ACTOR:
                    by_ev[eid]["roles"].append(("对象", text))
    for ev in graph.get("events") or []:
        pack = by_ev.get(ev["event_id"]) or {}
        actors = list(dict.fromkeys(pack.get("actors") or []))
        locs = list(dict.fromkeys(pack.get("locations") or ev.get("locations") or []))
        ev["actors"] = actors
        ev["locations"] = locs
        ev["mention_roles"] = pack.get("roles") or []
    return graph


def align_s3(graph: dict, *, actor_groups: list[dict], loc_groups: list[dict]) -> dict:
    restore_mentions(graph)
    apply_aliases(graph, actor_groups)
    apply_location_aliases(graph, loc_groups)
    # 对象槽：对齐到已有 Actor 节点的也写 PARTICIPANT
    amap = {}
    for g in graph.get("actors") or []:
        rec = {"id": g["id"], "canonical": g["canonical"]}
        for a in list(g.get("aliases") or []) + [g.get("canonical") or ""]:
            if a:
                amap[a] = rec
    for g in actor_groups:
        rec = {"id": g["id"], "canonical": g["canonical"]}
        for a in list(g.get("aliases") or []) + [g["canonical"]]:
            amap.setdefault(a, rec)
    extra_edges = []
    seen = {(e.get("from"), e.get("to"), e.get("mention")) for e in graph.get("edges") or [] if e.get("type") == "PARTICIPANT"}
    for ev in graph.get("events") or []:
        for role, text in ev.get("mention_roles") or []:
            if role != "对象":
                continue
            hit = amap.get(text)
            if not hit:
                continue
            key = (ev["event_id"], hit["id"], text)
            if key in seen:
                continue
            seen.add(key)
            extra_edges.append(
                {
                    "type": "PARTICIPANT",
                    "from": ev["event_id"],
                    "to": hit["id"],
                    "mention": text,
                    "canonical": hit["canonical"],
                    "role": "对象",
                }
            )
    if extra_edges:
        graph["edges"] = list(graph.get("edges") or []) + extra_edges
    graph["layer"] = "s3_align"
    return graph


def resolve_entity(graph: dict, name: str) -> dict | None:
    n = (name or "").strip()
    if not n:
        return None
    # 地名优先，避免「基辅」命中 Actor 别名「乌克兰首都基辅」
    for kind, key in (("location", "locations"), ("actor", "actors")):
        for node in graph.get(key) or []:
            aliases = list(node.get("aliases") or []) + [node.get("canonical") or "", node.get("id") or ""]
            if n in aliases or n == node.get("id"):
                return {"kind": kind, **node}
    for ev in graph.get("events") or []:
        for a in ev.get("actors_aligned") or []:
            if n in (a.get("mention"), a.get("canonical"), a.get("id")):
                return {"kind": "actor", "id": a["id"], "canonical": a["canonical"], "aliases": [a.get("mention")]}
        for a in ev.get("locations_aligned") or []:
            if n in (a.get("mention"), a.get("canonical"), a.get("id")):
                return {"kind": "location", "id": a["id"], "canonical": a["canonical"], "aliases": [a.get("mention")]}
    return None


def events_for_entity(graph: dict, name: str) -> list[dict]:
    ent = resolve_entity(graph, name)
    if not ent:
        return []
    eid, kind = ent["id"], ent["kind"]
    want = "PARTICIPANT" if kind == "actor" else "LOCATED_AT"
    hit_events = {e.get("from") for e in graph.get("edges") or [] if e.get("type") == want and e.get("to") == eid}
    out = []
    for ev in graph.get("events") or []:
        aligned = ev.get("actors_aligned") if kind == "actor" else ev.get("locations_aligned")
        if ev["event_id"] in hit_events or any(a.get("id") == eid for a in aligned or []):
            out.append(ev)
    return out


def cooccur(graph: dict, a: str, b: str) -> list[dict]:
    """两实体共同参与的 Event（不升格为结盟）。"""
    ea = {e["event_id"] for e in events_for_entity(graph, a)}
    eb = {e["event_id"] for e in events_for_entity(graph, b)}
    both = ea & eb
    return [e for e in graph.get("events") or [] if e["event_id"] in both]


def neighbors_via_event(graph: dict, name: str) -> list[dict]:
    evs = events_for_entity(graph, name)
    self = resolve_entity(graph, name)
    sid = (self or {}).get("id")
    other: dict[str, dict] = {}
    ev_ids = {e["event_id"] for e in evs}
    for e in graph.get("edges") or []:
        if e.get("type") != "PARTICIPANT" or e.get("from") not in ev_ids:
            continue
        if e.get("to") == sid:
            continue
        rec = other.setdefault(e["to"], {"id": e["to"], "canonical": e.get("canonical"), "event_ids": []})
        rec["event_ids"].append(e["from"])
    return sorted(other.values(), key=lambda x: -len(set(x["event_ids"])))


def s3_stats(graph: dict) -> dict:
    st = dict(graph.get("align_stats") or {})
    n_part = sum(1 for e in graph.get("edges") or [] if e.get("type") == "PARTICIPANT")
    n_loc = sum(1 for e in graph.get("edges") or [] if e.get("type") == "LOCATED_AT")
    n_can = sum(1 for e in graph.get("edges") or [] if e.get("type") == "PARTICIPANT" and e.get("canonical"))
    return {
        "n_events": len(graph.get("events") or []),
        "n_actor_nodes": len(graph.get("actors") or []),
        "n_location_nodes": len(graph.get("locations") or []),
        "n_participant": n_part,
        "n_located_at": n_loc,
        "n_participant_canonical": n_can,
        **st,
    }


def _seed_checks(graph: dict) -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    ru = resolve_entity(graph, "俄军")
    ru2 = resolve_entity(graph, "俄方")
    add("俄军/俄方同 id", bool(ru and ru2 and ru["id"] == ru2["id"]), f"{ru} vs {ru2}")
    ky = resolve_entity(graph, "基辅")
    ky2 = resolve_entity(graph, "基辅市")
    add("基辅/基辅市同 id", bool(ky and ky2 and ky["id"] == ky2["id"]), f"{ky and ky['id']} {ky2 and ky2['id']}")
    evs = cooccur(graph, "内塔尼亚胡", "土耳其") or cooccur(graph, "以色列", "土耳其")
    add("以方主体×土耳其经 Event 共现", len(evs) >= 1, f"n={len(evs)}")
    hou = events_for_entity(graph, "也门胡塞武装") or events_for_entity(graph, "胡塞武装")
    add("胡塞武装可查 Event", len(hou) >= 1, f"n={len(hou)}")
    n_ok = sum(1 for c in checks if c["ok"])
    return {"n": len(checks), "n_ok": n_ok, "pass": n_ok == len(checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="S3：对齐参与/地点边 + 共现查询")
    p.add_argument("--in", dest="src", default="")
    p.add_argument("--out", default=str(DATA / "graph" / "sitrep.s3.json"))
    p.add_argument("--query", default="", help="查某实体参与的 Event")
    p.add_argument("--cooccur", nargs=2, metavar=("A", "B"), help="两实体共现 Event")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    default_in = DATA / "graph" / "sitrep.s2.json"
    default_q = DATA / "graph" / "sitrep.s3.json"
    src = Path(args.src) if args.src else (default_q if (args.query or args.cooccur) else default_in)
    if args.query or args.cooccur:
        path = src if src.is_file() else default_q
        if not path.is_file():
            print("请先 python -m graph.s3 生成 sitrep.s3.json")
            return 1
        g = json.loads(path.read_text(encoding="utf-8"))
        if args.query:
            ent = resolve_entity(g, args.query)
            evs = events_for_entity(g, args.query)
            print(json.dumps({"entity": ent, "n_events": len(evs), "neighbors": neighbors_via_event(g, args.query)[:12], "events": [{"event_id": e["event_id"], "predicate": e.get("predicate"), "kind": e.get("kind"), "event_time": e.get("event_time")} for e in evs[:20]]}, ensure_ascii=False, indent=2))
            return 0
        a, b = args.cooccur
        evs = cooccur(g, a, b)
        print(json.dumps({"a": resolve_entity(g, a), "b": resolve_entity(g, b), "n": len(evs), "events": [{"event_id": e["event_id"], "predicate": e.get("predicate"), "kind": e.get("kind")} for e in evs]}, ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    if out.name in PROTECTED and not args.force:
        print(f"拒绝覆盖 {out}。请用 --out data/graph/sitrep.s3.json")
        return 2
    if not src.is_file():
        print(f"找不到 {src}，请先 python -m graph.s2")
        return 1
    g = json.loads(src.read_text(encoding="utf-8"))
    actors = _merge_groups(load_groups(ROOT / "align" / "aliases.json"), EXTRA_ACTOR)
    locs = _merge_groups(load_groups(ROOT / "align" / "loc_aliases.json"), EXTRA_LOC)
    g = align_s3(g, actor_groups=actors, loc_groups=locs)
    path = save_graph(g, out)
    s = s3_stats(g)
    ev = _seed_checks(g)
    print(f"写入 {path}")
    print(
        f"Actor 节点 {s['n_actor_nodes']}  Location {s['n_location_nodes']}  "
        f"PARTICIPANT {s['n_participant']}（canonical {s['n_participant_canonical']}）  "
        f"LOCATED_AT {s['n_located_at']}"
    )
    print(
        f"对齐命中 actor {s.get('actor_mentions_linked')}/{s.get('actor_mentions_linked', 0) + s.get('actor_mentions_unlinked', 0)}  "
        f"loc {s.get('location_mentions_linked')}/{s.get('location_mentions_linked', 0) + s.get('location_mentions_unlinked', 0)}"
    )
    print(f"种子检查 {ev['n_ok']}/{ev['n']} pass={ev['pass']}")
    for c in ev["checks"]:
        print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")

    report = DATA / "reports" / "s3_align.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# S3 对齐与共现验收",
        "",
        f"- 输入：`{src}`",
        f"- 输出：`{path}`",
        f"- Actor **{s['n_actor_nodes']}** | Location **{s['n_location_nodes']}**",
        f"- PARTICIPANT **{s['n_participant']}**（带 canonical **{s['n_participant_canonical']}**）| LOCATED_AT **{s['n_located_at']}**",
        f"- Actor mention 命中 **{s.get('actor_mentions_linked')}** / 未命中 **{s.get('actor_mentions_unlinked')}**",
        f"- Location mention 命中 **{s.get('location_mentions_linked')}** / 未命中 **{s.get('location_mentions_unlinked')}**",
        "",
        "查询：`python -m graph.s3 --query 俄军` 或 `--cooccur 以色列 土耳其`",
        "",
        f"种子检查 **{ev['n_ok']}/{ev['n']}**",
        "",
    ]
    for c in ev["checks"]:
        lines.append(f"- {'通过' if c['ok'] else '失败'} {c['name']} — {c['detail']}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告 {report}")
    return 0 if ev["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
