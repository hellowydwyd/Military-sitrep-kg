"""对比几种实体对齐方法。python -m align.compare"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from align.cluster import UnionFind, pair_key
from align.methods import (
    candidate_pairs,
    method_contain,
    method_edit,
    method_exact_norm,
    method_hybrid_rule,
    method_llm,
)
from align.stats import load_graph, mention_counts, top_names

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GOLD = Path(__file__).resolve().parent / "gold_pairs.json"


def load_gold() -> list[dict]:
    return json.loads(GOLD.read_text(encoding="utf-8"))


def names_from_gold(gold: list[dict], universe: set[str]) -> list[str]:
    s = set()
    for g in gold:
        if g["a"] in universe and g["b"] in universe:
            s.add(g["a"])
            s.add(g["b"])
    return sorted(s)


def score(pred: set[tuple[str, str]], gold: list[dict]) -> dict:
    same, other = set(), set()
    for g in gold:
        k = pair_key(g["a"], g["b"])
        if g["label"] == "same_as":
            same.add(k)
        else:
            other.add(k)
    # 只评金标里出现的对
    pred_g = {p for p in pred if p in same or p in other}
    tp = pred_g & same
    fp = pred_g & other
    fn = same - pred
    prec = len(tp) / len(pred_g) if pred_g else 1.0
    rec = len(tp) / len(same) if same else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "tp": sorted(f"{a}={b}" for a, b in tp),
        "fp": sorted(f"{a}={b}" for a, b in fp),
        "fn": sorted(f"{a}={b}" for a, b in fn),
        "n_pred_on_gold": len(pred_g),
        "n_same_gold": len(same),
    }


def cluster_preview(names: list[str], pred: set[tuple[str, str]]) -> list[str]:
    uf = UnionFind(names)
    for a, b in pred:
        if a in uf.parent and b in uf.parent:
            uf.union(a, b)
    lines = []
    for members in sorted(uf.clusters().values(), key=len, reverse=True):
        lines.append(" / ".join(members))
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="对比实体对齐方法")
    p.add_argument("--graph", default=str(DATA / "graph" / "sitrep.json"))
    p.add_argument("--top", type=int, default=80)
    p.add_argument("--llm", action="store_true")
    p.add_argument("--out", default=str(DATA / "reports" / "align_compare.md"))
    args = p.parse_args(argv)

    graph = load_graph(Path(args.graph))
    counts = mention_counts(graph, "actors")
    names80 = top_names(counts, n=args.top, min_freq=1)
    universe = set(counts)
    gold = [g for g in load_gold() if g["a"] in universe and g["b"] in universe]
    gold_names = names_from_gold(gold, universe)

    gold_pairs = [pair_key(g["a"], g["b"]) for g in gold]
    same_gold = {pair_key(g["a"], g["b"]) for g in gold if g["label"] == "same_as"}
    cand_gold = set(candidate_pairs(gold_names))
    cand_hit = same_gold & cand_gold
    methods: dict[str, set[tuple[str, str]]] = {
        "exact_norm（去引号全等）": method_exact_norm(names80),
        "contain（子串即合，宽松）": method_contain(names80),
        "edit≥0.75（编辑相似）": method_edit(names80, min_ratio=0.75),
        "hybrid_rule（禁国家被机构吞并）": method_hybrid_rule(names80),
    }
    print(
        f"金标 {len(gold)} 对（same_as {len(same_gold)}）；"
        f"候选生成召回 same_as {len(cand_hit)}/{len(same_gold)}；"
        f"top{args.top} 候选 {len(candidate_pairs(names80))}",
        flush=True,
    )
    try:
        methods["llm_judge（直接判金标对）"] = method_llm(gold_pairs)
    except Exception as exc:
        print(f"LLM 失败: {exc}", flush=True)
        methods["llm_judge（直接判金标对）"] = set()

    extra_llm = None
    if args.llm:
        cand = candidate_pairs(names80)
        print(f"对 top{args.top} 候选跑 LLM ({len(cand)} 对)…", flush=True)
        extra_llm = method_llm(cand)

    lines = [
        "# 实体对齐方法对比",
        "",
        f"- 图：`{args.graph}`",
        f"- 评测：`align/gold_pairs.json` 中图内可见的 **{len(gold)}** 对（same_as 应合；related/distinct 不应合）",
        f"- 规则方法在 **top {args.top}** Actor 上出合并对，再映射到金标对打分",
        f"- 子串∪编辑 对 same_as 的候选召回：**{len(cand_hit)}/{len(same_gold)}**（漏了就进不了「先筛再判」）",
        f"- LLM 本表是直接判金标对，测的是裁判能力，不是候选召回",
        "",
        "| 方法 | P | R | F1 | 金标上预测合 |",
        "|------|---|---|----|--------------|",
    ]
    reports = {}
    for name, pred in methods.items():
        sc = score(pred, gold)
        reports[name] = (pred, sc)
        lines.append(
            f"| {name} | {sc['precision']} | {sc['recall']} | {sc['f1']} | {sc['n_pred_on_gold']} |"
        )
    lines += ["", "## 对错明细", ""]
    for name, (pred, sc) in reports.items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- 命中 same_as：{', '.join(sc['tp']) or '无'}")
        lines.append(f"- 误合 related/distinct：{', '.join(sc['fp']) or '无'}")
        lines.append(f"- 漏合：{', '.join(sc['fn']) or '无'}")
        lines.append("")

    lines.append("## top80 上各方法合并簇（只列簇大小≥2）")
    lines.append("")
    for name, (pred, _) in reports.items():
        preview = cluster_preview(names80, pred)
        lines.append(f"### {name}")
        lines.append("")
        if not preview:
            lines.append("- （无多成员簇）")
        for row in preview[:25]:
            lines.append(f"- {row}")
        if len(preview) > 25:
            lines.append(f"- … 共 {len(preview)} 簇")
        lines.append("")

    if extra_llm is not None:
        sc = score(extra_llm, gold)
        lines.append("## top80 全候选 LLM")
        lines.append("")
        lines.append(f"- 金标 P/R/F1 = {sc['precision']} / {sc['recall']} / {sc['f1']}")
        lines.append(f"- 误合：{', '.join(sc['fp']) or '无'}")
        lines.append("")
        for row in cluster_preview(names80, extra_llm)[:30]:
            lines.append(f"- {row}")
        lines.append("")

    lines.append("## 频次（top 30 Actor）")
    lines.append("")
    for k, v in counts.most_common(30):
        lines.append(f"- {v} `{k}`")
    lines.append("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out.read_text(encoding="utf-8")[:2500])
    print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
