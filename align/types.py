"""类型归一：同一名称只保留一个类型。

两类冲突分开处理。
- 主权国家：本体把国家作为机构的一类，地点写法并入机构，子类记为「国家」。
  去向边的终点允许是地点或国家。
- 媒体：媒体栏目、站点、账号归平台。由机构改为平台后，实施边改记发布边。

    python3 -m align.types --src data/extract/pilot10v3_zh.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 主权国家与地区实体。按机构登记，子类写「国家」。
COUNTRY = frozenset(
    """
    中国 美国 俄罗斯 乌克兰 以色列 伊朗 伊拉克 叙利亚 也门 沙特阿拉伯 沙特
    土耳其 英国 法国 德国 日本 韩国 朝鲜 印度 巴基斯坦 阿富汗 澳大利亚 加拿大
    菲律宾 越南 泰国 印度尼西亚 马来西亚 新加坡 缅甸 孟加拉国 斯里兰卡 尼泊尔
    埃及 约旦 黎巴嫩 卡塔尔 阿联酋 阿曼 巴林 科威特 利比亚 苏丹 阿尔及利亚
    摩洛哥 突尼斯 埃塞俄比亚 肯尼亚 索马里 尼日利亚 加纳 南非 尼日尔 马里
    乍得 喀麦隆 刚果民主共和国 乌干达 坦桑尼亚 津巴布韦 莫桑比克 安哥拉
    巴西 阿根廷 智利 秘鲁 哥伦比亚 委内瑞拉 墨西哥 古巴 厄瓜多尔 玻利维亚
    巴拉圭 乌拉圭 意大利 西班牙 葡萄牙 荷兰 比利时 瑞士 瑞典 挪威 丹麦
    芬兰 波兰 捷克 斯洛伐克 匈牙利 罗马尼亚 保加利亚 希腊 塞尔维亚 克罗地亚
    奥地利 爱尔兰 白俄罗斯 格鲁吉亚 亚美尼亚 阿塞拜疆 哈萨克斯坦 乌兹别克斯坦
    土库曼斯坦 塔吉克斯坦 吉尔吉斯斯坦 蒙古 新西兰 巴勒斯坦 台湾 香港 澳门
    科索沃 摩尔多瓦 立陶宛 拉脱维亚 爱沙尼亚 斯洛文尼亚 波黑 北马其顿 阿尔巴尼亚
    """.split()
)

# 通讯社、电视台、报刊、站点与账号。按平台登记。
MEDIA = frozenset(
    """
    新华社 路透社 美联社 法新社 塔斯社 俄新社 卫星通讯社 彭博社
    马西拉电视台 半岛电视台 阿拉比亚电视台 伊朗新闻电视台 今日俄罗斯
    纽约时报 华盛顿邮报 华尔街日报 金融时报 卫报 泰晤士报 每日电讯报
    耶路撒冷邮报 以色列时报 基辅邮报 基辅独立报 乌克兰国家通讯社 国际文传电讯社
    环球时报 环球网 参考消息 人民日报 央视新闻 央视 总台 中国日报 观察者网
    澎湃新闻 财新 界面新闻 南方周末 新京报
    先锋报 国民报 今日报 每日邮报 卫报尼日利亚 优质时报 抨击报
    印度时报 印度斯坦时报 黎明报 海峡时报 曼谷邮报 雅加达邮报
    真理报 消息报 共青团真理报 俄罗斯商业咨询 国际文传电讯
    防务新闻 国防新闻 战区 战争地带 简氏防务 海军新闻 空军杂志
    美国之音 自由欧洲电台 自由亚洲电台 BBC CNN CNBC MSNBC ABC CBS NBC
    推特 脸书 电报 油管 X 微博 微信 抖音 TikTok Telegram Facebook Twitter YouTube
    以色列国防军发言人 伊朗伊斯兰共和国通讯社 也门通讯社 沙特通讯社
    """.split()
)

MEDIA_LATIN = frozenset(
    """
    xinhua reuters ap afp tass ria sputnik bloomberg aljazeera alarabiya press tv
    rt nyt nytimes washingtonpost wsj ft theguardian thetimes telegraph
    jpost jpostcom timesofisrael kyivpost kyivindependent ukrinform interfax
    globaltimes cgtn chinadaily guancha thepaper caixin bbc cnn cnbc msnbc abc cbs nbc
    vanguard thenational premiumtimes punch dailytrust channels
    defensenews twz janes navalnews airforcemag
    twitter facebook telegram youtube x weibo wechat douyin tiktok
    arxivlabs arxiv
    """.split()
)


def _latin_key(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def is_country(name: str) -> bool:
    return (name or "").strip() in COUNTRY


def is_media(name: str) -> bool:
    n = (name or "").strip()
    if n in MEDIA:
        return True
    return _latin_key(n) in MEDIA_LATIN


# 关系两端类型。国家是机构子类，去向终点可为地点或国家。
DOMAIN = {
    "参与": ({"人物"}, {"事件"}),
    "实施": ({"机构"}, {"事件"}),
    "搭载": ({"事件"}, {"装备"}),
    "拦截": ({"事件"}, {"装备"}),
    "发射": ({"事件"}, {"装备"}),
    "展示": ({"事件"}, {"装备"}),
    "生产": ({"事件"}, {"装备"}),
    "部署": ({"机构"}, {"装备"}),
    "从属": ({"机构"}, {"机构"}),
    "去向": ({"装备"}, {"地点", "机构"}),
    "发表": ({"人物", "机构", "平台"}, {"观点"}),
    "针对": ({"观点"}, {"事件"}),
    "表明": ({"人物", "机构"}, {"立场"}),
    "关于": ({"立场"}, {"事件"}),
    "处于": ({"事件"}, {"场景"}),
    "涉及": ({"事件"}, {"装备", "作品"}),
    "发布": ({"平台", "机构"}, {"事件"}),
}

# 由机构改平台时，这两类边改成发布。
MEDIA_RETYPE_REL = {"实施": "发布", "参与": "发布"}

# 同一文档内同名实体按此优先级只留一个类型。
# 事件是热点单元，优先；场景只描述事态场面；立场与观点是同句的不同切分。
TYPE_PRIORITY = {"事件": 5, "观点": 4, "立场": 3, "场景": 2, "地点": 1}


def _resolve_intra_doc(ents: list[dict]) -> tuple[list[dict], dict]:
    """同一文档内同名实体只保留优先级最高的类型。"""
    stats = Counter()
    by_name: dict[str, list[dict]] = defaultdict(list)
    for ent in ents:
        by_name[ent["name"]].append(ent)
    dropped_types: dict[str, set[str]] = defaultdict(set)
    keep_names: set[tuple[str, str]] = set()
    for name, group in by_name.items():
        types = {e["type"] for e in group}
        if len(types) <= 1 or not (types & set(TYPE_PRIORITY)):
            for e in group:
                keep_names.add((e["type"], name))
            continue
        best = max(group, key=lambda e: TYPE_PRIORITY.get(e["type"], 0))["type"]
        keep_names.add((best, name))
        for e in group:
            if e["type"] != best:
                dropped_types[name].add(e["type"])
                stats[f"drop_{e['type']}_dup"] += 1
    out = [e for e in ents if (e["type"], e["name"]) in keep_names]
    for name, lost in dropped_types.items():
        stats[f"resolved_{name}"] += 0
    return out, dict(stats)


def normalize_row(row: dict) -> tuple[dict, dict]:
    stats = Counter()
    ents = row.get("entities") or []
    # 名称 -> 归一后的类型
    final_type: dict[str, str] = {}
    for ent in ents:
        name, etype = ent.get("name") or "", ent.get("type") or ""
        if etype == "地点" and is_country(name):
            final_type[name] = "机构"
            stats["country_place_to_org"] += 1
        elif etype in ("机构", "平台") and is_media(name) and etype == "机构":
            final_type[name] = "平台"
            stats["media_org_to_platform"] += 1
        else:
            final_type.setdefault(name, etype)

    merged: dict[tuple[str, str], dict] = {}
    for ent in ents:
        name = ent.get("name") or ""
        etype = final_type.get(name, ent.get("type") or "")
        subtype = ""
        if etype == "机构" and is_country(name):
            subtype = "国家"
        key = (etype, name)
        node = merged.get(key)
        if node is None:
            node = {
                "type": etype,
                "name": name,
                "surface": ent.get("surface") or name,
                "aliases": list(ent.get("aliases") or []),
                "name_source": ent.get("name_source") or "",
                "chunk": ent.get("chunk", 0),
            }
            if ent.get("kind"):
                node["kind"] = ent["kind"]
            if subtype:
                node["subtype"] = subtype
            merged[key] = node
        else:
            for alias in ent.get("aliases") or []:
                if alias not in node["aliases"]:
                    node["aliases"].append(alias)
            if not node.get("kind") and ent.get("kind"):
                node["kind"] = ent["kind"]
            stats["merged_same_name"] += 1

    type_of = {}
    for (etype, name) in merged:
        type_of.setdefault(name, set()).add(etype)

    entities_out, intra_stats = _resolve_intra_doc(list(merged.values()))
    stats.update(intra_stats)
    # 合并后同名只剩一个类型
    type_of = {}
    for ent in entities_out:
        type_of.setdefault(ent["name"], set()).add(ent["type"])

    rels_out = []
    seen = set()
    for rel in row.get("relations") or []:
        name = rel.get("name") or ""
        head, tail = rel.get("head") or "", rel.get("tail") or ""
        head_types = type_of.get(head, set())
        tail_types = type_of.get(tail, set())
        if is_media(head) and name in MEDIA_RETYPE_REL:
            name = MEDIA_RETYPE_REL[name]
            stats["rel_retyped_to_publish"] += 1
        want_head, want_tail = DOMAIN.get(name, (set(), set()))
        if not (head_types & want_head) or not (tail_types & want_tail):
            stats["rel_dropped_after_retype"] += 1
            continue
        key = (name, head, tail)
        if key in seen:
            stats["rel_dup"] += 1
            continue
        seen.add(key)
        rels_out.append({**rel, "name": name})

    out = {**row, "entities": entities_out, "relations": rels_out}
    return out, dict(stats)


def _global_type_vote(rows: list[dict]) -> dict[str, str]:
    """全库同名实体只留一个类型：先按优先级，再按出现次数。"""
    tally: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for ent in row["entities"]:
            tally[ent["name"]][ent["type"]] += 1
    chosen: dict[str, str] = {}
    for name, c in tally.items():
        if len(c) == 1:
            chosen[name] = next(iter(c))
            continue
        ranked = sorted(
            c.items(),
            key=lambda kv: (TYPE_PRIORITY.get(kv[0], 0), kv[1]),
            reverse=True,
        )
        chosen[name] = ranked[0][0]
    return chosen


def run(src: Path, out: Optional[Path] = None) -> dict:
    if out is None:
        out = src.with_name(src.stem + "_typed.jsonl")
    total = Counter()
    rows_out = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        new_row, st = normalize_row(row)
        total.update(st)
        rows_out.append(new_row)

    # 全局同名同类型
    chosen = _global_type_vote(rows_out)
    for row in rows_out:
        kept = []
        for ent in row["entities"]:
            want = chosen.get(ent["name"], ent["type"])
            if ent["type"] != want:
                total["global_retyped"] += 1
                # 改类型后仍保留节点，别名与片段不变
                kept.append({**ent, "type": want, "retyped_from": ent["type"]})
            else:
                kept.append(ent)
        # 改类型后去掉重复节点
        uniq: dict[tuple[str, str], dict] = {}
        for ent in kept:
            key = (ent["type"], ent["name"])
            if key in uniq:
                for alias in ent.get("aliases") or []:
                    if alias not in uniq[key]["aliases"]:
                        uniq[key]["aliases"].append(alias)
                total["global_merged"] += 1
            else:
                uniq[key] = ent
        row["entities"] = list(uniq.values())

        # 按新类型复核关系两端
        type_of: dict[str, set[str]] = {}
        for ent in row["entities"]:
            type_of.setdefault(ent["name"], set()).add(ent["type"])
        rels = []
        seen = set()
        for rel in row.get("relations") or []:
            want_head, want_tail = DOMAIN.get(rel["name"], (set(), set()))
            ht = type_of.get(rel["head"], set())
            tt = type_of.get(rel["tail"], set())
            if not (ht & want_head) or not (tt & want_tail):
                total["global_rel_dropped"] += 1
                continue
            key = (rel["name"], rel["head"], rel["tail"])
            if key in seen:
                continue
            seen.add(key)
            rels.append(rel)
        row["relations"] = rels

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows_out:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    conflicts: dict[str, set[str]] = defaultdict(set)
    for row in rows_out:
        for ent in row["entities"]:
            conflicts[ent["name"]].add(ent["type"])
    left = {k: sorted(v) for k, v in conflicts.items() if len(v) > 1}
    rel_total = sum(len(r["relations"]) for r in rows_out)
    ent_total = sum(len(r["entities"]) for r in rows_out)
    return {
        "docs": len(rows_out),
        "entities": ent_total,
        "relations": rel_total,
        "actions": dict(total),
        "remaining_conflicts": left,
        "retyped_names": {n: t for n, t in sorted(chosen.items())
                          if t != "地点" and n in ("袭击", "空袭", "回应")},
        "out": str(out),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="类型归一")
    p.add_argument("--src", default=str(DATA / "extract" / "pilot10v3_zh.jsonl"))
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    res = run(Path(a.src), Path(a.out) if a.out else None)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
