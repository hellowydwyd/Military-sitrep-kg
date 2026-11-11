"""把消歧后的结果写成可查询的属性图，并按登记元路径走 1 至 5 级。

节点：十类实体、原文文档、事件。
边：业务边（关系名、话语种类、时间、摘录、原文编号）。
查询：锚点编号 + 时间窗 + 元路径，逐级匹配，每级带回原文。

    python3 -m graph.build10
    python3 -m graph.build10 --query 人物：弗拉基米尔·普京 --depth 5
    python3 -m graph.build10 --paths
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict

from align.types import COUNTRY as COUNTRY_NAMES
from extract.pilot10 import SPEECH_REL, is_non_actor
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SRC = DATA / "extract" / "pilot10v3_resolved.json"
GRAPH = DATA / "graph" / "sitrep10.json"

GENERIC_GEAR = frozenset(
    """
    无人机 弹道导弹 导弹 巡航导弹 油轮 战机 干货船 防空系统 战斗机 投射物 武器
    飞机 喷气式无人机 货船 固定翼无人机 防空导弹 军用车辆 武装侦察无人机 船只
    核潜艇 陆基高精度武器 核动力潜艇 雷达 核武器 地对空导弹 舰艇 武器库
    坦克 装甲车 火炮 火箭弹 炮弹 炸弹 水雷 鱼雷 直升机 运输机 侦察机 轰炸机
    军舰 驱逐舰 护卫舰 航母 航空母舰 潜艇 舰载机 反舰导弹 火箭炮 迫击炮
    防空导弹系统 导弹系统 雷达站 电子战系统 卫星 军用卫星 侦察卫星
    军事装备 装备 弹药 武器系统 空中目标 飞行器 无人艇 无人船 无人潜航器
    """.split()
)

# 数量词前缀，例如「两枚弹道导弹」「4架无人机」
QUANT_PREFIX = re.compile(
    r"^(?:[0-9一二两三四五六七八九十百千数多几]+|这|该|此)"
    r"(?:枚|架|艘|辆|门|套|台|个|批|种|型|发|支|组)?"
)


def is_generic_gear(name: str) -> bool:
    n = (name or "").strip().strip("“”\"'《》")
    if not n:
        return False
    bare = QUANT_PREFIX.sub("", n).strip()
    if n in GENERIC_GEAR or bare in GENERIC_GEAR:
        return True
    # 纯类别词加修饰，例如「沙特战机」「伊朗无人机」
    for word in GENERIC_GEAR:
        if len(word) >= 2 and n.endswith(word) and len(n) <= len(word) + 4:
            return True
    return False


# 登记的元路径。
# 图谱是事件中心的星形结构，路径分两类：
#   fanout 事件扇出：从某个事件一次取出各类对象，作为事件全貌
#   linear 语义短链：沿语义明确的关系走两到三跳
META_PATHS = {
    # 一、事件全貌：单跳扇出
    "F1_event_profile": [
        {"rel": "实施", "from": "机构", "to": "事件", "kind": "fanout"},
        {"rel": "参与", "from": "人物", "to": "事件", "kind": "fanout"},
        {"rel": "搭载", "from": "事件", "to": "装备", "kind": "fanout"},
        {"rel": "处于", "from": "事件", "to": "场景", "kind": "fanout"},
        {"rel": "关于", "from": "立场", "to": "事件", "kind": "fanout"},
        {"rel": "针对", "from": "观点", "to": "事件", "kind": "fanout"},
        {"rel": "涉及", "from": "事件", "to": "作品", "kind": "fanout"},
        {"rel": "发布", "from": "平台", "to": "事件", "kind": "fanout"},
    ],
    # 二、人物言行：人物发表的观点及其针对的事件
    "P3_person_claim_event": [
        {"rel": "发表", "from": "人物", "to": "观点", "kind": "linear"},
        {"rel": "针对", "from": "观点", "to": "事件", "kind": "linear"},
    ],
    # 人物表明的立场及其关于的事件
    "P3_person_stance_event": [
        {"rel": "表明", "from": "人物", "to": "立场", "kind": "linear"},
        {"rel": "关于", "from": "立场", "to": "事件", "kind": "linear"},
    ],
    # 三、装备去向：事件动用的装备去了哪里（只认部署去向）
    "P3_event_gear_destination": [
        {"rel": "搭载", "from": "事件", "to": "装备", "kind": "linear"},
        {"rel": "去向", "from": "装备", "to": "地点", "kind": "linear",
         "require_dest": "部署去向"},
    ],
    # 四、部署溯源：事件动用装备的部署方及其上级国家
    "P4_event_gear_org_country": [
        {"rel": "搭载", "from": "事件", "to": "装备", "kind": "linear"},
        {"rel": "部署", "from": "机构", "to": "装备", "kind": "linear"},
        {"rel": "从属", "from": "机构", "to": "机构", "kind": "linear",
         "require_target": "国家"},
    ],
    # 五、多平台口径：同一事件被哪些平台报道
    "P2_event_platform": [
        {"rel": "发布", "from": "平台", "to": "事件", "kind": "fanout"},
    ],
    # 单跳查询
    "P1_org_deploy_gear": [{"rel": "部署", "from": "机构", "to": "装备", "kind": "linear"}],
    "P1_org_event": [{"rel": "实施", "from": "机构", "to": "事件", "kind": "linear"}],
    "P1_event_gear": [{"rel": "搭载", "from": "事件", "to": "装备", "kind": "linear"}],
    "P1_event_scene": [{"rel": "处于", "from": "事件", "to": "场景", "kind": "linear"}],
    "P1_event_work": [{"rel": "涉及", "from": "事件", "to": "作品", "kind": "linear",
                       "require_target": "作品"}],
    "P1_platform_intercept": [{"rel": "拦截", "from": "事件", "to": "装备", "kind": "linear"}],
}


def _load() -> dict:
    return json.loads(SRC.read_text(encoding="utf-8"))


def build(payload: dict) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    doc_index: dict[str, dict] = {}

    for row in payload["docs"]:
        doc_id = row.get("doc_id") or ""
        if doc_id:
            doc_index[doc_id] = {
                "title": row.get("title"),
                "url": row.get("url"),
                "publish_time": row.get("publish_time"),
                "source": row.get("source"),
                "lang": row.get("lang"),
            }
        for ent in row["entities"]:
            nid = ent.get("id") or ent["name"]
            node = nodes.get(nid)
            if node is None:
                node = {
                    "id": nid,
                    "type": ent["type"],
                    "name": ent.get("canonical") or ent["name"],
                    "aliases": set(ent.get("aliases") or []),
                    "docs": set(),
                    "subtype": ent.get("subtype", ""),
                }
                nodes[nid] = node
            node["aliases"].update(ent.get("aliases") or [])
            if doc_id:
                node["docs"].add(doc_id)
        for rel in row["relations"]:
            hid, tid = rel.get("head_id"), rel.get("tail_id")
            if not hid or not tid:
                continue
            # 规范名本身若是记者、发言人这类非施动者，边不入图
            if rel["name"] not in SPEECH_REL:
                head_node = nodes.get(hid) or {}
                head_name = head_node.get("name") or rel.get("head") or ""
                if is_non_actor(head_name):
                    continue
            edges.append({
                "from": hid,
                "to": tid,
                "rel": rel["name"],
                "evidence": rel.get("evidence") or "",
                "derived": bool(rel.get("derived")),
                "doc": doc_id,
                "time": (row.get("publish_time") or "")[:10],
                "head_surface": rel.get("head_surface") or "",
                "tail_surface": rel.get("tail_surface") or "",
                "dest_kind": rel.get("dest_kind") or "",
                "discourse": rel.get("discourse") or "",
                "retyped_from": rel.get("retyped_from") or "",
            })

    out_nodes = []
    for node in nodes.values():
        rec = {
            **{k: node[k] for k in ("id", "type", "name", "subtype")},
            "aliases": sorted(node["aliases"]),
            "doc_count": len(node["docs"]),
        }
        if node["type"] == "装备" and is_generic_gear(node["name"]):
            rec["generic"] = True
        out_nodes.append(rec)

    # 时间邻近边：同一事件组内的文档之间
    by_group: dict[str, list[str]] = defaultdict(list)
    event_of_doc: dict[str, str] = {}
    for ev in payload.get("events") or []:
        for did in ev.get("docs") or []:
            if did:
                event_of_doc[did] = ev["event_id"]
                by_group[ev["event_id"]].append(did)

    return {
        "nodes": out_nodes,
        "edges": edges,
        "docs": doc_index,
        "events": payload.get("events") or [],
        "event_of_doc": event_of_doc,
        "metapaths": {k: list(v) for k, v in META_PATHS.items()},
    }


def _adj(edges: list[dict]) -> dict[tuple[str, str], list[dict]]:
    idx: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in edges:
        idx[(e["from"], e["rel"])].append(e)
    return idx


def _rev(edges: list[dict]) -> dict[tuple[str, str], list[dict]]:
    idx: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in edges:
        idx[(e["to"], e["rel"])].append(e)
    return idx


def _profile_view(trail: list[dict], nodes: dict[str, dict]) -> dict:
    """把扇出的各栏按关系名归拢，作为事件全貌。"""
    view: dict[str, list[dict]] = defaultdict(list)
    for step in trail:
        # 扇出时终点可能落在锚点一侧，取不是锚点的那一端
        other_id = step["to"]
        other_name = step["to_name"]
        if step.get("from") == step.get("anchor_id"):
            other_id, other_name = step["to"], step["to_name"]
        elif step.get("to") == step.get("anchor_id"):
            other_id, other_name = step["from"], step["from_name"]
        view[step["rel"]].append({
            "name": other_name,
            "id": other_id,
            "evidence": step["evidence"],
            "doc": step.get("doc") or "",
        })
    return {k: v for k, v in view.items()}


def walk(
    graph: dict,
    anchors: list[str],
    path_name: str,
    *,
    since: str = "",
    until: str = "",
    fanout: int = 5,
    allow_generic: bool = False,
    single_doc: bool = True,
    profile: bool = False,
) -> list[dict]:
    """按元路径逐级匹配。每一级带回起点、关系、终点、摘录、链接和时间。

    linear 步骤沿前一步的终点继续；branch 步骤从已走过的小径中
    类型相符的节点分出一支，路径总长度仍按步数计。
    """
    steps = META_PATHS[path_name]
    nodes = {n["id"]: n for n in graph["nodes"]}
    docs = graph["docs"]
    fwd = _adj(graph["edges"])
    rev = _rev(graph["edges"])
    type_of = {n["id"]: n["type"] for n in graph["nodes"]}
    generic = {n["id"] for n in graph["nodes"] if n.get("generic")}
    country_ids = {n["id"] for n in graph["nodes"]
                   if n.get("subtype") == "国家" or n.get("name") in COUNTRY_NAMES}
    results: list[dict] = []

    def in_window(e: dict) -> bool:
        t = e.get("time") or ""
        if since and t and t < since:
            return False
        if until and t and t > until:
            return False
        return True

    def options_from(node_id: str, step: dict, *, strict_type: bool = True) -> list[tuple[dict, str]]:
        rel, want_from, want_to = step["rel"], step["from"], step["to"]
        opts = []
        if type_of.get(node_id) == want_from:
            opts = [(e, e["to"]) for e in fwd.get((node_id, rel), [])]
        elif type_of.get(node_id) == want_to:
            opts = [(e, e["from"]) for e in rev.get((node_id, rel), [])]
        elif not strict_type:
            # 扇出：锚点可能处在关系的任一端，先按正向取，再按反向取
            opts = [(e, e["to"]) for e in fwd.get((node_id, rel), [])]
            opts += [(e, e["from"]) for e in rev.get((node_id, rel), [])]
        if not opts:
            return []
        opts = [(e, nxt) for e, nxt in opts
                if type_of.get(nxt) in (want_from, want_to) and nxt != node_id
                and in_window(e)]
        # 只有真正的部署去向才算多跳里的「去向」，事件地点不参与链条
        need_dest = step.get("require_dest")
        if need_dest:
            opts = [(e, nxt) for e, nxt in opts
                    if (e.get("dest_kind") or "") == need_dest]
        # 从属只认上级为国家；其他 require_target 按类型过滤
        need_target = step.get("require_target")
        if need_target == "国家":
            opts = [(e, nxt) for e, nxt in opts if nxt in country_ids]
        elif need_target:
            opts = [(e, nxt) for e, nxt in opts if type_of.get(nxt) == need_target]
        # 泛称装备只作单跳，不参与多级链
        if not allow_generic:
            opts = [(e, nxt) for e, nxt in opts
                    if not (type_of.get(nxt) == "装备" and nxt in generic)]
        opts.sort(key=lambda x: len(x[0].get("evidence") or ""), reverse=True)
        return opts

    def record(e: dict, node_id: str, step: dict, nxt: str) -> dict:
        doc = docs.get(e.get("doc") or "", {})
        return {
            "rel": step["rel"],
            "from": e["from"],
            "from_name": nodes.get(e["from"], {}).get("name", e["from"]),
            "to": e["to"],
            "to_name": nodes.get(e["to"], {}).get("name", e["to"]),
            "walk": "forward" if e["from"] == node_id else "backward",
            "next": nxt,
            "anchor_id": anchors[0] if anchors else "",
            "branch": step.get("kind") == "branch",
            "evidence": e.get("evidence") or "",
            "derived": e.get("derived", False),
            "dest_kind": e.get("dest_kind") or "",
            "discourse": e.get("discourse") or "",
            "time": e.get("time") or "",
            "doc": e.get("doc") or "",
            "url": doc.get("url") or "",
            "title": doc.get("title") or "",
        }

    def same_doc(trail: list[dict]) -> str | None:
        """整条链是否落在同一篇稿件内。跨稿拼接的链条不作数。"""
        ids = {s.get("doc") for s in trail if s.get("doc")}
        return ids.pop() if len(ids) == 1 else None

    def expand(node_id: str, step_i: int, trail: list[dict], seen: set[str]) -> None:
        if step_i >= len(steps):
            doc = same_doc(trail)
            if single_doc and not doc:
                return
            result = {"anchor": anchors[0], "path": path_name,
                      "doc": doc or "", "steps": list(trail)}
            if profile:
                result["profile"] = _profile_view(trail, nodes)
            results.append(result)
            return
        step = steps[step_i]
        if step.get("kind") == "fanout":
            # 扇出：每一栏各自取边，栏与栏之间不串联，缺栏不影响其他栏
            facets = options_from(node_id, step, strict_type=False)[:fanout]
            for e, nxt in facets:
                trail.append(record(e, node_id, step, nxt))
            expand(node_id, step_i + 1, trail, seen)
            for _ in facets:
                trail.pop()
            return
        for e, nxt in options_from(node_id, step)[:fanout]:
            if nxt in seen:
                continue
            trail.append(record(e, node_id, step, nxt))
            expand(nxt, step_i + 1, trail, seen | {nxt})
            trail.pop()

    first_from = steps[0]["from"]
    first_to = steps[0]["to"]
    for anchor in anchors:
        if type_of.get(anchor) not in (first_from, first_to):
            continue
        expand(anchor, 0, [], {anchor})
    return results


def resolve_anchor(graph: dict, text: str) -> list[str]:
    """锚点可以是编号，也可以是名称或别名。"""
    t = (text or "").strip()
    if not t:
        return []
    hit = [n["id"] for n in graph["nodes"] if n["id"] == t]
    if hit:
        return hit
    hit = [n["id"] for n in graph["nodes"] if n["name"] == t]
    if hit:
        return hit
    return [n["id"] for n in graph["nodes"] if t in (n.get("aliases") or [])]


def summary(graph: dict) -> dict:
    from collections import Counter

    return {
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "docs": len(graph["docs"]),
        "events": len(graph["events"]),
        "by_type": dict(Counter(n["type"] for n in graph["nodes"])),
        "by_rel": dict(Counter(e["rel"] for e in graph["edges"])),
        "derived_edges": sum(1 for e in graph["edges"] if e.get("derived")),
        "edges_with_evidence": sum(1 for e in graph["edges"] if (e.get("evidence") or "").strip()),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="十类属性图构建与元路径查询")
    p.add_argument("--src", default=str(SRC))
    p.add_argument("--out", default=str(GRAPH))
    p.add_argument("--query", default="", help="锚点名称或编号")
    p.add_argument("--path", default="F1_event_profile")
    p.add_argument("--since", default="")
    p.add_argument("--until", default="")
    p.add_argument("--depth", type=int, default=0, help="只保留前 N 级")
    p.add_argument("--paths", action="store_true", help="列出全部元路径及其可达条数")
    p.add_argument("--allow-generic", action="store_true", help="多跳时允许泛称装备")
    p.add_argument("--cross-doc", action="store_true", help="允许跨稿件拼接链条（默认只取同稿）")
    a = p.parse_args(argv)

    payload = json.loads(Path(a.src).read_text(encoding="utf-8"))
    graph = build(payload)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False) + "\n", encoding="utf-8")
    info = summary(graph)
    info["out"] = str(out)
    print(json.dumps(info, ensure_ascii=False, indent=2))

    if a.paths:
        rows = []
        for name in META_PATHS:
            steps = META_PATHS[name]
            n = 0
            anchors = 0
            for node in graph["nodes"]:
                if node["type"] in (steps[0]["from"], steps[0]["to"]):
                    hits = walk(graph, [node["id"]], name, allow_generic=a.allow_generic,
                                single_doc=not a.cross_doc)
                    if hits:
                        anchors += 1
                    n += len(hits)
            rows.append({"path": name, "depth": len(steps), "hits": n,
                         "anchors_with_hit": anchors})
        print(json.dumps(rows, ensure_ascii=False, indent=2))

    if a.query:
        ids = resolve_anchor(graph, a.query)
        if not ids:
            print(json.dumps({"error": "锚点未找到", "query": a.query}, ensure_ascii=False))
            return 1
        for anchor in ids:
            hits = walk(graph, [anchor], a.path, since=a.since, until=a.until)
            if a.depth:
                hits = [h for h in hits if len(h["steps"]) >= a.depth]
            print(json.dumps({
                "anchor": anchor,
                "path": a.path,
                "hits": len(hits),
                "sample": hits[:3],
            }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
