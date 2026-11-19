"""实体归一与泛称过滤，抽取后处理层。

解决三个问题：
1. **简称与全称并存**：「以军」4 篇、「以色列国防军」28 篇，图谱里成了两个节点。
   这里把简称归到规范全称，原文形式记进 aliases。
2. **泛称设施被当实体**：「港口」「弹药库」「军事基地」无法参与国别归属，
   在图里只能是孤立节点。这类不建节点，只作论元里的字面值。
3. **装备只有通名**：「无人机」32 篇聚集大量边，但没有型号与归属。
   只有带型号或专名的才建装备节点。

设计原则：本层只做「合并」与「降级」，不新增任何实体，也不改事件和论元的值。
论元里若引用了被降级的泛称，值原样保留（它本来就是原文片段），只是不再有对应节点。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 别名归一

# 简称/别称 -> 规范全称。键必须能在原文里作为独立片段出现。
ORG_ALIAS = {
    # 军队简称
    "以军": "以色列国防军",
    "俄军": "俄罗斯武装力量",
    "乌军": "乌克兰武装部队",
    "美军": "美国武装力量",
    "叙军": "叙利亚武装力量",
    "伊军": "伊朗武装力量",
    "土军": "土耳其武装力量",
    # 武装组织
    "黎真主党": "黎巴嫩真主党",
    "真主党": "黎巴嫩真主党",
    "胡塞武装": "也门胡塞武装",
    "胡塞": "也门胡塞武装",
    "哈马斯": "巴勒斯坦伊斯兰抵抗运动（哈马斯）",
    "巴勒斯坦伊斯兰抵抗运动": "巴勒斯坦伊斯兰抵抗运动（哈马斯）",
    "杰哈德": "巴勒斯坦伊斯兰圣战组织（杰哈德）",
    "巴勒斯坦伊斯兰圣战组织": "巴勒斯坦伊斯兰圣战组织（杰哈德）",
    "伊斯兰国": "极端组织“伊斯兰国”",
    "ISIS": "极端组织“伊斯兰国”",
    "IS": "极端组织“伊斯兰国”",
    # 机构简称
    "五角大楼": "美国国防部",
    "俄国防部": "俄罗斯国防部",
    "美国防部": "美国国防部",
    "以色列国防部": "以色列国防部",
    "伊朗革命卫队": "伊朗伊斯兰革命卫队",
    "革命卫队": "伊朗伊斯兰革命卫队",
    "海合会": "海湾阿拉伯国家合作委员会",
    "北约": "北大西洋公约组织",
    "联合国安理会": "联合国安全理事会",
    "安理会": "联合国安全理事会",
    "国际联盟": "打击“伊斯兰国”国际联盟",
    "反恐联盟": "打击“伊斯兰国”国际联盟",
    "俄空天军": "俄罗斯空天军",
    "乌空军": "乌克兰空军",
    "俄海军": "俄罗斯海军",
    "乌海军": "乌克兰海军",
}

# 归一后仍视为机构（用于类型修正）
ALIAS_AS_ORG = set(ORG_ALIAS.values())

# ---------------------------------------------------------------- 泛称过滤

# 无法参与国别归属的泛称设施。这些不作为实体节点。
# 只收「纯通名」：本身就是一个类别词，没有任何专名成分。
# 「加沙地带」「那霸基地」「扎波罗热核电站」这类带专名的不是泛称，必须在表外。
GENERIC_PLACE = {
    "港口", "机场", "基地", "军事基地", "空军基地", "海军基地", "美军基地",
    "弹药库", "武器库", "军火库", "油库", "储油基地", "储油设施",
    "石油设施", "石油化工设施", "油气设施", "能源设施", "能源基础设施",
    "交通基础设施", "基础设施", "民用设施", "设施", "物流中心", "后勤物流中心",
    "指挥中心", "指挥所", "总部", "军事指挥中心", "通信中心", "医疗中心",
    "炼油厂", "工厂",    "居民区", "住宅区", "大楼", "建筑物", "市中心", "泊位设施",
    "训练场", "阵地", "据点", "营地",
}

# 装备通名：没有型号、没有专名，只有类别。不建装备节点。
GENERIC_EQUIP = {
    "无人机", "导弹", "防空导弹", "弹道导弹", "巡航导弹", "高超音速导弹",
    "战机", "战斗机", "轰炸机", "攻击机", "运输机", "直升机", "武装直升机",
    "坦克", "装甲车", "装甲车辆", "步兵战车", "火炮", "榴弹炮", "火箭炮",
    "军舰", "舰艇", "战舰", "航母", "航空母舰", "驱逐舰", "护卫舰", "潜艇",
    "雷达", "防空系统", "反导系统", "卫星", "火箭弹", "炮弹", "炸弹",
    "防空导弹系统", "武器", "装备", "军事装备", "弹药", "制导导弹",
}

# 带这些后缀的即使命中通名表也算专名，如「S-400 防空系统」「林肯号」「爱国者防空系统」
PROPER_HINTS = re.compile(
    r"[A-Z]{1,4}[- ]?\d"          # F-35 / MQ-9 / S-400
    r"|[\u4e00-\u9fff]{1,6}号"      # 林肯号 / 邓肯号 / 德鲁伊特号
    r"|(?:爱国者|萨德|萨姆|箭式|铁穹|大卫投石索|铠甲|道尔|山毛榉|伊斯坎德尔|"
    r"口径|匕首|锆石|战斧|风暴阴影|流星|地狱火|杰达姆|小直径炸弹)"
)

# 泛称前的修饰词只能是国名/机构名这类限定，且整体必须是「X + 纯通名」。
# 用于「伊朗港口」「乌克兰物流中心」这类，但排除「加沙地带」「那霸基地」——
# 因为「地带」「基地」在表里，而修饰词是专名的一部分，无法与类别词分离。
# 因此这里只对明确由「国名 + 通名」构成的才降级。
_COUNTRY_SUFFIX = re.compile(
    r"^(?:[\u4e00-\u9fff]{2,6})(?:境内|境内同类|境内多处)?(?:"
    + "|".join(sorted(GENERIC_PLACE - {"基地", "地带", "地区"}, key=len, reverse=True))
    + r")$"
)


def normalize_org(name: str) -> str:
    """机构名归一：简称折算成规范全称，未收录的原样返回。"""
    n = (name or "").strip()
    return ORG_ALIAS.get(n, n)


def is_generic_place(name: str) -> bool:
    """是否为不建节点的泛称设施。

    只降级「纯通名」。带任何专名前缀的（纳坦兹核设施、那霸基地、加沙地带）
    一律保留——前缀究竟是国家修饰还是地名的一部分，无法可靠区分，
    误杀专名设施的代价比多留几个泛称节点大得多。
    """
    n = (name or "").strip()
    if not n:
        return False
    return n in GENERIC_PLACE


def is_generic_equipment(name: str) -> bool:
    """是否为只有通名、没有型号或专名的装备。"""
    n = (name or "").strip()
    if not n:
        return False
    if PROPER_HINTS.search(n):
        return False
    if n in GENERIC_EQUIP:
        return True
    # 「军用无人机」「自杀式无人机」这类修饰形式
    if len(n) <= 12 and any(n.endswith(g) for g in GENERIC_EQUIP):
        return True
    return False


def drop_reason(entity: dict) -> str:
    """该实体是否应降级为字面值。返回原因，空字符串表示保留。"""
    t = entity.get("type") or ""
    name = entity.get("text") or ""
    if t == "地点" and is_generic_place(name):
        return "generic_place"
    if t == "装备" and is_generic_equipment(name):
        return "generic_equipment"
    return ""


# ---------------------------------------------------------------- 后处理入口

def apply(rows: list[dict]) -> tuple[list[dict], dict]:
    """对抽取结果做归一并降级泛称。

    返回 (新结果, 统计)。不修改入参。
    """
    from collections import Counter

    stat = Counter()
    out = []
    for row in rows:
        new = dict(row)
        # 1. 实体层：归一 + 降级
        ents = []
        alias_map: dict[str, str] = {}   # 原文形式 -> 规范名
        dropped: set[str] = set()
        for e in row.get("entities") or []:
            e = dict(e)
            name, typ = e.get("text") or "", e.get("type") or ""
            norm = normalize_org(name) if typ in ("机构", "国家") else name
            if norm != name:
                alias_map[name] = norm
                stat["org_normalized"] += 1
                e["text"] = norm
                e["type"] = "机构"
                e.setdefault("aliases", [])
                if name not in e["aliases"]:
                    e["aliases"].append(name)
            reason = drop_reason(e)
            if reason:
                stat[reason] += 1
                dropped.add(name)
                dropped.add(e.get("text") or "")
                continue
            ents.append(e)

        # 合并归一后重复的实体（如本篇同时有「以军」与「以色列国防军」）
        merged: dict[tuple[str, str], dict] = {}
        for e in ents:
            k = (e.get("text") or "", e.get("type") or "")
            if k in merged:
                al = merged[k].setdefault("aliases", [])
                for a in e.get("aliases") or []:
                    if a not in al:
                        al.append(a)
                stat["entity_merged"] += 1
            else:
                merged[k] = e
        new["entities"] = list(merged.values())

        # 2. 关系层：端点归一。
        # 泛称端点不丢关系——改成字面值标记，信息保留但不建节点。
        rels = []
        for r in row.get("relations") or []:
            r = dict(r)
            h, t = r.get("head") or "", r.get("tail") or ""
            nh, nt = normalize_org(h), normalize_org(t)
            if nh in dropped:
                stat["relation_head_literal"] += 1
                r["head_literal"] = True
            if nt in dropped:
                stat["relation_tail_literal"] += 1
                r["tail_literal"] = True
            r["head"], r["tail"] = nh, nt
            rels.append(r)
        new["relations"] = rels

        # 3. 事件与论元：事件文本不改（它是原文片段），论元值归一到规范名
        evs = []
        for ev in row.get("events") or []:
            ev = dict(ev)
            # 事件文本若等于某个简称，改用规范名（少见，但保持一致）
            if ev.get("text") in alias_map:
                ev["text"] = alias_map[ev["text"]]
            evs.append(ev)
        new["events"] = evs

        out.append(new)
    return out, dict(stat)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description="实体归一与泛称过滤")
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rows = []
    for line in Path(a.src).open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    new_rows, stat = apply(rows)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as g:
        for r in new_rows:
            g.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(json.dumps(stat, ensure_ascii=False, indent=2))
    print(f"\n已写 {out}（{len(new_rows)} 篇）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
