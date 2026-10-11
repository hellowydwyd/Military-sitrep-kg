"""把 aliases.json 写进图谱：保留 mention，补 canonical_id。

python -m align.apply
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from align.normalize import normalize
from graph.proximity import next_in_time_edges
from graph.store import load_graph, save_graph

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _slug(canonical: str, *, prefix: str = "actor") -> str:
    return prefix + ":" + "".join(ch if ch.isalnum() else "_" for ch in canonical)[:40]


def load_alias_file(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_groups(path: Path) -> list[dict]:
    return load_alias_file(path).get("groups") or []


def refresh_next_in_time(graph: dict) -> dict:
    rest = [e for e in (graph.get("edges") or []) if e.get("type") != "NEXT_IN_TIME"]
    rest.extend(next_in_time_edges(graph.get("events") or []))
    graph["edges"] = rest
    return graph


def mention_to_canon(groups: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for g in groups:
        rec = {"id": g["id"], "canonical": g["canonical"]}
        for a in g.get("aliases") or []:
            out[a] = rec
            n = normalize(a)
            if n and n not in out:
                out[n] = rec
        out[g["canonical"]] = rec
    return out


def apply_aliases(graph: dict, groups: list[dict]) -> dict:
    amap = mention_to_canon(groups)
    actor_nodes: dict[str, dict] = {g["id"]: {"id": g["id"], "canonical": g["canonical"], "aliases": g.get("aliases") or []} for g in groups}

    n_hit = 0
    n_miss = 0
    for ev in graph.get("events") or []:
        aligned = []
        for m in ev.get("actors") or []:
            hit = amap.get(m) or amap.get(normalize(m) or "")
            if hit:
                n_hit += 1
                aligned.append({"mention": m, "canonical": hit["canonical"], "id": hit["id"]})
            else:
                n_miss += 1
                sid = _slug(m, prefix="actor")
                node = actor_nodes.get(sid)
                if node is None:
                    actor_nodes[sid] = {"id": sid, "canonical": m, "aliases": [m], "singleton": True}
                elif m not in (node.get("aliases") or []) and node.get("canonical") != m:
                    sid = f"actor:s_{len(actor_nodes)}"
                    actor_nodes[sid] = {"id": sid, "canonical": m, "aliases": [m], "singleton": True}
                aligned.append({"mention": m, "canonical": m, "id": sid})
        ev["actors_aligned"] = aligned

    edges = [e for e in graph.get("edges") or [] if e.get("type") != "PARTICIPANT"]
    for ev in graph.get("events") or []:
        for a in ev.get("actors_aligned") or []:
            edges.append(
                {
                    "type": "PARTICIPANT",
                    "from": ev["event_id"],
                    "to": a["id"] or a["mention"],
                    "mention": a["mention"],
                    "canonical": a["canonical"],
                }
            )
    graph["edges"] = edges
    graph["actors"] = list(actor_nodes.values())
    st = dict(graph.get("align_stats") or {})
    st["actor_mentions_linked"] = n_hit
    st["actor_mentions_unlinked"] = n_miss
    graph["align_stats"] = st
    return graph


def apply_location_aliases(graph: dict, groups: list[dict]) -> dict:
    amap = mention_to_canon(groups)
    loc_nodes: dict[str, dict] = {
        g["id"]: {"id": g["id"], "canonical": g["canonical"], "aliases": g.get("aliases") or []}
        for g in groups
    }

    n_hit = 0
    n_miss = 0
    for ev in graph.get("events") or []:
        aligned = []
        for m in ev.get("locations") or []:
            hit = amap.get(m) or amap.get(normalize(m) or "")
            if hit:
                n_hit += 1
                aligned.append({"mention": m, "canonical": hit["canonical"], "id": hit["id"]})
            else:
                n_miss += 1
                sid = _slug(m, prefix="loc")
                node = loc_nodes.get(sid)
                if node is None:
                    loc_nodes[sid] = {"id": sid, "canonical": m, "aliases": [m], "singleton": True}
                elif m not in (node.get("aliases") or []) and node.get("canonical") != m:
                    sid = f"loc:s_{len(loc_nodes)}"
                    loc_nodes[sid] = {"id": sid, "canonical": m, "aliases": [m], "singleton": True}
                aligned.append({"mention": m, "canonical": m, "id": sid})
        ev["locations_aligned"] = aligned

    edges = [e for e in graph.get("edges") or [] if e.get("type") != "LOCATED_AT"]
    for ev in graph.get("events") or []:
        for loc in ev.get("locations_aligned") or []:
            edges.append(
                {
                    "type": "LOCATED_AT",
                    "from": ev["event_id"],
                    "to": loc["id"] or loc["mention"],
                    "mention": loc["mention"],
                    "canonical": loc["canonical"],
                }
            )
    graph["edges"] = edges
    graph["locations"] = list(loc_nodes.values())
    st = dict(graph.get("align_stats") or {})
    st["location_mentions_linked"] = n_hit
    st["location_mentions_unlinked"] = n_miss
    graph["align_stats"] = st
    return graph


def coverage_report(graph: dict) -> str:
    st = graph.get("align_stats") or {}
    raw, canon = Counter(), Counter()
    mention_id: dict[str, str] = {}
    for ev in graph.get("events") or []:
        for a in ev.get("actors_aligned") or []:
            m = a.get("mention") or ""
            raw[m] += 1
            key = a.get("id") or a.get("mention") or ""
            canon[key] += 1
            if m:
                mention_id[m] = a.get("canonical") or m
    hit, miss = st.get("actor_mentions_linked", 0), st.get("actor_mentions_unlinked", 0)
    tot = hit + miss or 1
    n_raw, n_canon = len(raw), len(canon)
    n_clusters = sum(1 for a in graph.get("actors") or [] if not a.get("singleton"))
    n_nodes = len(graph.get("actors") or [])
    lines = [
        "# 全图 Actor 对齐实验",
        "",
        "流程：高频不截断 → 程序造候选（子串∪编辑∪X方）→ 国名子串自动 related、X方自动 same_as → LLM 只判剩余对 → same_as 并查集 → 拆开「国防部」类泛称枢纽。",
        "mention 原文保留；`actors_aligned` / `PARTICIPANT.canonical` 为实验层。Location 另见 `locations_aligned`。",
        "",
        f"- mention 落在别名簇：**{hit}/{hit+miss}**（{100*hit/tot:.1f}%）；其余为单例节点（如「俄军」无同义对）",
        f"- 原文 Actor 种类 **{n_raw}** → 对齐后节点 **{n_canon}**（{n_clusters} 个合并簇 + 单例）",
        f"- 图上 Actor 节点 **{n_nodes}**",
        "",
        "## 抽查",
        "",
    ]
    probes = [
        ("以军", "以色列国防军"),
        ("日本", "日本防卫省"),
        ("美国", "美军"),
        ("伊朗", "伊朗军方"),
        ("伊朗军方", "伊朗伊斯兰革命卫队"),
        ("俄方", "俄罗斯"),
        ("驻日美军", "美军"),
        ("伊朗方面", "伊朗方向"),
    ]
    for a, b in probes:
        ca, cb = mention_id.get(a, "（图中无）"), mention_id.get(b, "（图中无）")
        same = ca == cb and ca != "（图中无）"
        lines.append(f"- `{a}` → **{ca}**；`{b}` → **{cb}** {'（已合并）' if same else '（未合并）'}")
    lines += ["", "## 对齐后高频节点", ""]
    for k, n in canon.most_common(20):
        lines.append(f"- {n} `{k}`")
    lines += [
        "",
        "## 仍存在的问题",
        "",
        "- 泛称枢纽：LLM 曾把「国防部」串进美/俄/以；已程序拆开。",
        "- `驻日美军` 与 `美军` 现为 related（驻地部队）。",
        "- `伊朗方面` / `伊朗方向` 现为 related。",
        "- `美军` 与 `美国军方`、`俄武装部队` 与 `俄联邦武装力量` 仍分开。",
        "",
    ]
    return "\n".join(lines)


def location_coverage_report(graph: dict, *, n_next_before: int | None = None, title: str = "Location 对齐实验") -> str:
    st = graph.get("align_stats") or {}
    raw, canon = Counter(), Counter()
    mention_id: dict[str, str] = {}
    for ev in graph.get("events") or []:
        for a in ev.get("locations_aligned") or []:
            m = a.get("mention") or ""
            raw[m] += 1
            key = a.get("id") or a.get("mention") or ""
            canon[key] += 1
            if m:
                mention_id[m] = a.get("canonical") or m
    hit = st.get("location_mentions_linked", 0)
    miss = st.get("location_mentions_unlinked", 0)
    tot = hit + miss or 1
    n_raw, n_canon = len(raw), len(canon)
    n_clusters = sum(1 for a in graph.get("locations") or [] if not a.get("singleton"))
    n_nodes = len(graph.get("locations") or [])
    n_next = sum(1 for e in graph.get("edges") or [] if e.get("type") == "NEXT_IN_TIME")
    lines = [
        f"# {title}",
        "",
        "流程：地点全文 → 程序造候选（去市全等 ∪ 短串在长串 ∪ 共享词干 ∪ 编辑相近）→ 别名后缀自动 same_as、包含/海域/机场/省自动 related → LLM 只判剩余对 → same_as 并查集。并列清单不合点。",
        "mention 原文保留；`locations_aligned` / `LOCATED_AT.canonical` 为实验层。不改 Actor。",
        "",
        f"- mention 落在别名簇：**{hit}/{hit+miss}**（{100*hit/tot:.1f}%）；其余为单例",
        f"- 原文 Location 种类 **{n_raw}** → 对齐后节点 **{n_canon}**（{n_clusters} 个合并簇 + 单例）",
        f"- 图上 Location 节点 **{n_nodes}**",
        f"- NEXT_IN_TIME **{n_next_before if n_next_before is not None else '?'}** → **{n_next}**（按 aligned id 重算）",
        "",
        "## 抽查",
        "",
    ]
    probes = [
        ("基辅", "基辅市", True),
        ("基辅", "乌克兰首都基辅", True),
        ("基辅", "乌克兰首都基辅市", True),
        ("中东", "中东地区", True),
        ("阿布祖胡尔军用机场", "叙利亚阿布祖胡尔空军基地", True),
        ("华盛顿", "美国华盛顿", True),
        ("加沙", "加沙地带", True),
        ("台岛以东", "台岛以东海域", True),
        ("中东", "中东海域", False),
        ("黎巴嫩", "黎巴嫩南部", False),
        ("白宫", "白宫战情室", False),
        ("纳季兰", "纳季兰机场", False),
        ("霍尔木兹海峡", "曼德海峡", False),
        ("科威特", "科威特境内", False),
        ("布什尔市", "布什尔省", False),
        ("加沙城", "加沙地带", False),
        ("中东", "中东地区国家", False),
    ]
    for a, b, want_same in probes:
        ca, cb = mention_id.get(a, "（图中无）"), mention_id.get(b, "（图中无）")
        same = ca == cb and ca != "（图中无）"
        ok = same == want_same and ca != "（图中无）" and cb != "（图中无）"
        flag = "通过" if ok else "未通过"
        merge = "已合并" if same else "未合并"
        expect = "应合并" if want_same else "不应合并"
        if ca == "（图中无）" or cb == "（图中无）":
            flag = "跳过"
        lines.append(f"- `{a}` → **{ca}**；`{b}` → **{cb}** （{merge}，{expect}）**{flag}**")
    lines += ["", "## 对齐后高频节点", ""]
    for k, n in canon.most_common(20):
        lines.append(f"- {n} `{k}`")
    lines += [
        "",
        "## 仍存在的问题",
        "",
        "- 「社交媒体」「该基地」等噪声仍作为地点 mention 留下，不合点。",
        "- 并列清单保持单例；城市与省/南部/战情室应为 related，不应 same_as。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="把别名表写进 sitrep.json")
    p.add_argument("--graph", default=str(DATA / "graph" / "sitrep.json"))
    p.add_argument("--aliases", default="")
    p.add_argument("--kind", choices=("actors", "locations", "auto"), default="auto")
    p.add_argument("--report", default="")
    p.add_argument("--refresh-next", action="store_true", help="按对齐后的 id 重算 NEXT_IN_TIME")
    args = p.parse_args(argv)
    if not args.aliases:
        args.aliases = str(
            ROOT / "align" / ("loc_aliases.json" if args.kind == "locations" else "aliases.json")
        )
    data = load_alias_file(Path(args.aliases))
    kind = args.kind if args.kind != "auto" else (data.get("kind") or "actors")
    if not args.report:
        args.report = str(
            DATA / "reports" / ("align_loc_full.md" if kind == "locations" else "align_full.md")
        )
    groups = data.get("groups") or []
    graph = load_graph(Path(args.graph))
    n_next_before = sum(1 for e in graph.get("edges") or [] if e.get("type") == "NEXT_IN_TIME")
    if kind == "locations":
        graph = apply_location_aliases(graph, groups)
        # B0 默认不写 NEXT；已有邻近边（B1/B2）或显式要求时才按 id 重算。
        if args.refresh_next or n_next_before > 0:
            graph = refresh_next_in_time(graph)
        title = "全图 Location 对齐实验" if "b2" not in Path(args.graph).name else "B2 切片 Location 对齐实验"
        text = location_coverage_report(graph, n_next_before=n_next_before, title=title)
    else:
        graph = apply_aliases(graph, groups)
        if args.refresh_next:
            graph = refresh_next_in_time(graph)
        text = coverage_report(graph)
    save_graph(graph, Path(args.graph))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    print(f"已写回 {args.graph}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
