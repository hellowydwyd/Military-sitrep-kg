"""Location 对齐：候选对 + 规则预标 + 交给 LLM。不写回图。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from align.cluster import pair_key
from align.methods import COUNTRYISH
from align.normalize import normalize

ALIAS_SUFFIX = ("市", "县", "镇")
FEATURE_MARKERS = (
    "海域",
    "领空",
    "领海",
    "机场",
    "基地",
    "口岸",
    "边境",
    "海峡",
    "群岛",
    "岛",
    "空军",
    "建筑",
    "航母",
    "战列舰",
    "训练中心",
    "军事设施",
    "省",
    "州",
    "境内",
    "战情室",
)
GENERIC_LOCS = {
    "该基地",
    "海上",
    "舰体中部",
    "基地",
    "机场",
    "地区",
    "海域",
    "前线",
    "现场",
}
GENERIC_CORES = {
    "空军基地",
    "军用机场",
    "军事设施",
    "训练中心",
    "国际口岸",
    "安全缓冲",
    "空军某基",
}
NOT_PLACE_RE = re.compile(r"声明|社交媒体|报道称|声明中|战事")
ANAPHOR_RE = re.compile(
    r"(上述)?(两处|两个|多个|多处|相关)(区域|地区|地点)|一些和平集会现场"
)
COMPOUND_MARK = ("、", "以及", "等地", "等国", "等俄")
REFUSE_SAME_MARK = (
    "一栋建筑",
    "等地",
    "等国",
    "等俄",
    "航母",
    "战列舰",
    "声明",
    "舰体",
    "战事",
)
WATER_ALIAS = ("海域", "水域", "海面")
WATER_CORE = ("海", "峡", "以东", "以西", "以南", "以北", "近海")
SET_SUFFIX = ("国家", "各国")
CITY_VS_ZONE = (("城", "地带"), ("市", "地带"))
REGIONISH = set(COUNTRYISH) | {
    "中东",
    "亚洲",
    "欧洲",
    "非洲",
    "印度",
    "叙利亚",
    "埃及",
    "澳大利亚",
    "沙特",
    "也门",
    "阿曼",
    "科威特",
    "约旦",
    "卡塔尔",
    "阿联酋",
    "巴林",
    "海湾",
}

LOC_SYSTEM = """你是军事新闻地点对齐裁判。只判断两个名称是不是同一个地方的不同写法。
same_as：同一地点的别称（基辅=基辅市=乌克兰首都基辅；中东=中东地区；台岛以东=台岛以东海域）。
related：包含、毗邻或不同类型的地理对象（黎巴嫩南部≠黎巴嫩；中东≠中东海域；纳季兰≠纳季兰机场；加沙城≠加沙地带）。
distinct：不同地方（霍尔木兹海峡≠曼德海峡；基辅≠霍尔木兹海峡）。
并列清单（A、B以及C等地）不要并成其中某一个城市。泛称（两处区域、多个地点）不要并进专名。
拿不准选 related。输出 JSON：{"pairs":[{"a":"...","b":"...","label":"same_as|related|distinct"}]}"""

LOC_EXAMPLES = (
    "例子：基辅/基辅市 → same_as；台岛以东/台岛以东海域 → same_as；"
    "中东/中东海域 → related；加沙城/加沙地带 → related；霍尔木兹海峡/曼德海峡 → distinct。"
)


def is_garbage(name: str) -> bool:
    n = name or ""
    return bool(NOT_PLACE_RE.search(n) or ANAPHOR_RE.search(n))


def is_generic(name: str) -> bool:
    return (name or "").strip() in GENERIC_LOCS


def is_compound(name: str) -> bool:
    return any(m in (name or "") for m in COMPOUND_MARK)


def refuse_same(name: str) -> bool:
    return is_compound(name) or any(m in (name or "") for m in REFUSE_SAME_MARK)


def loc_stem(name: str) -> str:
    t = normalize(name) or (name or "")
    t = re.sub(r"^(乌克兰首都|首都)", "", t)
    for suf in ALIAS_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf) + 1:
            t = t[: -len(suf)]
            break
    for suf in ("地区", "一带"):
        if t.endswith(suf) and len(t) > len(suf):
            stem = t[: -len(suf)]
            if stem in REGIONISH or len(stem) <= 2:
                t = stem
            break
    for suf in WATER_ALIAS:
        if t.endswith(suf) and len(t) > len(suf):
            stem = t[: -len(suf)]
            if any(k in stem for k in WATER_CORE):
                t = stem
            break
    return t


def has_feature(name: str) -> bool:
    return any(m in (name or "") for m in FEATURE_MARKERS)


def pairable(name: str) -> bool:
    n = (name or "").strip()
    if len(n) < 2:
        return False
    if is_garbage(n) or is_generic(n):
        return False
    return True


def _shared_core(a: str, b: str, n: int = 5) -> bool:
    na, nb = normalize(a) or a, normalize(b) or b
    if min(len(na), len(nb)) < n:
        return False
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    for i in range(len(shorter) - n + 1):
        core = shorter[i : i + n]
        if core in longer and core not in GENERIC_CORES and not any(
            g in core for g in ("基地", "机场", "地区", "海域", "空军")
        ):
            return True
    return False


def loc_candidate_pairs(names: list[str]) -> list[tuple[str, str]]:
    """归一全等、去市/地区后全等、短串在长串里、共享≥4字词干、编辑相近。"""
    usable = [n for n in names if pairable(n)]
    out: set[tuple[str, str]] = set()
    norms = [(n, loc_stem(n), normalize(n) or n) for n in usable]
    for i, (a, sa, na) in enumerate(norms):
        for b, sb, nb in norms[i + 1 :]:
            if na == nb and a != b:
                out.add(pair_key(a, b))
                continue
            if sa and sa == sb:
                out.add(pair_key(a, b))
                continue
            short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
            if len(short) >= 2 and short in long_:
                out.add(pair_key(a, b))
                continue
            if _shared_core(a, b):
                out.add(pair_key(a, b))
                continue
            if min(len(na), len(nb)) > 1 and SequenceMatcher(None, na, nb).ratio() >= 0.72:
                out.add(pair_key(a, b))
    return sorted(out)


def loc_prelabel(a: str, b: str) -> tuple[str, str] | None:
    """规则能定的不送 LLM。包含≠同一点。"""
    if refuse_same(a) or refuse_same(b) or is_garbage(a) or is_garbage(b):
        na, nb = normalize(a) or a, normalize(b) or b
        if na in nb or nb in na:
            return ("related", "compound")
        return ("distinct", "compound")
    if _city_vs_zone(a, b):
        return ("related", "city_zone")
    if _set_suffix(a, b):
        return ("related", "set_suffix")
    sa, sb = loc_stem(a), loc_stem(b)
    fa, fb = has_feature(a), has_feature(b)
    if sa and sa == sb:
        if fa == fb or _water_alias_only(a, b):
            return ("same_as", "alias_suffix")
        return ("related", "stem_feature")
    na, nb = normalize(a) or a, normalize(b) or b
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short in REGIONISH and short in long_ and len(short) < len(long_):
        return ("related", "region_substr")
    if len(short) >= 2 and short in long_ and (fa != fb or has_feature(a) or has_feature(b)):
        if _water_alias_only(a, b):
            return ("same_as", "water_alias")
        return ("related", "contain_feature")
    return None


def _city_vs_zone(a: str, b: str) -> bool:
    for city, zone in CITY_VS_ZONE:
        if (a.endswith(city) and b.endswith(zone)) or (b.endswith(city) and a.endswith(zone)):
            return True
    return False


def _set_suffix(a: str, b: str) -> bool:
    for suf in SET_SUFFIX:
        if a.endswith(suf) and (b == a[: -len(suf)] or loc_stem(b) == loc_stem(a[: -len(suf)])):
            return True
        if b.endswith(suf) and (a == b[: -len(suf)] or loc_stem(a) == loc_stem(b[: -len(suf)])):
            return True
    return False


def _water_alias_only(a: str, b: str) -> bool:
    na, nb = normalize(a) or a, normalize(b) or b
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    for suf in WATER_ALIAS:
        if long_ == short + suf and any(k in short for k in WATER_CORE):
            return True
    return loc_stem(a) == loc_stem(b) and loc_stem(a) != "" and any(
        long_.endswith(s) and not short.endswith(s) for s in WATER_ALIAS
    )


def refine_loc_cluster(members: list[str]) -> list[str]:
    """并查集之后丢掉垃圾、并列清单、集合词、城vs地带里的城。"""
    kept = [
        m
        for m in members
        if not is_garbage(m) and not is_generic(m) and not is_compound(m)
    ]
    drop: set[str] = set()
    for m in kept:
        if any(m.endswith(s) for s in SET_SUFFIX):
            drop.add(m)
        if m.endswith("城") and any(x.endswith("地带") for x in kept if x != m):
            drop.add(m)
    kept = [m for m in kept if m not in drop]
    return kept if len(kept) > 1 else []
