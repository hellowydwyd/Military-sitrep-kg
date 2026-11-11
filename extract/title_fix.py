"""职务串改判：把「美国总统」「俄军总参谋长」这类职务名从机构改成人物。

起因：提示词里「国家也算机构」被模型外推成「美国总统是机构」，全量 4115 篇里
610 个职务串被标成机构，涉及 479 篇。同一篇里「美国总统」和「特朗普」并存，
一个主体裂成两个节点。

规则与 extract.kg_extract 的 title_span_kind 一致：
- 职务词结尾、或职务词后只跟一个人名 → 人物
- 职务词后跟机关后缀（府、办公室、部、局、院、委员会、司令部、秘书处）→ 仍是机构
- 职务词后跟文书后缀（令、法、案、条例）→ 作品

带姓名的职务串若同一篇里姓名已单独成节点，则并入姓名，关系端点一并改写。

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

# 这些关系要求起点是机构；端点改成人物后，把边改记为人物也能成立的施事边
ORG_HEAD_REL = {"实施", "部署", "从属", "隶属", "辖属", "敌对", "发布"}
ORG_TAIL_REL = {"从属", "辖属", "隶属", "敌对"}

# 头端从机构变人物时可以改记为施事的关系（机构→事件 转成 事件→人物）
TO_AGENT_REL = {"实施", "发布"}


def as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


# 两种抽取结果的字段名不同：kg_batch 用 text，align.zh 用 name
NAME_KEYS = ("name", "text")


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
    """返回改判后的行与统计。"""
    st = Counter()
    ents = as_list(row.get("entities"))
    if not ents:
        return row, st

    names = {ent_name(e): e.get("type") for e in ents}
    alias: dict[str, str] = {}

    # 第一遍：机构改成人物
    for e in ents:
        name = ent_name(e)
        if e.get("type") != "机构":
            continue
        kind = title_span_kind(name)
        if not kind:
            continue
        if kind == "title_name":
            person = title_name_part(name)
            if person and names.get(person) == "人物":
                alias[name] = person
                st["merged_into_name"] += 1
                continue
            # 姓名没单独成节点，就把这个串本身当人物（保留了职务与姓名）
        e["type"] = "人物"
        st["org_to_person"] += 1

    if alias:
        ents = [e for e in ents if ent_name(e) not in alias]
        # 去掉名字相同后可能产生的重复
        seen: set[str] = set()
        dedup = []
        for e in ents:
            n = ent_name(e)
            if n in seen:
                continue
            seen.add(n)
            dedup.append(e)
        ents = dedup

    types = {ent_name(e): e.get("type") for e in ents}

    # 第二遍：关系端点换名 + 类型不再成立时丢弃
    rels = []
    for x in as_list(row.get("relations")):
        h = alias.get(x.get("head"), x.get("head"))
        t = alias.get(x.get("tail"), x.get("tail"))
        if h != x.get("head") or t != x.get("tail"):
            x["head"], x["tail"] = h, t
            st["relation_endpoint_renamed"] += 1
        name = x.get("name")
        ht, tt = types.get(h), types.get(t)
        if name in ORG_HEAD_REL and ht == "人物":
            # 机构→事件 的边，头端是人时改记成 事件→施事，不丢事实
            if name in TO_AGENT_REL and tt == "事件":
                rels.append({
                    "name": "施事",
                    "head": t,
                    "tail": h,
                    "evidence": x.get("evidence") or "",
                    "chunk": x.get("chunk", 0),
                })
                st[f"retype_relation_{name}_to_施事"] += 1
                continue
            st[f"drop_relation_{name}"] += 1
            continue
        if name in ORG_TAIL_REL and tt == "人物":
            st[f"drop_relation_{name}"] += 1
            continue
        rels.append(x)

    # persons 里补上缺少职务的说明不做改动，职务本来就在「身份/职业」
    out = dict(row)
    out["entities"] = ents
    out["relations"] = rels
    return out, st


def run(src: Path, out: Path, limit: int = 0) -> Counter:
    total = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    with src.open(encoding="utf-8") as f, out.open("w", encoding="utf-8") as g:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            fixed, st = fix_row(row)
            total.update(st)
            g.write(json.dumps(fixed, ensure_ascii=False) + "\n")
    total["docs"] = i + 1 if not limit else min(limit, i + 1)
    return total


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="职务串从机构改判为人物")
    p.add_argument("--src", default=str(DATA / "extract" / "full_zh_localized2.jsonl"))
    p.add_argument("--out", default=str(DATA / "extract" / "full_zh_titlefix.jsonl"))
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args(argv)
    st = run(Path(a.src), Path(a.out), a.limit)
    print(json.dumps(dict(st), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
