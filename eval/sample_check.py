"""论元与关系的人工抽检工具。

从抽取结果里随机抽样，输出带原文对照的 Markdown 清单，供人工判定对错，
再按判定结果算出真实精确率。这是校准抽取质量最直接的办法——
不要用「覆盖率」自欺，覆盖率只说明「有多少条边」，不说明「对不对」。

    # 1. 抽样，生成待判定清单
    python3 -m eval.sample_check --src data/extract/kgv10_....jsonl --n 50

    # 2. 在生成的 md 里把每行 `[ ]` 改成 `[x]`（判对）或 `[-]`（判错）
    # 3. 回收判定，算准确率
    python3 -m eval.sample_check --score data/eval/check_50.md

判定标准（写进清单头部，避免主观漂移）：
- 角色对：该实体在句中确实承担这个语义角色
- 跨度对：取值是原文连续片段，没有截断或拼接
- 值对：不是编造的、不是从别处搬来的
三项全对才算对。
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "eval"

HEADER = """# 抽取质量人工抽检

**判定标准**（三项全对才算对）：
1. **角色对**：该实体在句中确实承担这个语义角色
2. **跨度对**：取值是原文的连续片段，没有截断、拼接或改写
3. **值对**：不是编造的，不是从别的句子搬来的

**怎么标**：把每条前面的 `[ ]` 改成 `[x]`（对）或 `[-]`（错）。
判错请在行尾 `↳` 后写一句原因，便于归类。

判定口径示例：
- 原文「以军空袭加沙」，抽到 `打击方 → 以色列国防军`：对（归一不算错）
- 原文只写「遭袭」，抽到 `打击方 → 某国`：错（编造）
- 原文「伊朗、伊拉克、叙利亚」，只抽到 `打击对象 → 伊朗`：错（漏抽枚举，跨度不全）
"""


def load(src: Path) -> list[dict]:
    rows = []
    for line in src.open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def collect_args(rows: list[dict]) -> list[dict]:
    """收集全部论元（事件 -> 实体）。"""
    out = []
    for row in rows:
        evs = {e["text"]: e for e in (row.get("events") or [])}
        for rel in row.get("relations") or []:
            if rel["name"] == "包含" or rel.get("head") not in evs:
                continue
            ev = evs[rel["head"]]
            out.append({
                "kind": "arg",
                "doc_id": row.get("doc_id"),
                "title": row.get("title") or "",
                "body": row.get("body") or "",
                "ev_text": ev["text"],
                "ev_type": f"{ev.get('event_type')}·{ev.get('subtype') or '-'}",
                "trigger": ev.get("trigger") or "",
                "role": rel["name"],
                "value": rel.get("tail") or "",
                "evidence": rel.get("evidence") or ev.get("evidence") or "",
            })
    return out


def collect_rels(rows: list[dict]) -> list[dict]:
    """收集结构关系（实体 -> 实体）。"""
    STRUCT = {"隶属", "辖属", "驻地", "部署方", "去向",
              "任职", "会晤", "搭载", "位于", "敌对"}
    out = []
    for row in rows:
        evset = {e["text"] for e in (row.get("events") or [])}
        for rel in row.get("relations") or []:
            if rel["name"] not in STRUCT or rel.get("head") in evset:
                continue
            out.append({
                "kind": "rel",
                "doc_id": row.get("doc_id"),
                "title": row.get("title") or "",
                "body": row.get("body") or "",
                "ev_text": "",
                "ev_type": rel["name"],
                "trigger": "",
                "role": rel["name"],
                "value": f"{rel.get('head')} → {rel.get('tail')}",
                "evidence": rel.get("evidence") or "",
            })
    return out


def ctx(body: str, needle: str, width: int = 90) -> str:
    """取包含待判定值的上下文。找不到就用句首。"""
    b = (body or "").replace("\n", " ").strip()
    if not b:
        return ""
    i = b.find(needle) if needle else -1
    if i < 0:
        return b[: width * 2]
    left = max(0, i - width)
    right = min(len(b), i + len(needle) + width)
    return ("…" if left else "") + b[left:right] + ("…" if right < len(b) else "")


def make_md(args: list[dict], rels: list[dict], n_arg: int, n_rel: int) -> str:
    lines = [HEADER, ""]
    lines.append(f"共抽检 {n_arg} 条论元、{n_rel} 条结构关系。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、事件论元")
    lines.append("")
    for i, a in enumerate(args, 1):
        lines.append(f"### A{i}. `{a['ev_type']}`　触发词「{a['trigger']}」")
        lines.append("")
        lines.append(f"- 待判：**{a['role']} → {a['value']}**")
        lines.append(f"- 事件：{a['ev_text']}")
        lines.append(f"- 原文：{ctx(a['body'], a['value'])}")
        if a["evidence"]:
            lines.append(f"- 证据：{a['evidence'][:120]}")
        lines.append(f"- 判定：[ ]　↳")
        lines.append("")
    lines.append("## 二、结构关系")
    lines.append("")
    for i, r in enumerate(rels, 1):
        lines.append(f"### R{i}. `{r['role']}`")
        lines.append("")
        lines.append(f"- 待判：**{r['value']}**")
        head = r["value"].split("→")[0].strip() if "→" in r["value"] else ""
        lines.append(f"- 原文：{ctx(r['body'], head)}")
        if r["evidence"]:
            lines.append(f"- 证据：{r['evidence'][:120]}")
        lines.append(f"- 判定：[ ]　↳")
        lines.append("")
    return "\n".join(lines)


def score(path: Path) -> int:
    """回收判定，算准确率。"""
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.M)
    stat = {}
    detail = []
    for sec in sections:
        if not sec.startswith(("一、", "二、")):
            continue
        name = "论元" if sec.startswith("一、") else "结构关系"
        ok = bad = pending = 0
        for m in re.finditer(r"判定：\[(.)\](.*)", sec):
            mark = m.group(1)
            reason = m.group(2).replace("↳", "").strip()
            if mark == "x":
                ok += 1
            elif mark == "-":
                bad += 1
                if reason:
                    detail.append((name, reason))
            else:
                pending += 1
        stat[name] = (ok, bad, pending)

    print(f"=== {path.name} 判定结果 ===\n")
    tot_ok = tot_bad = tot_pending = 0
    for name, (ok, bad, pending) in stat.items():
        done = ok + bad
        p = ok / done if done else 0
        print(f"{name:8s} 对 {ok:3d}　错 {bad:3d}　未判 {pending:3d}　"
              f"精确率 {p:6.1%}（{ok}/{done}）")
        tot_ok += ok; tot_bad += bad; tot_pending += pending
    done = tot_ok + tot_bad
    if done:
        print(f"\n合计　　 对 {tot_ok:3d}　错 {tot_bad:3d}　未判 {tot_pending:3d}　"
              f"精确率 {tot_ok/done:6.1%}")
    else:
        print("\n尚无判定。")
    if detail:
        print("\n=== 错误原因 ===")
        for name, r in detail[:20]:
            print(f"  [{name}] {r}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="抽取质量人工抽检")
    ap.add_argument("--src", default=str(DATA / "extract" / "kgv10_huanqiu_mil_2026-01.jsonl"))
    ap.add_argument("--n", type=int, default=50, help="论元抽样数")
    ap.add_argument("--n-rel", type=int, default=20, help="结构关系抽样数")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="")
    ap.add_argument("--score", default="", help="回收判定文件算准确率")
    a = ap.parse_args(argv)

    if a.score:
        return score(Path(a.score))

    rows = load(Path(a.src))
    args = collect_args(rows)
    rels = collect_rels(rows)
    rnd = random.Random(a.seed)
    pick_args = rnd.sample(args, min(a.n, len(args)))
    pick_rels = rnd.sample(rels, min(a.n_rel, len(rels)))

    md = make_md(pick_args, pick_rels, len(pick_args), len(pick_rels))
    out = Path(a.out) if a.out else OUT / f"check_{a.n}arg_{a.n_rel}rel.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")

    print(f"候选池：论元 {len(args)} 条，结构关系 {len(rels)} 条")
    print(f"已抽 {len(pick_args)} 条论元、{len(pick_rels)} 条关系 → {out}")
    print(f"\n判定完后执行：")
    print(f"  python3 -m eval.sample_check --score {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
