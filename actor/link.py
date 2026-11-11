"""表面名 → canonical。种子别名 + 同类型规则合并。未分类碎片不建实体。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from align.cluster import UnionFind
from align.methods import method_exact_norm
from actor.aliases import SEED_GROUPS
from actor.clean import army_canon, person_seed_of, ship_canon, system_canon
from actor.config import ACTOR_DIR
from actor.harvest import load_mentions
from actor.types import country_of, llm_types, rule_type


def _eid(name: str) -> str:
    return "e:" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def _guess_type(name: str, voted: str) -> str:
    if voted:
        return voted
    if country_of(name):
        return "Country"
    if person_seed_of(name) or ("·" in name and "号" not in name):
        return "Person"
    if army_canon(name):
        return "Org"
    return rule_type(name, "") or ""


def _pick_canonical(members: list[str], freq: dict[str, int]) -> str:
    mset = set(members)
    for g in SEED_GROUPS:
        if any(x in mset for x in g):
            return g[0]
    armies = [army_canon(m) for m in members]
    armies = [a for a in armies if a]
    if armies:
        return Counter(armies).most_common(1)[0][0]
    people = [person_seed_of(m) for m in members]
    people = [p for p in people if p]
    if people:
        return Counter(people).most_common(1)[0][0]
    countries = [m for m in members if country_of(m) == m]
    if countries:
        return sorted(countries, key=lambda x: (-freq.get(x, 0), len(x), x))[0]
    return sorted(members, key=lambda x: (-freq.get(x, 0), len(x), x))[0]


def surfaces_of(mentions: list[dict]) -> dict[str, dict]:
    bag: dict[str, dict] = {}
    for m in mentions:
        s = m["surface"]
        rec = bag.setdefault(
            s,
            {
                "surface": s,
                "n": 0,
                "docs": set(),
                "roles": Counter(),
                "type_votes": Counter(),
                "titles": [],
            },
        )
        rec["n"] += 1
        rec["docs"].add(m["doc_id"])
        rec["roles"][m["role"]] += 1
        if m.get("type_rule"):
            rec["type_votes"][m["type_rule"]] += 1
        if m.get("title") and m["title"] not in rec["titles"]:
            rec["titles"].append(m["title"])
    out = {}
    for s, rec in bag.items():
        voted = rec["type_votes"].most_common(1)[0][0] if rec["type_votes"] else ""
        top_role = rec["roles"].most_common(1)[0][0]
        fresh = rule_type(s, top_role) or ""
        # 旧提及把「总统令」标成人物时，以新规则为准。
        if fresh and fresh != "Person" and (not voted or voted == "Person"):
            voted = fresh
        elif not voted:
            voted = fresh
        elif not fresh and voted == "Person" and s.endswith(("演习", "会议", "会谈")):
            voted = ""
        out[s] = {
            "surface": s,
            "n": rec["n"],
            "n_docs": len(rec["docs"]),
            "type": _guess_type(s, voted),
            "titles": rec["titles"][:5],
        }
    return out


def fill_types(surfaces: dict[str, dict], *, use_llm: bool, min_docs: int = 3) -> int:
    need = [
        {"name": s, "titles": v["titles"]}
        for s, v in surfaces.items()
        if not v["type"] and v["n_docs"] >= min_docs
    ]
    if not need or not use_llm:
        return 0
    labeled = 0
    for i in range(0, len(need), 40):
        chunk = need[i : i + 40]
        got = llm_types(chunk)
        for name, typ in got.items():
            if name in surfaces and not surfaces[name]["type"]:
                surfaces[name]["type"] = typ
                labeled += 1
    return labeled


def _merge(surfaces: dict[str, dict]) -> tuple[dict[str, str], list[dict]]:
    names = list(surfaces)
    uf = UnionFind(names)
    for g in SEED_GROUPS:
        present = [x for x in g if x in uf.parent]
        for a, b in zip(present, present[1:]):
            uf.union(a, b)
    for s in names:
        if surfaces[s]["type"] == "Person":
            host = person_seed_of(s)
            if host:
                uf.union(s, host)
            continue
        ac = army_canon(s)
        if ac:
            uf.union(s, ac)
        if "革命卫队" in s and "和" not in s and len(s) <= 22:
            if not any(b in s for b in ("快艇", "弹药", "无人机", "防空系统", "目标")):
                uf.union(s, "伊朗伊斯兰革命卫队")
        sc = ship_canon(s) or system_canon(s)
        if sc:
            uf.union(s, sc)
    by_type: dict[str, list[str]] = defaultdict(list)
    for s, rec in surfaces.items():
        if rec["type"]:
            by_type[rec["type"]].append(s)
    for typ, group in by_type.items():
        if len(group) < 2:
            continue
        for a, b in method_exact_norm(group):
            uf.union(a, b)
    freq = {s: rec["n"] for s, rec in surfaces.items()}
    clusters = defaultdict(list)
    for s in names:
        clusters[uf.find(s)].append(s)
    surface_to = {}
    entities = []
    for members in clusters.values():
        members = [m for m in members if m in surfaces]
        if not members:
            continue
        canon = _pick_canonical(members, freq)
        types = Counter(surfaces[m]["type"] for m in members if surfaces[m]["type"])
        typ = types.most_common(1)[0][0] if types else _guess_type(canon, "")
        if not typ:
            continue
        aliases = sorted(set(members) - {canon})
        ent = {
            "id": _eid(canon),
            "name": canon,
            "type": typ,
            "aliases": aliases,
        }
        entities.append(ent)
        for m in members:
            surface_to[m] = ent["id"]
    entities.sort(key=lambda e: e["name"])
    return surface_to, entities


def link(mentions: list[dict] | None = None, *, use_llm: bool = False) -> dict:
    mentions = mentions if mentions is not None else load_mentions()
    surfaces = surfaces_of(mentions)
    n_llm = fill_types(surfaces, use_llm=use_llm)
    surf_to, entities = _merge(surfaces)
    by_id = {e["id"]: e for e in entities}
    kept = []
    for m in mentions:
        eid = surf_to.get(m["surface"])
        if not eid:
            continue
        ent = by_id[eid]
        m["entity_id"] = eid
        m["entity_name"] = ent["name"]
        m["entity_type"] = ent["type"]
        if not m.get("type_rule"):
            m["type_rule"] = surfaces[m["surface"]]["type"]
        kept.append(m)
    return {
        "n_llm_typed": n_llm,
        "n_entities": len(entities),
        "n_untyped": sum(1 for v in surfaces.values() if not v["type"]),
        "n_dropped": len(mentions) - len(kept),
        "entities": entities,
        "mentions": kept,
        "surfaces": {k: {**v, "entity_id": surf_to.get(k, "")} for k, v in surfaces.items()},
    }


def write_link(out: dict) -> Path:
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    path = ACTOR_DIR / "entities.json"
    slim = {
        "n_entities": out["n_entities"],
        "n_llm_typed": out["n_llm_typed"],
        "n_untyped": out["n_untyped"],
        "n_dropped": out.get("n_dropped", 0),
        "entities": out["entities"],
    }
    path.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from actor.harvest import write_mentions

    write_mentions(out["mentions"])
    return path
