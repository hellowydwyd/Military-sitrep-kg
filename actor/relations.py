"""从事件槽位收有向关系。共现不算关系。"""

from __future__ import annotations

from actor.clean import COUNTRY_TO_ARMY, army_canon
from actor.types import COUNTRIES

# 先匹配更具体的词。「联合打击」归打击，不归合作。
KINDS = (
    ("intercepts", "拦截", ("拦截", "击落")),
    ("launches", "发射", ("发射",)),
    ("strikes", "打击", ("袭击", "打击", "空袭", "轰炸", "摧毁", "击中", "遭袭", "开火", "射击", "驱离")),
    ("deploys", "部署", ("部署", "驻扎", "进驻", "开赴", "派遣")),
    ("withdraws", "撤离", ("撤离", "撤出", "撤军", "返航", "后撤")),
    ("sanctions", "制裁", ("制裁", "抵制")),
    ("condemns", "谴责", ("谴责", "抗议", "反对", "敦促")),
    ("threatens", "威胁", ("威胁", "警告")),
    ("confronts", "对峙", ("对抗", "对峙")),
    ("meets", "会谈", ("访问", "会见", "会谈")),
    ("aids", "援助", ("援助", "军援")),
    ("signs", "签署", ("签署",)),
    ("cooperates", "合作", ("合作", "联合")),
)
# 表态、计划只保留口头行为本身。签署、会谈、援助还没发生，不能画成既成事实。
SPEECH_KINDS = frozenset({"condemns", "threatens"})

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


def classify_trigger(trigger: str, event_kind: str = "", evidence: str = "") -> str | None:
    t = (trigger or "").strip()
    if not t or "暂停" in t:
        return None
    if "达成协议" in t or t.startswith("达成"):
        return None
    kind = ""
    for name, _zh, words in KINDS:
        if any(w in t for w in words):
            kind = name
            break
    if not kind:
        return None
    window = t
    ev = evidence or ""
    if t and t in ev:
        i = ev.find(t)
        window = ev[max(0, i - 12) : i + len(t) + 8]
    if any(w in window for w in ("不会", "尚未", "拒绝", "否认", "未签署", "不签署", "没有签署")):
        if kind in ("signs", "meets", "aids", "cooperates", "deploys"):
            return None
    if (event_kind or "").strip() in ("表态", "计划") and kind not in SPEECH_KINDS:
        return None
    return kind


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
