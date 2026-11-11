"""实体消歧与事件融合。

一、实体消歧
    规范化 → 类型内阻塞 → 词表合并 → 大模型判同一/相关/不同 → 赋规范编号
二、事件融合
    时间相近、地点相同，且机构或装备有重叠的主张挂到同一规范事件；
    槽位不一致的双方保留并标冲突。

    python3 -m align.disamb
    python3 -m align.disamb --max-pairs 120
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from align.cluster import UnionFind, pair_key
from align.methods import llm_label_pairs
from align.normalize import normalize

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SRC = DATA / "extract" / "full_zh_final2.jsonl"
OUT = DATA / "extract" / "full_zh_resolved2.json"
CACHE = DATA / "eval" / "disamb_pairs_cache.json"
REPORT = DATA / "reports" / "disamb.md"

NAME_TYPES = {"人物", "机构", "装备", "地点", "平台", "作品"}
SENT_TYPES = {"事件", "观点", "场景", "立场"}

JUDGE_SYSTEM = """你是军事新闻实体对齐裁判。判断两个名称是否指向同一个对象。
same_as：同一人、同一机构、同一装备、同一地点的不同写法（本雅明·内塔尼亚胡=内塔尼亚胡，以军=以色列国防军）。
related：有从属、代表或包含关系，但不是同一节点（以色列≠以色列国防军，美国≠美军，第13届东盟防长扩大会≠东盟）。
distinct：不同对象（美军≠俄军，袭击≠空袭）。
国家与其军队、政府、人物一律 related。
拿不准选 related。输出 JSON：{"pairs":[{"a":"...","b":"...","label":"same_as|related|distinct"}]}"""


def _rows(path: Path = SRC) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _eid(kind: str, name: str) -> str:
    raw = f"{kind}|{normalize(name)}"
    return f"{kind[:2]}-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _cand_key(name: str) -> str:
    """阻塞键：去括注、去职务前缀后的主干。"""
    n = normalize(name)
    n = re.sub(r"^(?:中国|美国|俄罗斯|以色列|伊朗|乌克兰|沙特|英国|法国|德国)",
               lambda m: m.group(0), n)
    return n


def collect(rows: list[dict]) -> dict[str, dict]:
    """按 (类型, 规范形) 收名称，记出现次数与别名。"""
    seen: dict[tuple[str, str], dict] = {}
    for row in rows:
        for ent in row["entities"]:
            name, etype = ent["name"], ent["type"]
            if etype in SENT_TYPES:
                continue
            key = (etype, _cand_key(name))
            slot = seen.setdefault(key, {"type": etype, "names": Counter(), "aliases": set()})
            slot["names"][name] += 1
            for a in ent.get("aliases") or []:
                slot["aliases"].add(a)
    return seen


def block_candidates(seen: dict[tuple[str, str], dict]) -> list[tuple[str, str, str]]:
    """类型内产生候选对：同阻塞键、包含关系、编辑相似度、共享别名。"""
    from difflib import SequenceMatcher

    by_type: dict[str, list[str]] = defaultdict(list)
    for (etype, _ck), slot in seen.items():
        for name in slot["names"]:
            by_type[etype].append(name)
    pairs: set[tuple[str, str, str]] = set()
    for etype, names in by_type.items():
        uniq = sorted(set(names), key=len)
        for i, a in enumerate(uniq):
            na = normalize(a)
            if len(na) < 2:
                continue
            for b in uniq[i + 1 :]:
                nb = normalize(b)
                if len(nb) < 2:
                    continue
                if na == nb:
                    pairs.add((etype, a, b))
                    continue
                # 包含：短串出现在长串里，且短串不短于 3
                if len(na) >= 3 and na in nb:
                    pairs.add((etype, a, b))
                    continue
                if len(nb) >= 3 and nb in na:
                    pairs.add((etype, a, b))
                    continue
                # 编辑相似度
                if abs(len(na) - len(nb)) <= 6 and SequenceMatcher(None, na, nb).ratio() >= 0.86:
                    pairs.add((etype, a, b))
    # 共享别名
    alias_idx: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (etype, _ck), slot in seen.items():
        for alias in slot["aliases"]:
            for name in slot["names"]:
                alias_idx[(etype, normalize(alias))].append(name)
    for (etype, _alias), names in alias_idx.items():
        uniq = sorted(set(names))
        for i, a in enumerate(uniq):
            for b in uniq[i + 1 :]:
                pairs.add((etype, a, b))
    return sorted(pairs)


def judge(pairs: list[tuple[str, str, str]], limit: int) -> list[dict]:
    """按类型分批交给模型。超出上限的按出现次数优先。"""
    if not pairs:
        return []
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for etype, a, b in pairs:
        grouped[etype].append((a, b))
    labeled: list[dict] = []
    used = 0
    for etype, group in grouped.items():
        if used >= limit:
            break
        take = group[: max(0, limit - used)]
        used += len(take)
        got = llm_label_pairs(take, cache_path=CACHE, system=JUDGE_SYSTEM)
        for item in got:
            labeled.append({**item, "type": etype})
    return labeled


def resolve(rows: list[dict], labels: list[dict]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """same_as 合并成簇，给出规范编号与别名。"""
    clusters: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in labels:
        if item.get("label") != "same_as":
            continue
        etype = item["type"]
        clusters[(etype, item["a"])].add(item["b"])
    uf_by_type: dict[str, UnionFind] = {}
    for etype in NAME_TYPES:
        uf_by_type[etype] = UnionFind([])
    for item in labels:
        if item.get("label") != "same_as":
            continue
        uf = uf_by_type[item["type"]]
        uf.union(item["a"], item["b"])
    canon: dict[str, dict] = {}
    alias_map: dict[str, list[str]] = {}
    for etype, uf in uf_by_type.items():
        groups: dict[str, list[str]] = defaultdict(list)
        for name in uf.parent:
            groups[uf.find(name)].append(name)
        for members in groups.values():
            members = sorted(set(members))
            # 规范名取出现最多者，按成员在当前数据里的频次
            display = members[0]
            cid = _eid(etype, display)
            for name in members:
                canon[name] = {"id": cid, "type": etype, "canonical": display}
            if len(members) > 1:
                alias_map[cid] = members
    return canon, alias_map


def apply_ids(rows: list[dict], canon: dict[str, dict]) -> list[dict]:
    """给实体补 id 与 aliases，关系两端改写成 id。"""
    out = []
    for row in rows:
        id_of: dict[str, str] = {}
        ents = []
        for ent in row["entities"]:
            name, etype = ent["name"], ent["type"]
            info = canon.get(name)
            if info and info["type"] == etype:
                eid, cname = info["id"], info["canonical"]
            else:
                eid, cname = _eid(etype, name), name
            id_of[name] = eid
            ents.append({
                **ent,
                "id": eid,
                "canonical": cname,
                "aliases": sorted(set(ent.get("aliases") or [])
                                  | ({name} if cname != name else set())),
            })
        rels = []
        for rel in row["relations"]:
            h = id_of.get(rel["head"])
            t = id_of.get(rel["tail"])
            if not h or not t:
                continue
            rels.append({**rel, "head_id": h, "tail_id": t, "doc_id": row.get("doc_id") or ""})
        out.append({**row, "entities": ents, "relations": rels})
    return out


def _day(row: dict) -> str:
    return (row.get("publish_time") or "")[:10]


def _near(d1: str, d2: str, days: int = 3) -> bool:
    if not d1 or not d2:
        return False
    try:
        a = datetime.strptime(d1, "%Y-%m-%d")
        b = datetime.strptime(d2, "%Y-%m-%d")
    except ValueError:
        return False
    return abs((a - b).days) <= days


def _country_ids(rows: list[dict]) -> set[str]:
    """国家节点的编号集合。国家不算具体地点。"""
    from align.types import COUNTRY

    out = set()
    for row in rows:
        for ent in row["entities"]:
            if ent.get("subtype") == "国家" or ent["name"] in COUNTRY:
                out.add(ent.get("id") or ent["name"])
    return out


GENERIC_NODES = frozenset(
    """
    白宫 五角大楼 国会 参议院 众议院 国务院 联合国 北约 欧盟 中东地区 欧洲
    亚洲 非洲 海湾国家 西方 国际社会 社交媒体 美国 俄罗斯 中国 以色列 伊朗
    乌克兰 也门 沙特阿拉伯 黎巴嫩 叙利亚 伊拉克 埃及 土耳其 英国 法国 德国 日本
    韩国 印度 巴基斯坦 尼日利亚 加纳 菲律宾 地区 边境 首都
    """.split()
)


def _specific(names: set[str], labels: dict[str, str]) -> set[str]:
    """去掉泛化节点，只留具体对象。labels 为 id -> 名称。"""
    return {i for i in names if labels.get(i, i) not in GENERIC_NODES}


def fuse_events(rows: list[dict], days: int = 2) -> tuple[list[dict], dict]:
    """同一热点合并。条件同时满足：日期相近、具体地点相同，
    且具体机构或装备有重叠。泛化节点（白宫、五角大楼、国家名）不参与判断。
    """
    labels = {}
    for row in rows:
        for ent in row["entities"]:
            labels[ent.get("id") or ent["name"]] = ent.get("canonical") or ent["name"]
    COUNTRY_IDS = _country_ids(rows)
    claims = []
    for row in rows:
        ents = {e["type"]: set() for e in row["entities"]}
        for e in row["entities"]:
            ents[e["type"]].add(e.get("id") or e["name"])
        claims.append({
            "row": row,
            "day": _day(row),
            "org": _specific(ents.get("机构", set()), labels) - COUNTRY_IDS,
            "gear": _specific(ents.get("装备", set()), labels),
            "place": _specific(ents.get("地点", set()), labels),
        })
    parent = list(range(len(claims)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a, b = claims[i], claims[j]
            if a["row"].get("doc_id") == b["row"].get("doc_id"):
                continue
            if not _near(a["day"], b["day"], days):
                continue
            place_shared = a["place"] & b["place"]
            if not place_shared:
                continue
            # 至少两处具体重叠，或一处具体地点加一处具体主体
            if not ((a["org"] & b["org"]) or (a["gear"] & b["gear"])):
                continue
            if len(place_shared) + len(a["org"] & b["org"]) + len(a["gear"] & b["gear"]) < 2:
                continue
            union(i, j)

    groups: dict[int, list[dict]] = defaultdict(list)
    for idx, claim in enumerate(claims):
        groups[find(idx)].append(claim)

    stats = Counter()
    events = []
    for root, members in groups.items():
        if len(members) == 1:
            row = members[0]["row"]
            ev_id = "ev-" + hashlib.sha1(
                f"{row.get('doc_id')}|{row.get('title')}".encode("utf-8")).hexdigest()[:10]
            events.append({"event_id": ev_id, "docs": [row.get("doc_id")], "titles": [row.get("title")],
                           "day": members[0]["day"], "claims": 1, "conflict": False})
            continue
        stats["fused_groups"] += 1
        # 槽位一致性：机构集合不同即冲突
        org_sets = [m["org"] for m in members]
        conflict = any(s != org_sets[0] for s in org_sets[1:])
        if conflict:
            stats["conflict_groups"] += 1
        ev_id = "ev-" + hashlib.sha1(
            "|".join(sorted(str(m["row"].get("doc_id")) for m in members)).encode("utf-8")
        ).hexdigest()[:10]
        events.append({
            "event_id": ev_id,
            "docs": [m["row"].get("doc_id") for m in members],
            "titles": [m["row"].get("title") for m in members],
            "day": min(m["day"] for m in members if m["day"]) if any(m["day"] for m in members) else "",
            "claims": len(members),
            "conflict": conflict,
            "org_diff": [sorted(s) for s in org_sets] if conflict else None,
        })
    return events, dict(stats)


def run(max_pairs: int = 400) -> dict:
    rows = _rows()
    seen = collect(rows)
    pairs = block_candidates(seen)
    labels = judge(pairs, max_pairs)
    same = [x for x in labels if x.get("label") == "same_as"]
    canon, alias_map = resolve(rows, labels)
    rows2 = apply_ids(rows, canon)
    events, ev_stats = fuse_events(rows2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "clusters": canon,
        "aliases": alias_map,
        "events": events,
        "docs": rows2,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    by_label = Counter(x.get("label") for x in labels)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        "\n".join([
            "# 实体消歧与事件融合",
            "",
            f"- 文档 {len(rows)}，名称型实体 {sum(len(v['names']) for v in seen.values())}",
            f"- 候选对 {len(pairs)}，判过 {len(labels)}：{dict(by_label)}",
            f"- 合并簇 {sum(1 for v in alias_map.values() if len(v) > 1)}",
            f"- 事件 {len(events)}，合并组 {ev_stats.get('fused_groups', 0)}，"
            f"冲突组 {ev_stats.get('conflict_groups', 0)}",
            "",
            "## 合并示例",
            "",
            *[f"- `{cid}`: {' / '.join(v)}" for cid, v in list(alias_map.items())[:15]],
            "",
        ]),
        encoding="utf-8",
    )
    return {
        "docs": len(rows),
        "candidate_pairs": len(pairs),
        "judged": len(labels),
        "labels": dict(by_label),
        "merged_clusters": sum(1 for v in alias_map.values() if len(v) > 1),
        "events": len(events),
        "event_stats": ev_stats,
        "out": str(OUT),
        "report": str(REPORT),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="实体消歧与事件融合")
    p.add_argument("--max-pairs", type=int, default=400)
    a = p.parse_args(argv)
    print(json.dumps(run(a.max_pairs), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
