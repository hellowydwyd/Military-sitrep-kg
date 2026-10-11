"""表面名清洗：去数量词、丢掉事件短语。不把国家并进该国军队。"""

from __future__ import annotations

import re

from actor.aliases import en_canon_of, is_en_generic
from actor.types import COUNTRIES, COUNTRY_ALIAS, GENERIC_MIL, ORG_SEED, PERSON_SEED, country_of

QUANT = re.compile(
    r"^(?:约|近|至多|最多|超过|逾|首批)?"
    r"(?:\d+(?:\.\d+)?(?:枚|架|艘|辆|套|台|门|名|个|次|座|批)?|"
    r"多枚|数枚|多架|数架|多艘|数艘|数辆|多辆|多套|一批|一组|一台|一枚|一架|一艘)"
)

GENERIC_CLASS = GENERIC_MIL | {
    "弹道导弹",
    "巡航导弹",
    "高超音速导弹",
    "防空导弹",
    "反舰导弹",
    "空对空导弹",
    "中程导弹",
    "远程武器",
    "制导弹药",
    "精确制导弹药",
    "战术核武器",
    "核武器",
    "战斗机",
    "轰炸机",
    "加油机",
    "空中加油机",
    "单向攻击无人机",
    "固定翼无人机",
    "拦截弹",
    "防空系统",
    "反导系统",
    "潜射战略导弹",
    "航空制导炸弹",
    "遥控无人运输载具",
}

CLAUSE_MARK = (
    "协议",
    "备忘录",
    "预算",
    "白皮书",
    "条款",
    "程序",
    "磋商",
    "邀请",
    "分歧",
    "言论",
    "任期",
    "顾问",
    "要求",
    "威胁等级",
    "重新开放",
    "消息人士",
    "有关方面",
    "相关人士",
    "打击行动",
    "军事行动",
    "之间",
    "规模",
    "内容",
    "问题",
    "安排",
    "机制",
    "货物",
    "航运",
    "安全控制",
    "多次警告",
    "目标",
    "许可",
    "授予",
    "授权",
    "兵力",
    "士兵",
    "将被替换",
    "命名",
    "谈判信号",
    "训练任务",
    "数十个",
    "公共关系",
)

ARMY_SUFFIX = (
    "联邦国防军",
    "武装部队",
    "武装力量",
    "国防军",
    "军方",
    "军队",
)

COUNTRY_TO_ARMY = {
    "美国": "美军",
    "美": "美军",
    "俄罗斯": "俄军",
    "俄": "俄军",
    "俄联邦": "俄军",
    "乌克兰": "乌军",
    "乌": "乌军",
    "以色列": "以军",
    "以": "以军",
    "伊朗": "伊军",
    "伊": "伊军",
    "中国": "解放军",
    "日本": "自卫队",
    "韩国": "韩军",
    "韩": "韩军",
    "朝鲜": "朝军",
    "英国": "英军",
    "英": "英军",
    "法国": "法军",
    "法": "法军",
    "叙利亚": "叙军",
    "叙": "叙军",
    "土耳其": "土军",
    "土": "土军",
    "黎巴嫩": "黎军",
    "黎": "黎军",
    "德国": "德军",
    "德": "德军",
    "科威特": "科军",
    "科": "科军",
    "沙特": "沙特军队",
    "沙特阿拉伯": "沙特军队",
    "马里": "马里军方",
    "约旦": "约旦武装部队",
    "巴基斯坦": "巴基斯坦军方",
    "苏丹": "苏丹武装部队",
    "印尼": "印尼军方",
    "印度尼西亚": "印尼军方",
}

PERSON_PREFIX = re.compile(
    r"^(?:中国|美国|俄罗斯|乌克兰|以色列|伊朗|日本|韩国|朝鲜|英国|法国|德国|"
    r"中|美|俄|乌|以|伊|日|韩|英|法|德)?"
    r"(?:国防部|外交部|武装部队|武装力量)?"
    r"(?:总统|总理|首相|防长|外长|外交部长|国防部长|部长|新闻发言人|发言人|"
    r"总参谋长|司令|高官|陛下|大校)?"
    r"的?$"
)

NAMED_EQUIP = re.compile(
    r"号(?:航母|驱逐舰|护卫舰|巡洋舰|潜艇|舰)?|号$|"
    r"F-\d|MQ-\d|歼-\d|轰-\d|运-\d|苏-\d|KC-\d|"
    r"爱国者|萨德|阿帕奇|死神|黑鹰|海马斯|朱姆沃尔特|尼米兹|福特级"
)

SHIP_STRIP = (
    "中国人民解放军海军",
    "美国海军",
    "中国海军",
    "美海军",
    "印尼海军",
    "印度尼西亚海军",
    "越南海军",
    "近卫巡洋舰",
    "核动力潜艇",
    "导弹驱逐舰",
    "两栖攻击舰",
    "第四艘航母",
    "美军",
    "美国",
    "中方",
)
SHIP_CORE = re.compile(r"([\u4e00-\u9fffA-Za-z·．.\-]{1,16}号)(?:航母|驱逐舰|护卫舰|巡洋舰|潜艇|核潜艇|舰)?")

SYSTEM_KEYS = (
    ("F-35", "F-35"),
    ("F-16", "F-16"),
    ("MQ-9", "MQ-9"),
    ("MQ－9", "MQ-9"),
    ("爱国者", "爱国者"),
    ("萨德", "萨德"),
    ("阿帕奇", "阿帕奇"),
    ("黑鹰", "黑鹰"),
    ("海马斯", "海马斯"),
)


def strip_quotes(text: str) -> str:
    t = (text or "").strip()
    t = t.replace(" ", "").replace("\u3000", "")
    for q in "\"'“”‘’「」『』«»":
        t = t.replace(q, "")
    t = re.sub(r"[（(][^）)]{0,16}[）)]", "", t)
    return t.strip("·•.,;:：")


def strip_quantifier(text: str) -> str:
    t = text
    for _ in range(2):
        nxt = QUANT.sub("", t, count=1)
        if nxt == t:
            break
        t = nxt
    return t.strip("的")


KNOWN_ARMY = set(COUNTRY_TO_ARMY.values()) | {
    "美军",
    "俄军",
    "乌军",
    "以军",
    "伊军",
    "韩军",
    "日军",
    "英军",
    "法军",
    "叙军",
    "土军",
    "黎军",
    "朝军",
    "德军",
    "科军",
    "解放军",
    "自卫队",
}


def army_canon(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    if t in KNOWN_ARMY:
        return t
    for suf in ARMY_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf):
            head = t[: -len(suf)]
            if head in COUNTRY_TO_ARMY:
                return COUNTRY_TO_ARMY[head]
            mapped = COUNTRY_ALIAS.get(head) or country_of(head)
            if mapped and mapped in COUNTRY_TO_ARMY:
                return COUNTRY_TO_ARMY[mapped]
            if mapped and mapped in COUNTRIES:
                return COUNTRY_TO_ARMY.get(mapped)
    return None


def person_seed_of(text: str) -> str | None:
    t = (text or "").strip()
    if t in PERSON_SEED:
        return t
    for seed in sorted(PERSON_SEED, key=len, reverse=True):
        if len(seed) < 2 or not t.endswith(seed) or t == seed:
            continue
        prefix = t[: -len(seed)]
        if "和" in prefix or "政府" in t or "顾问" in t:
            continue
        if PERSON_PREFIX.fullmatch(prefix):
            return seed
    return None


def is_clause(text: str) -> bool:
    t = text
    if any(m in t for m in CLAUSE_MARK):
        return True
    if re.search(r"[和及与]", t) and t not in ORG_SEED:
        return True
    if t.startswith(("针对", "持续", "重新", "打造", "一项", "与美国", "对伊", "对霍", "组建", "在", "授予", "授权", "升级", "所有")):
        return True
    if any(x in t for x in ("抵近", "编队", "首批", "新航母", "昂贵驱逐舰", "型防空驱逐舰")):
        return True
    if "的" in t and not person_seed_of(t) and not t.endswith(("司令部", "防卫省", "国防部", "外交部")):
        if not army_canon(t):
            return True
    return False


def keep_surface(text: str, role: str = "") -> bool:
    t = (text or "").strip()
    if len(t) < 2 or len(t) > 28:
        return False
    if t in GENERIC_CLASS:
        return False
    if is_clause(t):
        return False
    if role == "装备" and not NAMED_EQUIP.search(t):
        return False
    if t.endswith(("无人机", "导弹", "战机", "弹药", "雷达")) and not NAMED_EQUIP.search(t):
        return False
    if t.startswith(("此次", "该项", "该", "其", "一名", "一架", "一部", "一艘", "三艘", "数艘")):
        return False
    if t.endswith(("商船", "油轮", "桥梁", "淡化厂", "快艇", "部队")) and not t.endswith(("革命卫队", "武装部队")):
        return False
    return True


def ship_canon(text: str) -> str | None:
    m = SHIP_CORE.search(text or "")
    if not m:
        return None
    core = m.group(1).lstrip("的")
    for pref in SHIP_STRIP:
        if core.startswith(pref) and len(core) > len(pref) + 1:
            core = core[len(pref) :]
            break
    if len(core) < 2 or core == "号":
        return None
    return core


def system_canon(text: str) -> str | None:
    t = text or ""
    for key, canon in SYSTEM_KEYS:
        if key in t:
            return canon
    return None


def canonicalize_surface(raw: str, role: str = "") -> str | None:
    t = strip_quotes(raw)
    t = strip_quantifier(t)
    mapped = en_canon_of(t)
    if mapped:
        return mapped
    if is_en_generic(t):
        return None
    if not keep_surface(t, role):
        return None
    host = person_seed_of(t)
    if host:
        return host
    if role == "装备" or NAMED_EQUIP.search(t):
        sysn = system_canon(t)
        if sysn:
            return sysn
        ship = ship_canon(t)
        if ship:
            return ship
    return t
