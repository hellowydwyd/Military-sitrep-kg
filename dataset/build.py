"""三分卷导出：把抽取结果切成 NER、EE、RE 三份标注卷。

三个任务共享同一批文本，但各自的标注内容与评测指标不同：
- NER 卷：只标五类实体，评测片段加类型 F1
- EE 卷：标原子事件与六类论元，评测 Trg-F1 与 Arg-F1
- RE 卷：标实体间的十种结构关系，评测三元组 P/R/F1

RE 卷需要先筛选含结构关系的稿件，否则多数篇目无可标内容。

    python3 -m dataset.build --src data/extract/kgab_huanqiu_mil_2026-10.jsonl
    python3 -m dataset.build --src ... --sample 300 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from extract import country_infer, entity_norm

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT_DIR = DATA / "dataset"

# 关系归属统一取自 extract/slots.py。
# 注意「部署方」是同名多义：作事件论元（事件→机构）归 EE，作装备关系（装备→机构）归 RE。
# 所以按「关系名 + 头端点是否事件」判别，不能只看名字。
from extract.slots import DOMAIN, ROLE_NAMES  # noqa: E402

# 十个实体间结构关系归 RE
RE_RELATIONS = {"隶属", "辖属", "驻地", "部署方", "去向", "任职", "会晤", "搭载", "位于", "敌对"}
# EE 卷的论元：slots.py 里声明的全部槽位（不含包含）
EE_RELATIONS = set(ROLE_NAMES)
# 「部署方」等同名多义靠头端点区分：头是事件的算论元，头是装备的算实体关系

# 预筛正则：原文出现这些语义才可能标出结构关系
RE_HINTS = {
    "任职": re.compile(r"(出任|担任|就任|任命|兼任|接任|任.{0,6}(部长|司令|总统|主席|局长|主任|参谋长|发言人))"),
    "会晤": re.compile(r"(会见|会谈|通话|会晤|接见|通电话|举行会谈|举行会晤)"),
    "敌对": re.compile(r"(交火|冲突|对抗|宣战|敌对|打击|袭击|围攻)"),
    "搭载": re.compile(r"(搭载|挂载|携带.{0,8}(导弹|无人机|雷达|炸弹)|配备.{0,8}(导弹|武器))"),
    "位于": re.compile(r"(位于|地处|毗邻|接壤|辖区|管辖|属于.{0,6}(省|州|地区))"),
}


def load_rows(src: Path) -> list[dict]:
    rows = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("errors"):
            continue
        rows.append(row)
    return rows


def attach_body(rows: list[dict]) -> list[dict]:
    """抽取结果不带正文，从 raw 按 url 补齐，标注时需要看原文。

    译文稿同时带上英文原文，标注界面可做对照。
    """
    raw: dict[str, dict] = {}
    src_dir = DATA / "raw"
    need = {(r.get("url") or "") for r in rows if not r.get("body")}
    if not need:
        return rows
    for d in src_dir.iterdir():
        if not d.is_dir():
            continue
        for path in d.glob("*.jsonl"):
            for line in path.open(encoding="utf-8"):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = rec.get("url") or ""
                if url in need and url not in raw:
                    raw[url] = rec
    for r in rows:
        url = r.get("url") or ""
        if r.get("body"):
            # 译文稿自带 body 与 body_orig，不用回填
            continue
        rec = raw.get(url) or {}
        r["body"] = rec.get("body") or ""
        # 英文稿译成中文后，body 是译文，body_orig 是原文
        if r.get("translated"):
            r.setdefault("title_orig", rec.get("title") or "")
            r.setdefault("body_orig", rec.get("body") or "")
        elif rec.get("lang", "").startswith("en"):
            # 未译的英文稿，body 即英文，供界面提示
            r["body_orig"] = r["body"]
            r["title_orig"] = rec.get("title") or ""
    return rows


def re_score(row: dict) -> tuple[int, list[str]]:
    """评估一篇稿件可标出多少类结构关系，用于 RE 卷排序与筛选。"""
    events = {e["text"] for e in row.get("events") or []}
    rels = {
        x["name"] for x in row.get("relations") or []
        if x["name"] in RE_RELATIONS and x["head"] not in events
    }
    text = f"{row.get('title') or ''} {row.get('body') or ''}"
    hints = [k for k, rx in RE_HINTS.items() if rx.search(text)]
    score = len(rels) * 2 + len(set(hints) - rels)
    return score, sorted(rels | set(hints))


def _bilingual(row: dict) -> dict:
    """译文稿带上原文与译文字段，供标注界面对照。"""
    out: dict = {}
    if row.get("translated"):
        out["title_orig"] = row.get("title_orig") or ""
        out["text_orig"] = row.get("body_orig") or ""
        out["translated"] = True
    return out


def to_ner(row: dict) -> dict:
    ents = []
    for e in row.get("entities") or []:
        ents.append({"text": e["text"], "type": e["type"]})
    return {
        "doc_id": row.get("doc_id"),
        "title": row.get("title"),
        "text": row.get("body") or "",
        "entities": ents,
        **_bilingual(row),
    }


def to_ee(row: dict) -> dict:
    events = []
    for ev in row.get("events") or []:
        args = []
        for x in row.get("relations") or []:
            if x["head"] != ev["text"]:
                continue
            # 头端点就是本事件，必是论元；再排除「包含」这类事件间结构
            if x["name"] == "包含" or x["name"] not in EE_RELATIONS:
                continue
            item = {"role": x["name"], "text": x["tail"]}
            # 端点被降级为泛称：值保留，但没有对应的图节点
            if x.get("tail_literal"):
                item["literal"] = True
            if x.get("stance"):
                item["stance"] = x["stance"]
            args.append(item)
        events.append({
            "text": ev["text"],
            "trigger": ev.get("trigger") or "",
            "event_type": ev["event_type"],
            "subtype": ev.get("subtype") or "",
            "kind": ev["kind"],
            "time_raw": ev.get("raw_time") or "",
            "time_norm": ev.get("time_norm") or "",
            "time_precision": ev.get("time_precision") or "unknown",
            "arguments": args,
            "evidence": ev.get("evidence") or "",
        })
    comps = []
    for c in row.get("composites") or []:
        comps.append({
            "text": c["text"],
            "event_type": c["event_type"],
            "subtype": c.get("subtype") or "",
            "kind": c["kind"],
            "covers": c.get("covers") or [],
            "fallback": bool(c.get("fallback")),
        })
    return {
        "doc_id": row.get("doc_id"),
        "title": row.get("title"),
        "text": row.get("body") or "",
        "events": events,
        "composites": comps,
        **_bilingual(row),
    }


def to_re(row: dict) -> dict:
    triples = []
    events = {e["text"] for e in row.get("events") or []}
    for x in row.get("relations") or []:
        # 「部署方」等同名多义：头端点是事件的是论元，不进 RE 卷
        if x["name"] not in RE_RELATIONS or x["head"] in events:
            continue
        triples.append({
            "head": x["head"],
            "relation": x["name"],
            "tail": x["tail"],
            "evidence": x.get("evidence") or "",
            # 辖属等边区分来源：text=原文可证，marker=名称含国别标志，inferred=常识推断
            **({"source": x["source"]} if x.get("source") else {}),
        })
    return {
        "doc_id": row.get("doc_id"),
        "title": row.get("title"),
        "text": row.get("body") or "",
        "triples": triples,
        **_bilingual(row),
    }


def split(rows: list[dict], ratios: tuple[float, float, float], seed: int) -> dict[str, list[dict]]:
    rnd = random.Random(seed)
    idx = list(range(len(rows)))
    rnd.shuffle(idx)
    n = len(rows)
    n_train = int(n * ratios[0])
    n_dev = int(n * ratios[1])
    return {
        "train": [rows[i] for i in idx[:n_train]],
        "dev": [rows[i] for i in idx[n_train : n_train + n_dev]],
        "test": [rows[i] for i in idx[n_train + n_dev :]],
    }


def write_jsonl(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def run(src: Path, sample: int = 0, seed: int = 7, re_ratio: float = 0.6) -> dict:
    rows = attach_body(load_rows(src))
    if sample and len(rows) > sample:
        rows = random.Random(seed).sample(rows, sample)
        rows.sort(key=lambda r: r.get("publish_time") or "")

    # 实体归一与泛称降级：简称合成全称，泛称设施/通名装备不建节点
    rows, norm_stat = entity_norm.apply(rows)
    # 辖属边：打 source 标记，并剔除跨国/双边机构的错误归属
    rows, _ = country_infer.mark_sources(rows)
    rows, affiliation_stat = country_infer.fix_affiliation(rows)

    stats = Counter()
    stats["norm"] = norm_stat
    stats["affiliation"] = affiliation_stat
    # NER 与 EE 用全部篇目
    ner_rows = [to_ner(r) for r in rows]
    ee_rows = [to_ee(r) for r in rows]
    stats["ner_docs"] = len(ner_rows)
    stats["ee_docs"] = len(ee_rows)
    stats["ee_events"] = sum(len(r["events"]) for r in ee_rows)
    stats["ee_composites"] = sum(len(r["composites"]) for r in ee_rows)

    # RE 只取有结构关系或预筛命中的篇目，按可标量排序
    scored = [(re_score(r), r) for r in rows]
    scored.sort(key=lambda x: -x[0][0])
    keep_n = max(1, int(len(rows) * re_ratio))
    re_rows = [to_re(r) for _, r in scored[:keep_n]]
    re_rows = [r for r in re_rows if r["triples"]]
    stats["re_docs"] = len(re_rows)
    stats["re_triples"] = sum(len(r["triples"]) for r in re_rows)

    out = {}
    for name, data in (("ner", ner_rows), ("ee", ee_rows), ("re", re_rows)):
        parts = split(data, (0.7, 0.15, 0.15), seed) if data else {"train": [], "dev": [], "test": []}
        for part, subset in parts.items():
            path = OUT_DIR / f"{name}_{part}.jsonl"
            out[f"{name}_{part}"] = write_jsonl(path, subset)

    # 关系种类分布
    rel_dist: Counter = Counter()
    for r in re_rows:
        for t in r["triples"]:
            rel_dist[t["relation"]] += 1
    stats["re_by_relation"] = dict(rel_dist)
    return {"stats": dict(stats), "files": out, "out_dir": str(OUT_DIR)}


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="三分卷导出")
    p.add_argument("--src", default=str(DATA / "extract" / "kgab_huanqiu_mil_2026-10.jsonl"))
    p.add_argument("--sample", type=int, default=0, help="随机抽样篇数，0 表示全部")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--re-ratio", type=float, default=0.6, help="RE 卷从全部篇目中取前多少比例")
    a = p.parse_args(argv)
    print(json.dumps(run(Path(a.src), a.sample, a.seed, a.re_ratio), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
