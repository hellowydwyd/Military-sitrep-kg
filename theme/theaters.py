"""标题战场挡板：互斥地点/组织，不含「美国/以色列」这种到处出现的词。"""

from __future__ import annotations

# 长词优先
THEATER_ALIASES: list[tuple[str, str]] = []
_RAW = {
    "ukraine": [
        "顿涅茨克",
        "哈尔科夫",
        "泽连斯基",
        "乌克兰",
        "基辅",
        "乌军",
        "俄乌",
        "顿巴斯",
        "乌方",
        "俄军",
        "俄方",
        "Ukraine",
        "Kyiv",
        "Kiev",
        "Zelensky",
        "Zelenskyy",
        "Donetsk",
        "Kharkiv",
        "Donbas",
    ],
    "iran": [
        "霍尔木兹海峡",
        "霍尔木兹",
        "革命卫队",
        "德黑兰",
        "拉腊克",
        "伊朗",
        "Hormuz",
        "Tehran",
        "IRGC",
        "Iran",
    ],
    "houthi": [
        "曼德海峡",
        "胡塞",
        "也门",
        "萨那",
        "红海",
        "Houthi",
        "Houthis",
        "Yemen",
        "Sanaa",
        "Red Sea",
        "Ansar Allah",
    ],
    "lebanon": ["真主党", "黎巴嫩", "联黎", "贝鲁特", "黎南部", "黎南", "黎叙", "Hezbollah", "Lebanon", "Beirut"],
    "gaza": ["哈马斯", "加沙", "Hamas", "Gaza"],
    "carrier": ["林肯」号", "林肯号", "罗斯福号", "航母", "USS Lincoln", "USS Roosevelt"],
    "eastasia": ["仁爱礁", "菲律宾", "台岛", "东部战区", "南部战区", "Philippines", "Taiwan", "South China Sea"],
    "japan": ["高市早苗", "防卫省", "日本", "Japan"],
    "korea": ["韩美", "韩国", "朝鲜", "Korea", "Seoul", "Pyongyang"],
}
for tid, words in _RAW.items():
    for w in sorted(words, key=len, reverse=True):
        THEATER_ALIASES.append((w, tid))


def theaters_of(title: str) -> frozenset[str]:
    t = title or ""
    t_low = t.casefold()
    hit: set[str] = set()
    used: list[tuple[int, int]] = []
    for word, tid in THEATER_ALIASES:
        if word.isascii():
            i = t_low.find(word.casefold())
        else:
            i = t.find(word)
        if i < 0:
            continue
        j = i + len(word)
        if any(not (j <= a or i >= b) for a, b in used):
            continue
        hit.add(tid)
        used.append((i, j))
    return frozenset(hit)


def cannot_link(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> bool:
    """两边都认出战场且完全不相交 → 禁止并入。空集不挡（标题没点名）。"""
    if not a or not b:
        return False
    return set(a).isdisjoint(set(b))


def primary_theater(title: str) -> str:
    """一篇标题一个战场。多个命中取文中最左；都没有则空串。"""
    hits = theaters_of(title)
    if not hits:
        return ""
    if len(hits) == 1:
        return next(iter(hits))
    t = title or ""
    t_low = t.casefold()
    best_tid, best_i = "", len(t) + 1
    for word, tid in THEATER_ALIASES:
        if tid not in hits:
            continue
        i = t_low.find(word.casefold()) if word.isascii() else t.find(word)
        if 0 <= i < best_i:
            best_tid, best_i = tid, i
    return best_tid
