"""机构国别归属的推断补全。

抽取阶段已经能把「正文可证」和「名字自带国别」的辖属边抽出来（见 kg_extract 提示词）。
本模块只补剩下的缺口：正文没提国家、名字也不含国别标志，但领域常识可以判断归属的机构，
例如「美军驻地」全文无「美国」二字。

关键约束：**推断结果必须与文本可证的结果分开**，标 source 字段：

    {"head":"美军","relation":"辖属","tail":"美国","source":"text"}      原文/名字可证
    {"head":"美军","relation":"辖属","tail":"美国","source":"inferred"}  常识推断

可视化时推断边应画成虚线，用户能看出哪些是推的。混在一起会让整张图的
可信度被少数推错的边拖累。

    python3 -m extract.country_infer --src data/extract/kgv10_....jsonl --out ...
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

# 跨国机构与无法确定归属的名称，一律不推断
TRANSNATIONAL = {
    "北约", "北大西洋公约组织", "联合国", "联合国安全理事会", "安理会",
    "欧盟", "欧洲联盟", "欧佩克", "国际原子能机构", "红十字会", "红十字国际委员会",
    "国际刑事法院", "海牙国际法院", "世界卫生组织", "世界银行", "国际货币基金组织",
    "伊斯兰国", "极端组织“伊斯兰国”", "基地组织", "库尔德武装", "塔利班",
    "打击“伊斯兰国”国际联盟", "国际联盟", "多国联军", "维和部队",
    "国防部", "外交部", "军方", "政府", "总统府", "总理办公室", "内政部", "总参谋部",
    # 多国联军/联合机构：参与方不等于归属方
    "沙特主导的多国联军", "多国联军", "联军", "联合部队", "国际联盟",
    "库尔德自由斗士组织", "库尔德工人党", "人民保护部队", "叙利亚民主力量",
}

# 名称含这些词的视为跨国或归属不明。只收真正会跨国的表述，
# 不收「部队」「委员会」这类，否则会误杀「乌克兰武装部队」「伊朗最高国家安全委员会」。
TRANSNATIONAL_PAT = re.compile(
    r"(?:多国|联合[国合]?|跨国|国际(?!文传)|维和|地区性|区域性)"
    r"|库尔德"
)

# 明确的国别标志。按此表可以把机构名直接映射到国家，不需要调模型。
MARKERS = {
    "美国": ("美", "五角大楼", "白宫", "国会山", "美军"),
    "俄罗斯": ("俄", "克里姆林宫"),
    "中国": ("中国", "解放军", "五角大楼" ),
    "以色列": ("以色列", "以军"),
    "伊朗": ("伊朗", "伊斯兰革命卫队"),
    "乌克兰": ("乌克兰", "乌军"),
    "英国": ("英国", "英军", "皇家海军", "皇家空军"),
    "法国": ("法国", "法军"),
    "德国": ("德国", "德军", "联邦国防军"),
    "日本": ("日本", "自卫队"),
    "韩国": ("韩国", "韩军"),
    "朝鲜": ("朝鲜", "朝军"),
    "印度": ("印度", "印军"),
    "土耳其": ("土耳其", "土军"),
    "沙特": ("沙特",),
    "黎巴嫩": ("黎巴嫩", "真主党"),
    "巴勒斯坦": ("巴勒斯坦", "哈马斯", "杰哈德", "法塔赫"),
    "也门": ("也门", "胡塞"),
    "叙利亚": ("叙利亚", "叙军"),
    "伊拉克": ("伊拉克",),
    "苏丹": ("苏丹",),
    "埃及": ("埃及",),
    "卡塔尔": ("卡塔尔",),
    "阿联酋": ("阿联酋", "阿拉伯联合酋长国"),
    "约旦": ("约旦",),
    "科威特": ("科威特",),
    "巴林": ("巴林",),
    "阿曼": ("阿曼",),
    "波兰": ("波兰",),
    "荷兰": ("荷兰",),
    "加拿大": ("加拿大",),
    "澳大利亚": ("澳大利亚",),
    "菲律宾": ("菲律宾",),
    "巴基斯坦": ("巴基斯坦",),
    "阿富汗": ("阿富汗",),
    "委内瑞拉": ("委内瑞拉",),
    "哥伦比亚": ("哥伦比亚",),
    "南非": ("南非",),
    "意大利": ("意大利",),
    "西班牙": ("西班牙",),
    "挪威": ("挪威",),
    "瑞典": ("瑞典",),
    "芬兰": ("芬兰",),
    "丹麦": ("丹麦",),
}


# 国家简称，用于识别双边共管机构（「泰柬边界委员会」= 泰国+柬埔寨）
COUNTRY_SHORT = {
    "泰": "泰国", "柬": "柬埔寨", "中": "中国", "印": "印度", "美": "美国",
    "俄": "俄罗斯", "日": "日本", "韩": "韩国", "朝": "朝鲜", "越": "越南",
    "菲": "菲律宾", "巴": "巴基斯坦", "伊": "伊朗", "叙": "叙利亚", "土": "土耳其",
    "以": "以色列", "黎": "黎巴嫩", "埃": "埃及", "约": "约旦", "沙": "沙特",
}


def is_bilateral(name: str, countries: set[str]) -> bool:
    """机构名含两个以上国家名，视为双边共管机构。

    「泰柬边界委员会」同时归泰国和柬埔寨都是错的，这类不建辖属边。
    只认双字以上国家全名，避免「泰国第一军区」的「国」误配到「中国」。
    简称形式另用 COUNTRY_SHORT 识别首字组合。
    """
    n = (name or "").strip()
    if not n:
        return False
    hits = {c for c in countries if len(c) >= 2 and c in n}
    # 名称以两个国别简称开头，如「泰柬…」「中日…」「美伊…」
    if len(n) >= 2:
        a, b = COUNTRY_SHORT.get(n[0]), COUNTRY_SHORT.get(n[1])
        if a and b and a != b:
            hits |= {a, b}
    return len(hits) >= 2


def resolve_country(name: str, countries: set[str] | None = None) -> str:
    """用机构名里的国别标志判定归属，判不出返回空串。

    匹配规则：多字标志按包含匹配，单字标志（美/俄/中/英/法/德/日/韩/朝/印/土）
    只认词首，避免「美国防部」里的「国」之类误配。
    多个标志命中时取最长的那个，因为长标志更具体。
    """
    n = (name or "").strip()
    if not n or is_transnational(n):
        return ""
    # 双边共管机构不归属任何单一国家
    if countries and is_bilateral(n, countries):
        return ""
    best_len = 0
    hit = ""
    for country, marks in MARKERS.items():
        for m in marks:
            if m == n or (len(m) == 1 and n.startswith(m)) or (len(m) > 1 and m in n):
                if len(m) > best_len:
                    best_len = len(m)
                    hit = country
    return hit


def _resolve(name: str, countries: set[str] | None = None) -> str:
    return resolve_country(name, countries)


def infer(rows: list[dict]) -> tuple[list[dict], dict]:
    """为没有辖属边的机构补推断归属。

    只处理机构。已有辖属边的（不论 text 还是 inferred）跳过。
    """
    stat = Counter()
    out = []
    for row in rows:
        row = dict(row)
        rels = [dict(x) for x in row.get("relations") or []]
        have = {(x["head"], x["tail"]) for x in rels if x["name"] == "辖属"}
        covered = {x["head"] for x in rels if x["name"] == "辖属"}

        # 本篇出现过的国家名，用于判定双边共管机构
        countries = {e.get("text") for e in row.get("entities") or [] if e.get("type") == "国家"}

        for e in row.get("entities") or []:
            if e.get("type") != "机构":
                continue
            name = e.get("text") or ""
            if name in covered:
                continue
            country = _resolve(name, countries)
            if not country or country == name:
                if name in TRANSNATIONAL:
                    stat["skip_transnational"] += 1
                else:
                    stat["unresolved"] += 1
                continue
            if (name, country) in have:
                continue
            rels.append({
                "name": "辖属",
                "head": name,
                "tail": country,
                "evidence": "",
                "source": "inferred",
            })
            covered.add(name)
            stat["inferred"] += 1

        row["relations"] = rels
        out.append(row)
    return out, dict(stat)


def mark_sources(rows: list[dict]) -> tuple[list[dict], dict]:
    """给已有的辖属边打 source 标记：有 evidence 的算 text，无 evidence 的按名称标记算 marker。"""
    stat = Counter()
    out = []
    for row in rows:
        row = dict(row)
        rels = []
        for x in row.get("relations") or []:
            if x["name"] == "辖属" and "source" not in x:
                x = dict(x)
                if x.get("evidence"):
                    x["source"] = "text"
                    stat["text"] += 1
                else:
                    x["source"] = "marker"
                    stat["marker"] += 1
            rels.append(x)
        row["relations"] = rels
        out.append(row)
    return out, dict(stat)


def is_transnational(name: str) -> bool:
    """是否为跨国或归属不明的机构。"""
    n = (name or "").strip()
    if not n:
        return False
    if n in TRANSNATIONAL:
        return True
    # 「国际文传电讯社」是乌克兰的通讯社，不能被「国际」误杀
    if n.startswith("国际文传"):
        return False
    return bool(TRANSNATIONAL_PAT.search(n))


def fix_affiliation(rows: list[dict]) -> tuple[list[dict], dict]:
    """修正辖属边的错误归属。

    三类处理：
    1. 跨国/泛称机构被强加了归属 → 删除该边
    2. 双边共管机构被归到多个国家 → 全删（应改用隶属或事件表达）
    3. 起点或终点类型不符 → 删除
    """
    stat = Counter()
    out = []
    for row in rows:
        row = dict(row)
        countries = {e.get("text") for e in row.get("entities") or [] if e.get("type") == "国家"}
        types = {e.get("text"): e.get("type") for e in row.get("entities") or []}
        kept = []
        # 先按 head 聚合，判断是否被归到多个国家
        by_head: dict[str, list[dict]] = {}
        for x in row.get("relations") or []:
            if x.get("name") == "辖属":
                by_head.setdefault(x.get("head") or "", []).append(x)

        bad_heads: set[str] = set()
        for head, edges in by_head.items():
            if is_transnational(head) or is_bilateral(head, countries):
                bad_heads.add(head)
                stat["affiliation_dropped_bad_head"] += len(edges)
                continue
            tails = {e.get("tail") for e in edges}
            if len(tails) > 1:
                # 同一机构被归到多个国家，说明模型判断不稳，全部丢弃
                bad_heads.add(head)
                stat["affiliation_dropped_multi_tail"] += len(edges)
                continue
            if types.get(head) not in (None, "机构"):
                bad_heads.add(head)
                stat["affiliation_dropped_head_type"] += len(edges)

        for x in row.get("relations") or []:
            if x.get("name") == "辖属" and x.get("head") in bad_heads:
                continue
            kept.append(x)
        row["relations"] = kept
        out.append(row)
    return out, dict(stat)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="机构国别归属推断补全")
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--infer", action="store_true", help="启用推断（默认只打 source 标记）")
    a = ap.parse_args(argv)

    rows = []
    for line in Path(a.src).open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    rows, s1 = mark_sources(rows)
    print("打 source 标记:", s1)
    rows, s0 = fix_affiliation(rows)
    print("修正错归属:", s0)
    if a.infer:
        rows, s2 = infer(rows)
        print("推断补全:", s2)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as g:
        for r in rows:
            g.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
