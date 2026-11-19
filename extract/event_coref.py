"""事件共指消解：把多篇报道里的同一事件合并为一个事件节点。

问题：同一件事（如「伊朗袭击以色列」）在 N 篇稿子里出现，现在会产生 N 个
事件节点，图谱上看起来像 N 件不同的事，无法回答「这件事被多少家媒体报道」。

做法：两阶段
1. **候选发现**：同类型 + 时间接近 + 论元重叠度高的跨文档事件配对
2. **判定**：论元重叠（施动方/受击方一致）、文本相似度、时间一致性三条打分

不做全量 LLM 判定（昂贵且不稳），先用规则把明显是同一件的合并；
存疑的留在候选池里，可人工确认。

    python3 -m extract.event_coref --src data/extract/kgv10_....jsonl --out /tmp/coref.jsonl
    python3 -m extract.event_coref --src ... --stats
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 判定「同一事件」时参与比对的角色。只用施动与被作用方，
# 不用发射物/战果这类细节，因为不同媒体的细节覆盖度差异大。
KEY_ROLES = (
    "打击方", "受击方", "拦截方", "袭击方", "控制方", "受控方",
    "部署方", "表态方", "甲方", "乙方", "援助方", "受援方",
    "打击对象", "控制对象", "受灾对象", "施动方", "主体",
)

# 时间容差：同一事件在不同稿件里的日期可能差 1 天
DAY_TOLERANCE = 1


def load(src: Path) -> list[dict]:
    rows = []
    for line in src.open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def norm(s: str) -> str:
    """文本归一：去标点空白，用于相似度比对。"""
    return re.sub(r"[\s，。、；：“”‘’（）()《》〈〉·—\-…!?！？]", "", s or "")


def bigrams(s: str) -> set[str]:
    t = norm(s)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else {t}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collect_events(rows: list[dict]) -> list[dict]:
    """整理成扁平事件列表，附上论元。"""
    out = []
    for row in rows:
        doc_id = row.get("doc_id") or ""
        evs = {e["text"]: e for e in (row.get("events") or [])}
        argmap: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for rel in row.get("relations") or []:
            if rel.get("head") in evs and rel["name"] != "包含":
                argmap[rel["head"]].append((rel["name"], rel.get("tail") or ""))
        for text, ev in evs.items():
            out.append({
                "eid": f"{doc_id}#{hash(text) & 0xFFFFFF:06x}",
                "doc": doc_id,
                "doc_title": row.get("title") or "",
                "text": text,
                "type": ev.get("event_type") or "",
                "subtype": ev.get("subtype") or "",
                "trigger": ev.get("trigger") or "",
                "time": (ev.get("time_norm") or "")[:10],
                "time_raw": ev.get("raw_time") or "",
                "args": argmap.get(text, []),
                "bigrams": bigrams(text),
            })
    return out


def key_actors(ev: dict) -> set[str]:
    return {v for r, v in ev["args"] if r in KEY_ROLES and v}


def day_gap(a: str, b: str) -> int:
    """两个 YYYY-MM-DD 相差天数，任一为空返回 999。"""
    if not a or not b:
        return 999
    try:
        from datetime import date
        ya, ma, da = (int(x) for x in a.split("-")[:3])
        yb, mb, db = (int(x) for x in b.split("-")[:3])
        return abs((date(ya, ma, da) - date(yb, mb, db)).days)
    except Exception:
        return 999


def score_pair(a: dict, b: dict) -> tuple[float, dict]:
    """给两个事件打分，返回 (分数, 明细)。分数 >= 阈值判为同一事件。"""
    if a["type"] != b["type"]:
        return 0.0, {}
    if a["subtype"] and b["subtype"] and a["subtype"] != b["subtype"]:
        return 0.0, {}

    gap = day_gap(a["time"], b["time"])
    if gap > DAY_TOLERANCE:
        return 0.0, {}

    text_sim = jaccard(a["bigrams"], b["bigrams"])
    aa, ba = key_actors(a), key_actors(b)
    actor_sim = jaccard(aa, ba) if (aa or ba) else 0.0

    # 施动方必须一致，受击方可以一方更具体（「美以联合打击」vs「美国打击」）
    def side(ev: dict, roles: set[str]) -> set[str]:
        return {v for r, v in ev["args"] if r in roles and v}

    A_SIDE = {"打击方", "拦截方", "袭击方", "控制方", "部署方",
              "表态方", "甲方", "援助方", "施动方", "主体"}
    agent_a, agent_b = side(a, A_SIDE), side(b, A_SIDE)
    same_agent = bool(agent_a) and agent_a == agent_b
    # 受击方有交集即可（不同稿件详略不同）
    tgt_a, tgt_b = side(a, {"受击方", "打击对象", "控制对象", "受灾对象"}), \
                   side(b, {"受击方", "打击对象", "控制对象", "受灾对象"})
    target_overlap = bool(tgt_a & tgt_b) if (tgt_a or tgt_b) else False

    # 加权：施动方一致权重最高；受击方有交集加分；文本相似辅助
    s = 0.0
    if same_agent:
        s += 0.50
        if target_overlap:
            s += 0.18
    else:
        s += actor_sim * 0.40
    s += text_sim * 0.30
    if gap == 0:
        s += 0.08
    elif gap <= DAY_TOLERANCE:
        s += 0.04

    return min(s, 1.0), {
        "text_sim": round(text_sim, 3),
        "actor_sim": round(actor_sim, 3),
        "same_agent": same_agent,
        "target_overlap": target_overlap,
        "gap": gap if gap < 999 else None,
    }


def build_clusters(events: list[dict], threshold: float = 0.62) -> list[list[dict]]:
    """按类型分组后两两比对，用并查集合并。"""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_type[e["type"]].append(e)

    parent: dict[str, str] = {e["eid"]: e["eid"] for e in events}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    pairs = []
    for typ, group in by_type.items():
        # 按时间排序，只比邻近的，避免 O(n^2)
        group = sorted(group, key=lambda e: e["time"] or "")
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if day_gap(a["time"], b["time"]) > DAY_TOLERANCE:
                    continue
                if a["doc"] == b["doc"]:
                    continue
                s, detail = score_pair(a, b)
                if s >= threshold:
                    union(a["eid"], b["eid"])
                    pairs.append((s, a["eid"], b["eid"], detail))

    clusters: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        clusters[find(e["eid"])].append(e)
    return list(clusters.values()), pairs


def merge_cluster(cluster: list[dict]) -> dict:
    """把一个簇合并成一条事件。

    事件文本取最长的那条（信息最全），论元取并集，
    文档列表与报道数记下来，用于「被多少家媒体报道」。
    """
    cluster = sorted(cluster, key=lambda e: -len(e["text"]))
    main = cluster[0]
    docs = []
    for e in cluster:
        if e["doc"] not in docs:
            docs.append(e["doc"])

    # 论元按 (角色, 值) 去重，保留首次出现
    args: list[tuple[str, str]] = []
    seen = set()
    for e in cluster:
        for pair in e["args"]:
            if pair in seen:
                continue
            seen.add(pair)
            args.append(pair)

    return {
        "eid": main["eid"],
        "text": main["text"],
        "event_type": main["type"],
        "subtype": main["subtype"],
        "trigger": main["trigger"],
        "time": main["time"],
        "time_raw": main["time_raw"],
        "arguments": [{"role": r, "text": t} for r, t in args],
        "docs": docs,
        "doc_titles": [e["doc_title"] for e in cluster],
        "mentions": len(cluster),
        "variants": [e["text"] for e in cluster],
    }


def run(src: Path, threshold: float) -> tuple[list[dict], dict]:
    rows = load(src)
    events = collect_events(rows)
    clusters, pairs = build_clusters(events, threshold)
    merged = [merge_cluster(c) for c in clusters]
    stat = {
        "docs": len(rows),
        "events_raw": len(events),
        "events_merged": len(merged),
        "clusters_multi": sum(1 for c in clusters if len(c) > 1),
        "merged_away": len(events) - len(merged),
        "pairs": len(pairs),
    }
    return merged, stat


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="事件共指消解")
    ap.add_argument("--src", default=str(DATA / "extract" / "kgv10_huanqiu_mil_2026-01.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--threshold", type=float, default=0.62)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--show", type=int, default=0, help="打印前 N 个多报道事件")
    a = ap.parse_args(argv)

    merged, stat = run(Path(a.src), a.threshold)
    print(json.dumps(stat, ensure_ascii=False, indent=2))

    multi = [m for m in merged if m["mentions"] > 1]
    multi.sort(key=lambda m: -m["mentions"])
    print(f"\n被多篇报道的事件 {len(multi)} 个")
    if a.show:
        for m in multi[: a.show]:
            print(f"\n[{m['mentions']} 篇] {m['text'][:60]}")
            print(f"  类型 {m['event_type']}·{m['subtype']}　时间 {m['time']}")
            print(f"  报道: {', '.join(t[:24] for t in m['doc_titles'][:4])}")
            arg_txt = ", ".join(
                "{}={}".format(x["role"], x["text"][:16]) for x in m["arguments"][:6]
            )
            print(f"  论元: {arg_txt}")

    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as g:
            for m in merged:
                g.write(json.dumps(m, ensure_ascii=False) + "\n")
        print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
