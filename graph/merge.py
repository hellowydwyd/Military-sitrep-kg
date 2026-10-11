"""S2：Report → 世界 Event 合并。不写 STATE。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from graph.s1 import save_graph

PRED_STRIKE = ("袭击", "发动", "空袭", "打击", "实施")
PRED_PROCESS = ("响起", "移动", "出现", "持续", "标志着", "经由", "接近")
PRED_ATTACH_SPEECH = ("证实", "通报", "提醒", "表示")
PRED_INDEPENDENT_SPEECH = ("谴责", "反驳", "警告", "宣布", "批评", "说", "称", "报道称", "透露")
SPLIT_TLINK = ("发现",)
AFTER_CUES = ("随后", "之后", "接着", "然后")
UMBRELLA = ("两处", "多次", "多个", "多地", "一轮")

ACTOR_CANON = [
    ("俄联邦武装力量", "RU"),
    ("俄军的弹道导弹", "RU"),
    ("俄罗斯国防部", "RU"),
    ("俄军", "RU"),
    ("俄方", "RU"),
    ("俄罗斯", "RU"),
    ("以战机", "IL"),
    ("以军", "IL"),
    ("以方", "IL"),
    ("以色列", "IL"),
    ("内塔尼亚胡", "IL"),
    ("以总理", "IL"),
    ("乌克兰空军", "UA"),
    ("乌空军", "UA"),
    ("乌军", "UA"),
    ("乌克兰", "UA"),
    ("也门胡塞武装", "HOU"),
    ("胡塞", "HOU"),
    ("土耳其通信局", "TR"),
    ("土耳其政府", "TR"),
    ("土耳其", "TR"),
    ("美军飞机", "USAF"),
    ("美国军机", "USAF"),
    ("这些飞机", "USAF"),
    ("林肯", "CVN72"),
    ("乔治·华盛顿", "CVN73"),
]


def _eid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _roles(rep: dict, role: str) -> list[str]:
    return [str(a.get("text") or "").strip() for a in rep.get("arguments") or [] if a.get("role") == role and str(a.get("text") or "").strip()]


def canon_actor(text: str) -> str:
    t = text or ""
    for k, v in ACTOR_CANON:
        if k in t:
            return v
    return t


def actor_set(rep: dict) -> set[str]:
    out = set()
    for t in _roles(rep, "主体"):
        out.add(canon_actor(t))
    return out


def loc_set(rep: dict) -> set[str]:
    raw = _roles(rep, "地点")
    norm = set()
    for x in raw:
        n = x.replace("市", "").replace("所谓", "").replace("“", "").replace("”", "")
        if n:
            norm.add(n)
    return norm


def obj_set(rep: dict) -> set[str]:
    return set(_roles(rep, "对象"))


def loc_overlap(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    for x in a:
        for y in b:
            if x in y or y in x:
                return True
    return False


def time_compatible(a: dict, b: dict) -> bool:
    pa, pb = a.get("time_precision") or "", b.get("time_precision") or ""
    da, db = (a.get("t_event_hint") or "")[:10], (b.get("t_event_hint") or "")[:10]
    if a.get("time_background") or b.get("time_background"):
        return False
    raw_a, raw_b = str(a.get("raw_time") or ""), str(b.get("raw_time") or "")
    if any(k in raw_a or k in raw_b for k in ("协议达成后", "此前", "早前")):
        if da != db and da and db:
            return False
        if (da and not db) or (db and not da):
            return False
    if pa in ("unknown", "fuzzy", "") or pb in ("unknown", "fuzzy", "") or not da or not db:
        return True  # 同簇内再靠实体约束
    if pa == "interval" or pb == "interval":
        return True
    if da == db:
        return True
    try:
        from datetime import date
        d1, d2 = date.fromisoformat(da), date.fromisoformat(db)
        return abs((d1 - d2).days) <= 1
    except ValueError:
        return False


def pred_class(trigger: str, kind: str) -> str:
    t = trigger or ""
    if kind == "计划":
        return "plan"
    if any(t == x or t.startswith(x) for x in PRED_PROCESS):
        return "process"
    if t in SPLIT_TLINK:
        return "tlink_left"
    if kind == "发生" and any(x in t for x in PRED_STRIKE):
        return "strike"
    if t in PRED_ATTACH_SPEECH or (kind == "表态" and t in PRED_ATTACH_SPEECH):
        return "speech_attach"
    if kind == "表态" or t in PRED_INDEPENDENT_SPEECH:
        return "speech"
    if kind == "发生":
        return "happen"
    return "other"


def is_umbrella_obj(rep: dict) -> bool:
    return any(u in o for o in obj_set(rep) for u in UMBRELLA)


def split_dual_targets(rep: dict) -> list[dict]:
    """一条里两个「对象+地点」拆成两条 Report（不改 report_id 源，生成子 id）。"""
    args = list(rep.get("arguments") or [])
    objs = [(i, a) for i, a in enumerate(args) if a.get("role") == "对象"]
    locs = [(i, a) for i, a in enumerate(args) if a.get("role") == "地点"]
    if len(objs) >= 2 and len(locs) >= 2:
        out = []
        others = [a for a in args if a.get("role") not in ("对象", "地点")]
        for n, ((_, o), (_, loc)) in enumerate(zip(objs, locs)):
            child = dict(rep)
            child["arguments"] = others + [o, loc]
            child["report_id"] = _eid(rep["report_id"], "split", str(n))
            child["split_from"] = rep["report_id"]
            out.append(child)
        return out
    return [rep]


class UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


_FACILITY = ("厂", "码头", "机场", "基地", "建筑", "石油", "仓库", "目标", "航母", "导弹")


def _is_facility(text: str) -> bool:
    return any(k in (text or "") for k in _FACILITY)


def _specific_obj_conflict(a: dict, b: dict) -> bool:
    """两处具体设施（炼油厂 vs 码头）不合并；国家/城市别名不算冲突。"""
    oa, ob = obj_set(a), obj_set(b)
    if is_umbrella_obj(a) or is_umbrella_obj(b):
        return True
    if not oa or not ob:
        return False
    if loc_overlap(oa, ob) or oa & ob:
        return False
    fa = [x for x in oa if _is_facility(x)]
    fb = [x for x in ob if _is_facility(x)]
    if fa and fb:
        return True
    return False


def should_merge_strike(a: dict, b: dict) -> bool:
    if a.get("kind") != b.get("kind"):
        return False
    if pred_class(a.get("trigger") or "", a.get("kind") or "") != "strike":
        return False
    if pred_class(b.get("trigger") or "", b.get("kind") or "") != "strike":
        return False
    if is_umbrella_obj(a) or is_umbrella_obj(b):
        return False
    if not time_compatible(a, b):
        return False
    if not (actor_set(a) & actor_set(b)):
        return False
    # 同块同主体的打击：机场别名等允许合并
    if a.get("chunk_id") == b.get("chunk_id"):
        la, lb = loc_set(a), loc_set(b)
        if la and lb and not loc_overlap(la, lb):
            return False
        return True
    if _specific_obj_conflict(a, b):
        return False
    la, lb = loc_set(a), loc_set(b)
    if la and lb and not loc_overlap(la, lb):
        return False
    return True


def death_mentions(text: str) -> set[str]:
    return set(re.findall(r"(\d+)\s*人死亡", text or ""))


def merge_reports(reports: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (reports_with_event_id, events, extra_edges)."""
    expanded: list[dict] = []
    for r in reports:
        expanded.extend(split_dual_targets(r))

    n = len(expanded)
    uf = UF(n)
    cls = [pred_class(r.get("trigger") or "", r.get("kind") or "") for r in expanded]

    # 1) 打击类互并
    for i in range(n):
        if cls[i] != "strike":
            continue
        for j in range(i + 1, n):
            if cls[j] != "strike":
                continue
            if should_merge_strike(expanded[i], expanded[j]):
                uf.union(i, j)

    # 2) 同块过程句并入最近打击
    by_chunk: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(expanded):
        by_chunk[r.get("chunk_id") or ""].append(i)
    for idxs in by_chunk.values():
        strikes = [i for i in idxs if cls[i] == "strike"]
        if not strikes:
            continue
        for i in idxs:
            if cls[i] != "process":
                continue
            cue = str(expanded[i].get("raw_time") or "")
            # 过程句即使含随后也并入主打击（补丁 A）；发现+随后打击不在此类
            host = strikes[0]
            for s in strikes:
                if actor_set(expanded[i]) & actor_set(expanded[s]) or loc_overlap(loc_set(expanded[i]), loc_set(expanded[s])) or not actor_set(expanded[i]):
                    host = s
                    break
            uf.union(i, host)

    # 3) 同块「随后」打击：发现 vs 打击保持分裂，记 TLINK
    tlink_pairs: list[tuple[int, int, str]] = []
    for idxs in by_chunk.values():
        for i in idxs:
            if cls[i] != "tlink_left":
                continue
            cue_host = None
            for j in idxs:
                if cls[j] == "strike" and any(c in str(expanded[j].get("raw_time") or "") + str(expanded[j].get("evidence") or "") for c in AFTER_CUES):
                    cue_host = j
                    break
            if cue_host is not None:
                tlink_pairs.append((i, cue_host, "随后"))

    # 4) 证实/通报 挂到同主体同地的打击（不另建空袭 Event）
    for i in range(n):
        if cls[i] != "speech_attach":
            continue
        # 哀悼日 / 禁止娱乐不挂
        blob = str(expanded[i].get("evidence") or "") + "".join(obj_set(expanded[i]))
        if any(k in blob for k in ("哀悼", "娱乐")):
            continue
        for j in range(n):
            if cls[j] != "strike":
                continue
            same_doc = expanded[i].get("doc_id") == expanded[j].get("doc_id")
            same_chunk = expanded[i].get("chunk_id") == expanded[j].get("chunk_id")
            same_loc = loc_overlap(loc_set(expanded[i]), loc_set(expanded[j])) or loc_overlap(obj_set(expanded[i]), loc_set(expanded[j]))
            if not (same_chunk or same_doc or same_loc):
                continue
            if "袭" in blob or "打击" in blob or "空袭" in blob or same_chunk:
                uf.union(i, j)
                break
            if actor_set(expanded[i]) & actor_set(expanded[j]) and same_loc:
                uf.union(i, j)
                break

    # 5) 独立表态：同 predicate+同主体 canon+时间近 可跨文档合并
    for i in range(n):
        if cls[i] not in ("speech", "happen", "plan", "tlink_left"):
            continue
        for j in range(i + 1, n):
            if cls[j] != cls[i]:
                continue
            if expanded[i].get("kind") != expanded[j].get("kind"):
                continue
            if (expanded[i].get("trigger") or "") != (expanded[j].get("trigger") or ""):
                continue
            if not (actor_set(expanded[i]) & actor_set(expanded[j])):
                continue
            if not time_compatible(expanded[i], expanded[j]):
                continue
            # 两源证实离开：trigger 不同（表示 vs 证实）走 attach 或下面的 corroborated speech
            uf.union(i, j)

    # 5b) 表态 表示/证实 同日同地同对象类型（军机离开）
    for i in range(n):
        if expanded[i].get("kind") != "表态":
            continue
        if expanded[i].get("trigger") not in ("表示", "证实"):
            continue
        for j in range(i + 1, n):
            if expanded[j].get("kind") != "表态":
                continue
            if expanded[j].get("trigger") not in ("表示", "证实"):
                continue
            if loc_overlap(loc_set(expanded[i]), loc_set(expanded[j])) and time_compatible(expanded[i], expanded[j]):
                uf.union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)

    events = []
    edges = []
    conflicts = []
    for root, members in groups.items():
        reps = [expanded[i] for i in members]
        kinds = {r.get("kind") for r in reps}
        # 事件 kind：若含发生则发生，否则众数
        kind = "发生" if "发生" in kinds else (reps[0].get("kind") or "")
        triggers = [r.get("trigger") or "" for r in reps]
        predicate = next((t for t in triggers if pred_class(t, reps[triggers.index(t)].get("kind") or "") == "strike"), None)
        if not predicate:
            predicate = triggers[0]
        actors, locs = set(), set()
        for r in reps:
            actors |= actor_set(r)
            locs |= loc_set(r)
        times = [(r.get("t_event_hint") or "", r.get("time_precision") or "") for r in reps]
        days = sorted({d[:10] for d, p in times if d and p == "day" and len(d) >= 10})
        event_time = days[0] if days else (times[0][0] or "")
        precision = "interval" if len(days) > 1 else (next((p for d, p in times if d), "unknown") or "unknown")
        if len(days) > 1:
            precision = "interval"
        deaths = set()
        for r in reps:
            deaths |= death_mentions((r.get("evidence") or "") + "".join(obj_set(r)))
        obj_all = [o for r in reps for o in obj_set(r) if not any(u in o for u in UMBRELLA)]
        status = "singleton"
        if len({r.get("doc_id") for r in reps}) > 1:
            status = "corroborated"
        if len(deaths) > 1:
            status = "conflicting"
        eid = _eid("event", kind, predicate, ",".join(sorted(actors)[:4]), event_time, str(root))
        ev = {
            "event_id": eid,
            "kind": kind,
            "predicate": predicate,
            "trigger_surface": predicate,
            "event_time": event_time if precision != "unknown" else "",
            "event_time_end": days[-1] if len(days) > 1 else "",
            "time_precision": precision if event_time else "unknown",
            "time_background": any(r.get("time_background") for r in reps),
            "status": status,
            "report_ids": [r["report_id"] for r in reps],
            "actors": sorted(actors),
            "locations": sorted(locs),
            "agreed_objects": sorted(set(obj_all)) if len(set(obj_all)) == 1 else [],
            "layer": "event",
        }
        if len(set(obj_all)) > 1 and status != "conflicting":
            # 别名（机场）可多对象但不写入核心槽
            ev["object_variants"] = sorted(set(obj_all))
        events.append(ev)
        for r in reps:
            r["event_id"] = eid
            edges.append({"type": "REPORTS", "from": r["report_id"], "to": eid})
        if len(deaths) > 1:
            ids = [r["report_id"] for r in reps]
            for a, b in zip(ids, ids[1:]):
                conflicts.append(
                    {"type": "CONFLICTS_WITH", "from": a, "to": b, "fields": ["伤亡人数"]}
                )

    for left, right, cue in tlink_pairs:
        e1, e2 = expanded[left].get("event_id"), expanded[right].get("event_id")
        if e1 and e2 and e1 != e2:
            edges.append(
                {
                    "type": "TLINK",
                    "from": e2,
                    "to": e1,
                    "order": "AFTER",
                    "cue": cue,
                    "scope": "chunk",
                }
            )

    # PARTICIPANT / LOCATED_AT 用 mention，对齐是 S3
    for ev in events:
        for name in ev.get("actors") or []:
            edges.append({"type": "PARTICIPANT", "from": ev["event_id"], "to": name, "role": "主体"})
        for loc in ev.get("locations") or []:
            edges.append({"type": "LOCATED_AT", "from": ev["event_id"], "to": loc})

    return expanded, events, edges + conflicts


def build_s2_graph(s1: dict) -> dict[str, Any]:
    reports = [dict(r) for r in s1.get("reports") or []]
    expanded, events, extra = merge_reports(reports)
    edges = [e for e in (s1.get("edges") or []) if e.get("type") in ("CONTAINS", "SOURCED_FROM", "CLAIM")]
    # SOURCED_FROM 可能仍指向未 split 的 id；补子 report 溯源
    parent_chunk = {r["report_id"]: r.get("chunk_id") for r in reports}
    for r in expanded:
        cid = r.get("chunk_id") or parent_chunk.get(r.get("split_from") or "")
        if cid:
            edges.append({"type": "SOURCED_FROM", "from": r["report_id"], "to": cid})
    edges.extend(extra)
    # 弱/强完备
    for ev in events:
        has_ent = bool(ev.get("actors") or ev.get("locations"))
        has_time = (ev.get("time_precision") or "") in ("day", "month", "interval")
        ev["weak_complete"] = bool(ev.get("predicate") and has_ent and ev.get("report_ids"))
        ev["strong_complete"] = bool(ev["weak_complete"] and has_time and ev.get("actors") and (ev.get("locations") or ev.get("agreed_objects")))
    out = dict(s1)
    out["layer"] = "s2_event"
    out["reports"] = expanded
    out["events"] = events
    out["states"] = []
    out["edges"] = edges
    return out


def s2_stats(g: dict) -> dict:
    evs = g.get("events") or []
    reps = g.get("reports") or []
    n_rep_linked = sum(1 for r in reps if r.get("event_id"))
    by_status: dict[str, int] = defaultdict(int)
    for e in evs:
        by_status[e.get("status") or ""] += 1
    n_conf = sum(1 for e in g.get("edges") or [] if e.get("type") == "CONFLICTS_WITH")
    n_rep_e = sum(1 for e in g.get("edges") or [] if e.get("type") == "REPORTS")
    n_weak = sum(1 for e in evs if e.get("weak_complete"))
    n_strong = sum(1 for e in evs if e.get("strong_complete"))
    return {
        "n_reports": len(reps),
        "n_reports_linked": n_rep_linked,
        "n_events": len(evs),
        "n_states": len(g.get("states") or []),
        "n_reports_edges": n_rep_e,
        "n_conflicts": n_conf,
        "n_weak_complete": n_weak,
        "n_strong_complete": n_strong,
        "by_status": dict(by_status),
        "compression": round(len(evs) / len(reps), 4) if reps else None,
    }


def eval_against_seed(g: dict, seed: dict) -> dict:
    """用 chunk+predicate 启发式对齐种子，检查必并/必分。"""
    reps = g.get("reports") or []
    by_chunk: dict[str, list[dict]] = defaultdict(list)
    for r in reps:
        by_chunk[r.get("chunk_id") or ""].append(r)

    def ids_for(chunk_ids: list[str], kind: str, triggers: tuple[str, ...]) -> set[str]:
        out = set()
        for cid in chunk_ids:
            for r in by_chunk.get(cid, []):
                if kind and r.get("kind") != kind:
                    continue
                if triggers and not any(t in (r.get("trigger") or "") for t in triggers):
                    continue
                if r.get("event_id"):
                    out.add(r["event_id"])
        return out

    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    e5 = ids_for(["b749d0023f85_00"], "发生", ("袭击", "发动", "响起", "移动", "出现"))
    add("E5 同块打击过程合并", len(e5) == 1, f"event_ids={sorted(e5)}")
    e5b = ids_for(["b749d0023f85_00", "d79bfeecb9b8_00"], "发生", ("袭击", "发动"))
    # 第二篇 发生只有禁止；打击在通报挂接
    e5_all = set()
    for cid in ("b749d0023f85_00", "d79bfeecb9b8_00"):
        for r in by_chunk.get(cid, []):
            blob = (r.get("trigger") or "") + (r.get("evidence") or "")
            if r.get("event_id") and any(k in blob for k in ("袭击", "打击", "响起", "弹道导弹")):
                if "哀悼" in blob or "娱乐" in blob:
                    continue
                e5_all.add(r["event_id"])
    add("E5 两源同一 Event", len(e5_all) == 1, f"event_ids={sorted(e5_all)}")
    e6 = ids_for(["d79bfeecb9b8_00"], "表态", ("宣布",))
    add("E6 哀悼日不并入 E5", e6.isdisjoint(e5_all) and len(e6) >= 1, f"E6={sorted(e6)} E5={sorted(e5_all)}")

    e1 = ids_for(["2e5e3e0593fb_00"], "发生", ("发动", "空袭"))
    add("E1 两次发动合并", len(e1) == 1, f"event_ids={sorted(e1)}")
    e2 = ids_for(["2e5e3e0593fb_00", "7f249edc9074_00"], "表态", ("反驳",))
    add("E2 不与 E1 合并", e2.isdisjoint(e1), f"E2={sorted(e2)} E1={sorted(e1)}")

    e9e10 = []
    for r in by_chunk.get("2fc042b7a905_00", []):
        if r.get("kind") == "发生" and "袭击" in (r.get("trigger") or ""):
            if is_umbrella_obj(r):
                continue
            e9e10.append((r.get("event_id"), "".join(obj_set(r)), "".join(loc_set(r))))
    uniq = {x[0] for x in e9e10}
    add("E9/E10 两设施不合并", len(uniq) >= 2, f"pairs={e9e10}")

    disc = ids_for(["ee024cbf2b1d_00"], "发生", ("发现",))
    hit = ids_for(["ee024cbf2b1d_00"], "发生", ("发动", "实施"))
    add("E15 发现≠随后打击", disc.isdisjoint(hit) and disc and len(hit) == 1, f"发现={sorted(disc)} 打击={sorted(hit)}")
    tlinks = [e for e in g.get("edges") or [] if e.get("type") == "TLINK"]
    add("E15 TLINK 存在", any(e.get("from") in hit and e.get("to") in disc for e in tlinks) or any(e.get("cue") for e in tlinks), f"n_tlink={len(tlinks)}")

    arr = ids_for(["e95c0467f067_00"], "发生", ("抵达",))
    leave_ids = set()
    for r in by_chunk.get("e95c0467f067_00", []):
        if r.get("trigger") in ("表示", "证实") and r.get("event_id"):
            leave_ids.add(r["event_id"])
    add("E16 抵达 ≠ E17 离开表态", arr.isdisjoint(leave_ids) and arr and leave_ids, f"抵达={sorted(arr)} 离开={sorted(leave_ids)}")
    add("E17 两源离开同 Event", len(leave_ids) == 1, f"ids={sorted(leave_ids)}")

    n_ok = sum(1 for c in checks if c["ok"])
    return {"n": len(checks), "n_ok": n_ok, "pass": n_ok == len(checks), "checks": checks}
