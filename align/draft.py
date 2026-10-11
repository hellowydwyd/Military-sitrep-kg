"""从高频 Actor 候选 + LLM 判别生成 aliases.yaml 草稿（不写回图）。

python -m align.draft
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from align.cluster import UnionFind, pair_key
from align.loc import (
    LOC_EXAMPLES,
    LOC_SYSTEM,
    is_compound,
    loc_candidate_pairs,
    loc_prelabel,
    refine_loc_cluster,
)
from align.methods import COUNTRYISH, FANG, candidate_pairs, llm_label_pairs
from align.stats import load_graph, mention_counts, top_names

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _slug(canonical: str, *, prefix: str = "actor") -> str:
    return prefix + ":" + "".join(ch if ch.isalnum() else "_" for ch in canonical)[:40]


FANG_NAMES = set(FANG)

COUNTRY_CANON = set(FANG.values()) | COUNTRYISH


GENERIC_HUBS = {
    "国防部",
    "军方",
    "军队",
    "武装部队",
    "发言人",
    "编队",
    "无人机",
    "该人",
    "该人员",
    "政府军",
    "一架无人机",
    "部队",
}

_COUNTRY_HEAD = (
    ("俄罗斯", "俄罗斯"),
    ("以色列", "以色列"),
    ("乌克兰", "乌克兰"),
    ("美国", "美国"),
    ("日本", "日本"),
    ("伊朗", "伊朗"),
    ("中国", "中国"),
    ("韩国", "韩国"),
    ("也门", "也门"),
    ("黎巴嫩", "黎巴嫩"),
    ("沙特", "沙特"),
    ("俄", "俄罗斯"),
    ("美", "美国"),
    ("以", "以色列"),
    ("乌", "乌克兰"),
    ("日", "日本"),
    ("中", "中国"),
    ("韩", "韩国"),
)


def country_tag(name: str) -> str | None:
    for prefix, tag in _COUNTRY_HEAD:
        if name.startswith(prefix):
            return tag
    return None


def drop_composites(members: list[str]) -> list[str]:
    """「以色列与黎巴嫩真主党」这类并列串不并进其中一方。"""
    drop: set[str] = set()
    for a in members:
        for b in members:
            if a != b and a in b and ("与" in b or "和" in b):
                drop.add(b)
    return [m for m in members if m not in drop]


def split_generic_hubs(members: list[str]) -> list[list[str]]:
    """「国防部」把美/俄/以防部串成一簇时拆开；泛称本身不合节点。"""
    members = drop_composites(members)
    if not any(m in GENERIC_HUBS for m in members):
        return [members] if len(members) > 1 else []
    rest = [m for m in members if m not in GENERIC_HUBS]
    buckets: dict[str, list[str]] = {}
    untagged: list[str] = []
    for n in rest:
        tag = country_tag(n)
        if tag:
            buckets.setdefault(tag, []).append(n)
        else:
            untagged.append(n)
    if len(buckets) == 1:
        buckets[next(iter(buckets))].extend(untagged)
        untagged = []
    parts = list(buckets.values())
    if untagged:
        parts.append(untagged)
    return [p for p in parts if len(p) > 1]


def pick_canonical(members: list[str], counts: Counter) -> str:
    """少用「X方」当规范名；国名优先；再按频次、全称长度。"""

    def key(n: str):
        fang = 1 if n in FANG_NAMES else 0
        country = 0 if n in COUNTRY_CANON else 1
        return (fang, country, -counts[n], -len(n), n)

    return sorted(members, key=key)[0]


def pick_loc_canonical(members: list[str], counts: Counter) -> str:
    """高频接近时取更短常用名（基辅优于基辅市）。"""
    top = max(counts[m] for m in members) if members else 0
    thresh = max(3, top * 0.5) if top else 0
    cands = [m for m in members if counts[m] >= thresh] or list(members)

    def key(n: str):
        return (1 if is_compound(n) else 0, len(n), -counts[n], n)

    return sorted(cands, key=key)[0]


def drop_compound_places(members: list[str]) -> list[str]:
    kept = [m for m in members if not is_compound(m)]
    return kept if len(kept) > 1 else []


def yaml_dump(groups: list[dict], related: list[dict], *, kind: str = "actors") -> str:
    lines = [
        "# 实体别名草稿（LLM 判别，未经人工全量审定）",
        "# same_as 才会写入 canonical；related 不合节点，仅备查。",
        "# 生成：python -m align.draft",
        "",
        f"{kind}:",
    ]
    for g in groups:
        lines.append(f"  - id: {g['id']}")
        lines.append(f"    canonical: {json.dumps(g['canonical'], ensure_ascii=False)}")
        lines.append("    aliases:")
        for a in g["aliases"]:
            lines.append(f"      - {json.dumps(a, ensure_ascii=False)}")
        lines.append("")
    lines.append("related_not_merged:")
    for r in related:
        lines.append(f"  - [{json.dumps(r['a'], ensure_ascii=False)}, {json.dumps(r['b'], ensure_ascii=False)}]")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="生成 aliases.yaml 草稿")
    p.add_argument("--graph", default=str(DATA / "graph" / "sitrep.json"))
    p.add_argument("--kind", choices=("actors", "locations"), default="actors")
    p.add_argument("--top", type=int, default=80, help="0 表示不截断，用 --min-freq")
    p.add_argument("--min-freq", type=int, default=1)
    p.add_argument("--all", action="store_true", help="不截 top（min-freq=1）")
    p.add_argument("--out", default="")
    p.add_argument("--review", default="")
    args = p.parse_args(argv)

    if not args.out:
        args.out = str(ROOT / "align" / ("loc_aliases.yaml" if args.kind == "locations" else "aliases.yaml"))
    if not args.review:
        args.review = str(
            DATA / "reports" / ("align_loc_draft.md" if args.kind == "locations" else "align_aliases_draft.md")
        )

    if args.all:
        args.top = 0
        args.min_freq = 1
    graph = load_graph(Path(args.graph))
    counts = mention_counts(graph, args.kind)
    if args.top:
        names = top_names(counts, n=args.top, min_freq=args.min_freq)
    else:
        names = [k for k, v in counts.most_common() if v >= args.min_freq]
    prefix = "loc" if args.kind == "locations" else "actor"
    if args.kind == "locations":
        cand = loc_candidate_pairs(names)
        cache = ROOT / "align" / "cache" / "loc_pair_labels.json"
        print(f"Location {len(names)}（min_freq={args.min_freq}），候选 {len(cand)} 对", flush=True)
        labeled = llm_label_pairs(
            cand,
            cache_path=cache,
            system=LOC_SYSTEM,
            user_examples=LOC_EXAMPLES,
            prelabel=loc_prelabel,
        )
    else:
        cand = candidate_pairs(names)
        cache = ROOT / "align" / "cache" / "pair_labels.json"
        print(f"Actor {len(names)}（min_freq={args.min_freq}），候选 {len(cand)} 对", flush=True)
        labeled = llm_label_pairs(cand, cache_path=cache)
    by = {pair_key(x["a"], x["b"]): x["label"] for x in labeled}
    # 候选里模型没回的当 related（不合）
    same = [pair_key(a, b) for a, b in cand if by.get(pair_key(a, b)) == "same_as"]
    related = [
        {"a": a, "b": b}
        for a, b in cand
        if by.get(pair_key(a, b), "related") == "related"
    ]
    distinct_n = sum(1 for a, b in cand if by.get(pair_key(a, b)) == "distinct")
    missing = [pair_key(a, b) for a, b in cand if pair_key(a, b) not in by]

    uf = UnionFind(names)
    for a, b in same:
        uf.union(a, b)
    groups = []
    for members in uf.clusters().values():
        parts = [refine_loc_cluster(members)] if args.kind == "locations" else split_generic_hubs(members)
        for part in parts:
            if len(part) < 2:
                continue
            canon = pick_loc_canonical(part, counts) if args.kind == "locations" else pick_canonical(part, counts)
            groups.append(
                {
                    "id": _slug(canon, prefix=prefix),
                    "canonical": canon,
                    "aliases": sorted(part, key=lambda n: (-counts[n], n)),
                    "freq_sum": sum(counts[x] for x in part),
                }
            )
    groups.sort(key=lambda g: -g["freq_sum"])
    seen_id: dict[str, int] = {}
    for g in groups:
        base = g["id"]
        n = seen_id.get(base, 0)
        seen_id[base] = n + 1
        if n:
            g["id"] = f"{base}_{n}"

    yaml_path = Path(args.out)
    yaml_path.write_text(yaml_dump(groups, related[:120], kind=args.kind), encoding="utf-8")
    json_path = yaml_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "kind": args.kind,
                "groups": groups,
                "labeled": labeled,
                "missing": [f"{a}={b}" for a, b in missing],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    title = "Location" if args.kind == "locations" else "Actor"
    hint = (
        "请重点看：基辅是否合并；中东≠中东海域；纳季兰≠机场；长描述/并列清单不要并进城市。"
        if args.kind == "locations"
        else "请重点看：国家是否被并进军队、美军/俄军是否被并、以军是否收到 IDF。"
    )
    cluster_head = (
        "## same_as 簇"
        if args.kind == "locations"
        else "## same_as 簇（泛称枢纽已拆：国防部/编队/发言人等）"
    )
    md = [
        "# 别名表草稿（未写回图）",
        "",
        f"- 输入：{title} **{len(names)}**（min_freq={args.min_freq}），候选 **{len(cand)}** 对",
        f"- 判别：same_as **{len(same)}**，related **{len(related)}**，distinct **{distinct_n}**，无回包 **{len(missing)}**",
        f"- 合并簇 **{len(groups)}** → `{yaml_path}`",
        f"- {hint}",
        "",
        cluster_head,
        "",
    ]
    thresh = 1 if args.kind == "locations" else 5
    big = [g for g in groups if g["freq_sum"] >= thresh]
    for g in big:
        alias = "、".join(f"{n}({counts[n]})" for n in g["aliases"])
        md.append(f"- **{g['canonical']}** `{g['id']}`：{alias}")
    small = len(groups) - len(big)
    if small:
        md.append(f"- （另有频次和 <{thresh} 的簇 **{small}** 个，见 yaml）")
    md += ["", "## related（不合节点，摘录）", ""]
    for r in related[:40]:
        md.append(f"- {r['a']} ↔ {r['b']}")
    if len(related) > 40:
        md.append(f"- … 共 {len(related)} 对")
    md.append("")
    review = Path(args.review)
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md[:80]))
    print(f"\nYAML {yaml_path}")
    print(f"审阅 {review}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
