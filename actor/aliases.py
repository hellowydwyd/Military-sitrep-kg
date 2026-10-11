"""种子别名。只并同一对象的写法，国家与该国军队不合。"""

from __future__ import annotations

import re

# 每组第一项是展示名
SEED_GROUPS: list[list[str]] = [
    ["美国", "美方", "美政府"],
    ["中国", "中方"],
    ["俄罗斯", "俄方", "俄联邦"],
    ["乌克兰", "乌方"],
    ["以色列", "以方"],
    ["日本", "日方"],
    ["韩国", "韩方"],
    ["伊朗", "伊方"],
    ["叙利亚", "叙方"],
    ["土耳其", "土方"],
    ["英国", "英方"],
    ["法国", "法方"],
    ["德国", "德方"],
    ["以军", "以战机", "以色列国防军", "以色列军队", "IDF", "以空军", "以色列军方", "以军方"],
    ["俄军", "俄联邦武装力量", "俄罗斯军队", "俄空天军", "俄罗斯军方", "俄武装部队", "俄武装力量", "俄罗斯武装部队", "俄罗斯武装力量"],
    ["乌军", "乌克兰武装部队", "乌克兰空军", "乌空军", "乌克兰军队", "乌克兰军方", "乌国防军"],
    ["美军", "美国军队", "美军方", "美国军方"],
    ["伊军", "伊朗军方", "伊朗军队", "伊朗武装部队", "伊朗武装力量"],
    ["韩军", "韩国军方", "韩军方"],
    ["黎军", "黎巴嫩军队", "黎巴嫩军方", "黎巴嫩武装部队"],
    ["德军", "德国联邦国防军", "联邦国防军"],
    ["科军", "科威特军方", "科军方", "科威特武装部队"],
    ["沙特军队", "沙特军方"],
    ["马里军方", "马里武装部队"],
    ["约旦武装部队"],
    ["巴基斯坦军方"],
    ["苏丹武装部队", "苏丹军方"],
    ["印尼军方"],
    ["解放军", "中国军队", "中国军方"],
    ["自卫队", "日本自卫队", "航空自卫队"],
    ["特朗普", "美国总统特朗普", "唐纳德·特朗普", "特朗普总统"],
    ["泽连斯基", "乌克兰总统泽连斯基", "乌总统泽连斯基"],
    ["内塔尼亚胡", "以总理内塔尼亚胡", "以色列总理内塔尼亚胡"],
    ["真主党", "黎巴嫩真主党"],
    ["胡塞武装", "胡塞", "也门胡塞武装"],
    ["哈马斯", "巴勒斯坦伊斯兰抵抗运动"],
    ["北约", "北大西洋公约组织"],
    ["沙特阿拉伯", "沙特"],
    ["伊朗伊斯兰革命卫队", "革命卫队", "伊朗革命卫队", "伊斯兰革命卫队"],
    ["董军", "国防部长董军", "中国国防部长董军"],
    ["普京", "俄罗斯总统普京", "俄总统普京"],
    ["阿拉格齐", "伊朗外交部长阿拉格齐", "伊朗外长阿拉格齐"],
    ["巴加埃", "伊朗外交部发言人巴加埃"],
    ["日本防卫省", "防卫省"],
    ["韩国联合参谋本部", "韩军联参", "联参"],
]


def latin_key(text: str) -> str:
    """去标点空格后的小写拉丁键。U.S.forces / The IDF 都能对上。"""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# 英文表面 → 中文规范名。键是 latin_key，须覆盖去空格后的写法。
EN_CANON: dict[str, str] = {
    "russia": "俄罗斯",
    "ukraine": "乌克兰",
    "iran": "伊朗",
    "israel": "以色列",
    "us": "美国",
    "usa": "美国",
    "unitedstates": "美国",
    "america": "美国",
    "iraq": "伊拉克",
    "yemen": "也门",
    "saudi": "沙特阿拉伯",
    "saudiarabia": "沙特阿拉伯",
    "singapore": "新加坡",
    "argentina": "阿根廷",
    "china": "中国",
    "japan": "日本",
    "idf": "以军",
    "theidf": "以军",
    "israeldefenseforces": "以军",
    "israelidefenseforces": "以军",
    "israelmilitary": "以军",
    "israelimilitary": "以军",
    "irgc": "伊朗伊斯兰革命卫队",
    "theirgc": "伊朗伊斯兰革命卫队",
    "islamicrevolutionaryguardcorps": "伊朗伊斯兰革命卫队",
    "revolutionaryguards": "伊朗伊斯兰革命卫队",
    "revolutionaryguard": "伊朗伊斯兰革命卫队",
    "houthis": "胡塞武装",
    "thehouthis": "胡塞武装",
    "houthi": "胡塞武装",
    "houthirebels": "胡塞武装",
    "houthimovement": "胡塞武装",
    "ansarallah": "胡塞武装",
    "usforces": "美军",
    "usforce": "美军",
    "usmilitary": "美军",
    "usarmy": "美军",
    "americanforces": "美军",
    "theusnavy": "美军",
    "usnavy": "美军",
    "theusforces": "美军",
    "russianforces": "俄军",
    "russiantroops": "俄军",
    "russianmilitary": "俄军",
    "therussianmilitary": "俄军",
    "ukrainianforces": "乌军",
    "ukrainiantroops": "乌军",
    "ukrainianarmedforces": "乌军",
    "ukrainesairforce": "乌军",
    "ukrainianairforce": "乌军",
    "theukrainianairforce": "乌军",
    "hezbollah": "真主党",
    "hamas": "哈马斯",
    "nato": "北约",
    "zelensky": "泽连斯基",
    "zelenskyy": "泽连斯基",
    "volodymyrzelensky": "泽连斯基",
    "volodymyrzelenskyy": "泽连斯基",
    "presidentvolodymyrzelensky": "泽连斯基",
    "presidentzelensky": "泽连斯基",
    "netanyahu": "内塔尼亚胡",
    "benjaminnetanyahu": "内塔尼亚胡",
    "putin": "普京",
    "vladimirputin": "普京",
    "presidentputin": "普京",
    "trump": "特朗普",
    "donaldtrump": "特朗普",
    "presidenttrump": "特朗普",
    "kyiv": "基辅",
    "kiev": "基辅",
}

EN_GENERIC = {
    "theofficial",
    "official",
    "officials",
    "aserniorofficial",
    "theseniorofficial",
    "theseniordefenseofficial",
    "thedefenseestablishment",
    "governmentforces",
    "airraidsirens",
    "multipleexplosions",
    "warehousefacilities",
    "thevessel",
    "thesite",
    "thenumberoftankers",
    "ballisticmissiles",
    "severalballisticmissiles",
}


def en_canon_of(text: str) -> str | None:
    k = latin_key(text)
    if not k:
        return None
    if k in EN_CANON:
        return EN_CANON[k]
    if k.startswith("the") and k[3:] in EN_CANON:
        return EN_CANON[k[3:]]
    if k.startswith("only") and k[4:] in EN_CANON:
        return EN_CANON[k[4:]]
    return None


def is_en_generic(text: str) -> bool:
    k = latin_key(text)
    if not k:
        return False
    if k in EN_GENERIC:
        return True
    if k.endswith(("official", "officials")):
        return True
    if k.endswith(("sirens", "explosions", "facilities", "tankers")):
        return True
    return False
