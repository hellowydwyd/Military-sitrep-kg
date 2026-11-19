"""从抽取结果构建事件图谱。

节点是实体（国家、机构、人物、装备、地点、作品）、事件和文档。
论元边从事件发出。辖属边把机构归到国家，供地图按国家聚合。
泛称实体在上游降成字面值，不进节点。

输出一份自包含 JSON。

    python3 -m graph.build
    python3 -m graph.build --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl --out data/graph/sitrep.json
    python3 -m graph.build --stats
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 结构关系：两端都是实体
STRUCT_RELATIONS = {
    "隶属", "辖属", "驻地", "部署方", "去向",
    "任职", "会晤", "搭载", "位于", "敌对",
}

# 事件论元边（事件 -> 实体）
ARG_RELATIONS = None  # 由 slots.ROLE_NAMES 决定


def _role_names() -> set[str]:
    global ARG_RELATIONS
    if ARG_RELATIONS is None:
        from extract.slots import ROLE_NAMES
        ARG_RELATIONS = set(ROLE_NAMES)
    return ARG_RELATIONS


def load(src: Path) -> list[dict]:
    rows = []
    for line in src.open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build(rows: list[dict], *, infer_country: bool = False) -> dict:
    """构建属性图。

    infer_country=True 时，对仍无国别归属的机构用名称标志补一条推断边，
    并标 source=inferred（可视化应画成虚线）。
    """
    from extract import country_infer, entity_norm

    # 前置：实体归一（简称合全称）+ 泛称降级（港口、无人机不建节点）
    rows, norm_stat = entity_norm.apply(rows)
    # 辖属纠错（剔除跨国/双边机构的错误归属）
    rows, _ = country_infer.mark_sources(rows)
    rows, aff_stat = country_infer.fix_affiliation(rows)
    if infer_country:
        rows, inf_stat = country_infer.infer(rows)
    else:
        inf_stat = {}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    docs: dict[str, dict] = {}
    events: list[dict] = []

    def add_node(nid: str, typ: str, name: str, doc_id: str, extra: dict | None = None) -> dict:
        n = nodes.get(nid)
        if n is None:
            n = {"id": nid, "type": typ, "name": name, "docs": set(), "degree": 0}
            if extra:
                n.update(extra)
            nodes[nid] = n
        n["docs"].add(doc_id)
        return n

    for row in rows:
        doc_id = row.get("doc_id") or ""
        docs[doc_id] = {
            "title": row.get("title") or "",
            "publish_time": row.get("publish_time") or "",
            "url": row.get("url") or "",
            "source_name": row.get("source_name") or "",
        }
        # 文档也是节点，「报道」边才有两端
        add_node(doc_id, "文档", row.get("title") or doc_id, doc_id,
                 {"publish_time": row.get("publish_time") or ""})

        # 实体节点。text 即节点 id（跨篇同名合并）
        for e in row.get("entities") or []:
            name, typ = e.get("text") or "", e.get("type") or ""
            if not name or not typ:
                continue
            n = add_node(name, typ, name, doc_id)
            if e.get("aliases"):
                n.setdefault("aliases", set()).update(e["aliases"])

        # 事件节点
        ev_ids: dict[str, str] = {}
        for i, ev in enumerate(row.get("events") or []):
            eid = f"{doc_id}#e{i}"
            ev_ids[ev["text"]] = eid
            t, sub = ev.get("event_type") or "", ev.get("subtype") or ""
            nodes[eid] = {
                "id": eid,
                "type": "事件",
                "name": ev["text"],
                "event_type": t,
                "subtype": sub,
                "kind": ev.get("kind") or "",
                "time": ev.get("time_norm") or "",
                "time_raw": ev.get("raw_time") or "",
                "trigger": ev.get("trigger") or "",
                "doc": doc_id,
                "docs": {doc_id},
                "degree": 0,
            }
            events.append({
                "id": eid,
                "text": ev["text"],
                "event_type": t,
                "subtype": sub,
                "time": ev.get("time_norm") or "",
                "doc": doc_id,
                "doc_title": row.get("title") or "",
            })
            # 事件归属文档
            edges.append({
                "from": doc_id, "to": eid, "rel": "报道",
                "doc": doc_id, "time": (row.get("publish_time") or "")[:10],
            })

        # 关系边。论元的 head 是事件文本，转成事件节点 id
        for x in row.get("relations") or []:
            name = x.get("name") or ""
            h, t = x.get("head") or "", x.get("tail") or ""
            if not h or not t:
                continue
            # 头是事件 -> 论元边；否则是实体间结构关系
            if h in ev_ids:
                hid = ev_ids[h]
                etype = "arg"
            elif h in nodes:
                hid = h
                etype = "struct"
            else:
                continue
            # 尾端可能被降级为字面值（泛称），那时不建节点边
            if t not in nodes:
                tid = None
            else:
                tid = t
            if tid is None:
                # 字面值论元：保留为事件属性，不画边
                if etype == "arg":
                    nodes[hid].setdefault("literals", []).append({"role": name, "text": t})
                continue
            edge = {
                "from": hid, "to": tid, "rel": name,
                "doc": doc_id,
                "time": (row.get("publish_time") or "")[:10],
                "evidence": x.get("evidence") or "",
                "kind": etype,
            }
            if x.get("source"):
                edge["source"] = x["source"]
            edges.append(edge)

    # 度数
    for e in edges:
        if e["from"] in nodes:
            nodes[e["from"]]["degree"] = nodes[e["from"]].get("degree", 0) + 1
        if e["to"] in nodes:
            nodes[e["to"]]["degree"] = nodes[e["to"]].get("degree", 0) + 1

    out_nodes = []
    for n in nodes.values():
        rec = {
            "id": n["id"],
            "type": n["type"],
            "name": n["name"],
            "doc_count": len(n.get("docs") or []),
            "degree": n.get("degree", 0),
        }
        for k in ("aliases", "event_type", "subtype", "kind", "time", "time_raw",
                  "trigger", "literals", "doc"):
            if n.get(k):
                rec[k] = sorted(n[k]) if isinstance(n[k], set) else n[k]
        # 机构节点挂上国别，方便可视化按国家聚合
        out_nodes.append(rec)

    # 机构 -> 国家 的映射表，可视化时直接可用
    org_country: dict[str, str] = {}
    for e in edges:
        if e.get("rel") == "辖属":
            org_country[e["from"]] = e["to"]

    # 事件间关系：子事件 + 时序 + 因果。这是「事件图谱」区别于「实体图谱+事件节点」的关键。
    ev_links = []
    composite_nodes: list[dict] = []
    try:
        from extract import event_links

        # 复合事件也建节点（子事件的起点），从 composites 字段取
        for row in rows:
            doc_id = row.get("doc_id") or ""
            for ci, c in enumerate(row.get("composites") or []):
                cid = f"{doc_id}#c{ci}"
                ctext = c.get("text") or ""
                if not ctext:
                    continue
                composite_nodes.append({
                    "id": cid,
                    "type": "事件",
                    "name": ctext,
                    "event_type": c.get("event_type") or "",
                    "subtype": c.get("subtype") or "",
                    "kind": c.get("kind") or "",
                    "level": "composite",
                    "doc": doc_id,
                    "docs": [doc_id],
                    "degree": 0,
                    "covers": c.get("covers") or [],
                })

        # 用 dict 建索引，避免原先的 O(n²) 查找
        by_doc_text = {(n.get("doc") or "", n["name"]): n["id"]
                       for n in out_nodes if n["type"] == "事件"}
        # 复合事件按 (doc, text) 也建索引
        comp_index = {(n["doc"], n["name"]): n["id"] for n in composite_nodes}

        # 优先读已有的关系文件（含 LLM 因果判定），没有再现场算规则
        links_path = DATA / "extract" / "event_links_kgv15.jsonl"
        if links_path.is_file():
            links = [json.loads(l) for l in links_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            print(f"  事件间关系读自 {links_path.name}（{len(links)} 条）")
        else:
            links, _stat = event_links.build_links(rows, use_llm=False)
        for e in links:
            d1, t1 = e["from_doc"], e["from_text"]
            d2, t2 = e["to_doc"], e["to_text"]
            if e["rel"] == "subevent":
                a = comp_index.get((d1, t1))
                b = by_doc_text.get((d2, t2))
            else:
                a = by_doc_text.get((d1, t1))
                b = by_doc_text.get((d2, t2))
            if not a or not b:
                continue
            ev_links.append({
                "from": a, "to": b, "rel": e["rel"],
                "subtype": e.get("subtype") or "",
                "kind": "event",
                "reason": e.get("reason") or "",
                "confidence": e.get("confidence") or "",
                "doc": d1,
                "time": e.get("from_time") or "",
            })
    except Exception as exc:  # 事件间关系是增强项，失败不影响主图
        ev_links = []
        composite_nodes = []
        print(f"  (事件间关系跳过：{type(exc).__name__}: {exc})")

    # 复合事件并入节点列表
    out_nodes.extend(composite_nodes)

    return {
        "meta": {
            "docs": len(docs),
            "nodes": len(out_nodes),
            "edges": len(edges) + len(ev_links),
            "event_links": len(ev_links),
            "events": len(events),
            "normalized": norm_stat,
            "affiliation_fixed": aff_stat,
            "inferred": inf_stat,
        },
        "nodes": out_nodes,
        "edges": edges + ev_links,
        "docs": docs,
        "events": events,
        "org_country": org_country,
    }


def stats(graph: dict) -> str:
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_type = Counter(n["type"] for n in nodes)
    by_rel = Counter(e["rel"] for e in edges)
    by_layer = Counter()

    # 静态/动态分层：实体间稳定关系属静态层，事件与论元属动态层
    STATIC = {"隶属", "辖属", "驻地", "部署方", "去向",
              "任职", "会晤", "搭载", "位于", "敌对"}
    for e in edges:
        if e.get("rel") == "报道":
            continue
        by_layer["静态" if e.get("rel") in STATIC else "动态"] += 1

    lines = [
        "=== 图谱规模 ===",
        f"节点 {len(nodes)}，边 {len(edges)}，事件 {graph['meta']['events']}，文档 {graph['meta']['docs']}",
        "",
        "节点类型：",
    ]
    for t, n in by_type.most_common():
        lines.append(f"  {t:6s} {n:5d}")
    lines.append("")
    lines.append("边类型（前 20）：")
    for r, n in by_rel.most_common(20):
        lines.append(f"  {r:10s} {n:5d}")

    # 连通性：孤立节点
    deg = {n["id"]: n["degree"] for n in nodes}
    iso = [i for i, d in deg.items() if d == 0]
    lines.append("")
    lines.append(f"孤立节点 {len(iso)} 个 ({len(iso)/max(len(nodes),1):.1%})")
    lines.append("")
    lines.append("静态/动态分层：")
    for k, v in by_layer.most_common():
        lines.append(f"  {k}层 {v:5d} 条边")

    # 国家级聚合效果
    oc = graph["org_country"]
    orgs = [n for n in nodes if n["type"] == "机构"]
    covered = [n for n in orgs if n["id"] in oc]
    lines.append(f"机构 {len(orgs)} 个，有国别归属 {len(covered)} 个 "
                 f"({len(covered)/max(len(orgs),1):.1%})")
    lines.append("")
    lines.append("")
    lines.append("国别聚合 Top 10（该国的机构数）：")
    cnt = Counter(oc.values())
    for c, n in cnt.most_common(10):
        lines.append(f"  {c:8s} {n:3d} 个机构")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="构建事件图谱")
    ap.add_argument("--src", default=str(DATA / "extract" / "kgv10_huanqiu_mil_2026-01.jsonl"))
    ap.add_argument("--out", default=str(DATA / "graph" / "sitrep.json"))
    ap.add_argument("--infer-country", action="store_true", help="启用国别推断补全")
    ap.add_argument("--stats", action="store_true", help="只打印统计，不写文件")
    a = ap.parse_args(argv)

    rows = load(Path(a.src))
    print(f"载入 {len(rows)} 篇", flush=True)
    graph = build(rows, infer_country=a.infer_country)

    if a.stats:
        print(stats(graph))
        return 0

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as g:
        json.dump(graph, g, ensure_ascii=False)
    print(stats(graph))
    size = out.stat().st_size
    print(f"\n已写 {out}（{size/1024/1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
