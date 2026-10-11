"""从事件槽位收有向关系。共现不算关系。"""

from __future__ import annotations

from actor.clean import COUNTRY_TO_ARMY, army_canon
from actor.types import COUNTRIES

KINDS = (
    (
        "attacks",
        "打击",
        ("袭击", "打击", "空袭", "轰炸", "发射", "拦截", "击落", "摧毁", "击中", "发动", "遭袭", "开火", "射击", "驱离"),
    ),
    (
        "opposes",
        "对立",
        ("抗议", "对抗", "对峙", "抵制", "制裁", "谴责", "反对"),
    ),
    (
        "cooperates",
        "合作",
        ("访问", "会见", "会谈", "合作", "联合", "签署", "援助"),
    ),
)

KIND_ZH = {k: zh for k, zh, _ in KINDS}
KIND_ZH["belongs_to"] = "从属"

ARMY_TO_COUNTRY = {
    army: country
    for country, army in COUNTRY_TO_ARMY.items()
    if len(country) >= 2 and country in COUNTRIES
}
ARMY_TO_COUNTRY.update(
    {
        "解放军": "中国",
        "自卫队": "日本",
        "伊朗伊斯兰革命卫队": "伊朗",
    }
)


def classify_trigger(trigger: str, event_kind: str = "") -> str | None:
    t = (trigger or "").strip()
    if not t or "暂停" in t:
        return None
    if "达成协议" in t or t.startswith("达成"):
        return None
    # 表态是口径，不画成打击/对立/合作。威胁、警告、谴责几乎全是这类。
    if (event_kind or "").strip() == "表态":
        return None
    if event_kind == "计划" and any(w in t for w in ("威胁", "警告", "谴责", "敦促")):
        return None
    for kind, _zh, words in KINDS:
        if any(w in t for w in words):
            return kind
    return None


def belong_country(name: str, typ: str) -> str | None:
    if typ not in ("Org", "MilitaryEntity"):
        return None
    if name.startswith("美军"):
        return "美国"
    if name.startswith("俄军"):
        return "俄罗斯"
    if name.startswith("以军"):
        return "以色列"
    mapped = ARMY_TO_COUNTRY.get(name) or ARMY_TO_COUNTRY.get(army_canon(name) or "")
    if mapped:
        return mapped
    for c in sorted(COUNTRIES, key=len, reverse=True):
        if len(c) >= 2 and name.startswith(c) and name != c:
            return c
    return None
