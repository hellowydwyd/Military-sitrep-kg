"""职务串清理：把「美国总统」「俄军总参谋长」这类称谓从实体表里剔除。

设计依据：新闻稿几乎都会写清人名。全量 4115 篇里，含职务串人物的 558 篇中
521 篇（93.4%）同篇还有其他人名实体；剩下的 37 篇，原文要么只写职务没写名字，
要么是「西方领导人」「核心指挥官」这类泛称，都不是人名。

所以职务串不是实体：
- 带姓名的职务串（「美国总统特朗普」）只保留姓名「特朗普」
- 纯职务串（「伊朗军方发言人」）直接剔除
- 挂在这些职务串上的边一并丢弃；事件保留，只是缺一个施事论元

两个字段名都支持：kg_batch 用 text，align.zh 用 name。

    python3 -m extract.title_fix --src data/extract/full_zh_localized2.jsonl \
        --out data/extract/full_zh_titlefix.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Optional

from extract.kg_extract import title_name_part, title_span_kind

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

NAME_KEYS = ("name", "text")


def as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def ent_name(e: dict) -> str:
    for k in NAME_KEYS:
        v = e.get(k)
        if v:
            return str(v)
    return ""


def set_ent_name(e: dict, value: str) -> None:
    for k in NAME_KEYS:
        if k in e:
            e[k] = value
            return
    e["name"] = value


def fix_row(row: dict) -> tuple[dict, Counter]:
    """返回清理后的行与统计。"""
    st = Counter()
    ents = as_list(row.get("entities"))
    if not ents:
        return row, st

    names = {ent_name(e): e.get("type") for e in ents}
    alias: dict[str, str] = {}
    drop_names: set[str] = set()

    for e in ents:
        name = ent_name(e)
        if not title_span_kind(name):
            continue
        person = title_name_part(name)
        if person and len(person) >= 2 and person != name:
            # 姓名已单独成节点，直接指向它
            if person in names:
                alias[name] = person
                drop_names.add(name)
                st["title_merged"] += 1
                continue
            # 姓名没成节点，把这个实体本身改名成姓名
            set_ent_name(e, person)
            e["type"] = "人物"
            names.pop(name, None)
            names[person] = "人物"
            alias[name] = person
            st["title_renamed"] += 1
            continue
        drop_names.add(name)
        st["title_dropped"] += 1

    if drop_names:
        ents = [e for e in ents if ent_name(e) not in drop_names]
        seen: set[str] = set()
        dedup = []
        for e in ents:
            n = ent_name(e)
            if n in seen:
                st["entity_dup_removed"] += 1
                continue
            seen.add(n)
            dedup.append(e)
        ents = dedup

    types = {ent_name(e): e.get("type") for e in ents}
    ev_names = _events(row)
    # 清理前就存在的实体与事件名，用来区分「本次改动造成的丢失」
    before = {ent_name(e) for e in as_list(row.get("entities"))} | ev_names

    rels = []
    for x in as_list(row.get("relations")):
        h0, t0 = x.get("head"), x.get("tail")
        h = alias.get(h0, h0)
        t = alias.get(t0, t0)
        if h in drop_names or t in drop_names:
            st["relation_dropped_title"] += 1
            continue
        # 改名为姓名后姓名不在实体表里（原文没抽出人名）：这条边丢弃
        lost = any(
            v0 in alias and v not in types and v not in ev_names
            for v0, v in ((h0, h), (t0, t))
        )
        if lost:
            st["relation_dropped_untyped"] += 1
            continue
        # 原本就无类型的端点，不归因于本次改动
        if h0 == h and t0 == t and (h not in before or t not in before):
            st["relation_pre_existing_orphan"] += 1
            rels.append(x)
            continue
        if h != h0 or t != t0:
            x["head"], x["tail"] = h, t
            st["relation_endpoint_renamed"] += 1
        rels.append(x)

    out = dict(row)
    out["entities"] = ents
    out["relations"] = rels
    return out, st


def _events(row: dict) -> set[str]:
    ev = row.get("events")
    if isinstance(ev, dict):
        ev = [ev]
    return {e.get("text") for e in (ev or []) if e.get("text")}


def run(src: Path, out: Path, limit: int = 0) -> Counter:
    total = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with src.open(encoding="utf-8") as f, out.open("w", encoding="utf-8") as g:
        for line in f:
            if limit and n >= limit:
                break
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            fixed, st = fix_row(row)
            total.update(st)
            g.write(json.dumps(fixed, ensure_ascii=False) + "\n")
            n += 1
    total["docs"] = n
    return total


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="职务串从实体表剔除")
    p.add_argument("--src", default=str(DATA / "extract" / "full_zh_localized2.jsonl"))
    p.add_argument("--out", default=str(DATA / "extract" / "full_zh_titlefix.jsonl"))
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args(argv)
    st = run(Path(a.src), Path(a.out), a.limit)
    print(json.dumps(dict(st), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
