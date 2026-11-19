"""实体同一性判定（Equal 关系），替代硬编码别名表。

现状：extract/entity_norm.py 里维护 44 条别名（以军→以色列国防军），
覆盖高频项但覆盖不了长尾——装备型号的全称与简称、机构的正式名与通称。

CMNIE 基准把「是否同一实体」做成显式可标注的关系（Equal），
而不是预处理阶段的字符串替换。本模块提供三层判定：

1. **词形层**：去括号、去空格、大小写、全半角归一后相同 → 判同一
2. **包含层**：A 是 B 的子串且 A 长度 >= 阈值 → 判同一（如「伊朗国防部」⊂「伊朗国防部新闻局」需谨慎）
3. **相似层**：编辑距离 / 缩写展开 / 型号归一 → 得分超阈值判同一

输出是 (head, tail, confidence, reason) 的候选对，供人工确认或直接合入别名表。

    python3 -m extract.entity_equal --src data/extract/kgv10_....jsonl --stats
    python3 -m extract.entity_equal --src ... --out data/eval/equal_pairs.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 括号内的补充说明，通常可忽略：「以色列国防军（IDF）」
BRACKET = re.compile(r"[（(][^）)]{0,20}[）)]")
# 军种/部门词：这些词不同则一定是不同实体，不能靠编辑距离合并
# 「美国海军」vs「美国空军」、「国防部」vs「外交部」都是编辑距离 1 但语义不同
MILITARY_BRANCH = {
    "海军", "空军", "陆军", "海军陆战队", "空天军", "火箭军", "战略火箭军",
    "海岸警卫队", "国民警卫队", "特种部队", "空降军", "边防军", "内卫部队",
}
ORG_DEPT = {
    "国防部", "外交部", "内政部", "财政部", "司法部", "交通部", "能源部",
    "农业部", "商务部", "卫生部", "教育部", "劳工部", "国土安全部",
    "国防军", "总参谋部", "总司令部", "情报局", "安全局", "调查局",
}
# 行政区划层级：省/州/市/县不同级，不是同一实体
ADMIN_LEVEL = {"省", "州", "市", "县", "区", "镇", "村", "自治区", "自治州", "盟", "旗"}
# 战区/司令部名：不同战区是不同建制
THEATER = {
    "中央司令部", "南方司令部", "北方司令部", "非洲司令部", "欧洲司令部",
    "印太司令部", "太平洋司令部", "战略司令部", "运输司令部", "特种作战司令部",
    "战区", "军区", "舰队", "航空队",
}


def _has_distinct_term(a: str, b: str) -> str:
    """若两者分别含不同的专属词，返回冲突说明。"""
    for group in (MILITARY_BRANCH, ORG_DEPT, THEATER):
        ina = {w for w in group if w in a}
        inb = {w for w in group if w in b}
        if ina and inb and not (ina & inb):
            return f"{'/'.join(sorted(ina))} ≠ {'/'.join(sorted(inb))}"

    # 行政区划层级不同：「布什尔省」vs「布什尔市」
    la = {w for w in ADMIN_LEVEL if a.endswith(w)}
    lb = {w for w in ADMIN_LEVEL if b.endswith(w)}
    if la and lb and not (la & lb):
        return f"行政层级不同（{''.join(la)} ≠ {''.join(lb)}）"
    return ""


# 常见无意义后缀
NOISE_SUFFIX = re.compile(r"(?:方面|人士|官员|发言人|当局|军方|方面表示)$")
# 引号、书名号
QUOTES = re.compile(r"[「」『』“”\"'《》〈〉]")
# 空白与标点
SPACE_PUNCT = re.compile(r"[\s·—\-－_、，,。.；;：:]+")
# 装备型号归一：F-35 / F35 / f-35 → f35
MODEL = re.compile(r"([A-Za-z]{1,4})\s*[-－]?\s*(\d{1,3})")


def canon(s: str) -> str:
    """归一化到可比形式。"""
    t = unicodedata.normalize("NFKC", s or "")
    t = QUOTES.sub("", t)
    t = BRACKET.sub("", t)
    t = NOISE_SUFFIX.sub("", t)
    t = SPACE_PUNCT.sub("", t)
    t = MODEL.sub(lambda m: (m.group(1).lower() + m.group(2)), t)
    return t.lower()


def edit_distance(a: str, b: str, cap: int = 4) -> int:
    """带剪枝的编辑距离，超过 cap 直接返回 cap+1。"""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (a[i - 1] != b[j - 1]),
            )
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[lb]


def judge(a: str, b: str, type_a: str, type_b: str) -> tuple[float, str]:
    """判定两个实体名是否指同一实体，返回 (置信度, 理由)。"""
    if type_a != type_b:
        return 0.0, "类型不同"
    ca, cb = canon(a), canon(b)
    if not ca or not cb:
        return 0.0, ""
    if ca == cb:
        return 0.98, "归一后完全相同"

    # 军种/部门词冲突则一定不是同一实体，跳过后续所有近似判定
    conflict = _has_distinct_term(a, b)
    if conflict:
        return 0.0, f"专属词冲突（{conflict}）"

    # 包含关系一律不判同一：这是上下级或部分-整体关系，应交给「隶属」关系表达。
    # 「伊朗伊斯兰革命卫队」⊂「伊朗伊斯兰革命卫队海军」是母组织与下属军种，
    # 「中国海警」与「中国海军」更是不同机构。宁可漏合并，不可错合并。
    # 只有「同名词 + 类型后缀」这种情况才算同一，如「中国海警」vs「中国海警局」。
    if len(ca) >= 3 and len(cb) >= 3:
        short, long_ = (ca, cb) if len(ca) < len(cb) else (cb, ca)
        if short in long_ and len(short) / len(long_) >= 0.7:
            tail = long_.replace(short, "", 1)
            # 后缀是纯机构类型词，才算同一实体
            if tail in ("局", "部", "总部", "司令部", "组织", "部队", "军", "队"):
                return 0.82, f"同名词加机构后缀（{tail}）"

    # 编辑距离只能识别「音译/写法差异」，不能识别「不同实体」。
    # 中文里差一个字往往是完全不同的东西（海军/陆军、北方/后方、省/州、港/市），
    # 所以只在差异出现在末位、且末位是纯音译用字时才认。
    d = edit_distance(ca, cb, cap=2)
    if d == 0:
        return 0.98, "归一后完全相同"
    if d <= 2 and min(len(ca), len(cb)) >= 5:
        # 找出差异位置：末尾差异（音译写法不同）才算
        common = 0
        for x, y in zip(ca, cb):
            if x != y:
                break
            common += 1
        if common >= min(len(ca), len(cb)) - 1:
            # 末位若是建制、军种或行政区后缀，说明是不同实体（上级 vs 下级、省 vs 南部）
            longer = ca if len(ca) > len(cb) else cb
            shorter = cb if longer is ca else ca
            suffix = longer[len(shorter):]
            SUBUNIT = set(MILITARY_BRANCH) | ADMIN_LEVEL | THEATER | {
                "南部", "北部", "东部", "西部", "中部", "东南部", "西南部",
                "东北部", "西北部", "地区", "战区", "分部", "支队", "大队",
                "中队", "小队", "总部", "司令部", "指挥所", "基地", "分局", "总局",
            }
            if any(suffix.endswith(s) or suffix == s for s in SUBUNIT):
                return 0.0, f"末位是建制/区划后缀（{suffix}），判为不同实体"
            return 0.72, f"仅末位差异（编辑距离 {d}，疑似音译写法不同）"

    return 0.0, ""


def collect_entities(rows: list[dict]) -> dict[str, dict]:
    """按规范名聚合实体，统计出现的原始形式与类型。"""
    out: dict[str, dict] = {}
    for row in rows:
        for e in row.get("entities") or []:
            name, typ = e.get("text") or "", e.get("type") or ""
            if not name:
                continue
            rec = out.setdefault(name, {"name": name, "type": typ, "count": 0,
                                       "aliases": set()})
            rec["count"] += 1
            for al in e.get("aliases") or []:
                rec["aliases"].add(al)
    return out


def find_pairs(ents: dict[str, dict], min_conf: float = 0.6) -> list[dict]:
    """在同类实体中找同一性候选。用分桶避免 O(n^2)。"""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for rec in ents.values():
        by_type[rec["type"]].append(rec)

    pairs: list[dict] = []
    for typ, group in by_type.items():
        # 按 canon 首字分桶，只比同桶与邻桶
        buckets: dict[str, list[dict]] = defaultdict(list)
        for rec in group:
            c = canon(rec["name"])
            if len(c) >= 2:
                buckets[c[:2]].append(rec)
        keys = sorted(buckets)
        seen = set()
        for k in keys:
            cand = list(buckets.get(k, []))
            # 同类型内还需和首字相同的桶比，覆盖「伊朗国防部/伊朗外交部」
            for i in range(len(cand)):
                for j in range(i + 1, len(cand)):
                    a, b = cand[i], cand[j]
                    key = tuple(sorted([a["name"], b["name"]]))
                    if key in seen:
                        continue
                    seen.add(key)
                    conf, reason = judge(a["name"], b["name"], a["type"], b["type"])
                    if conf >= min_conf:
                        pairs.append({
                            "head": a["name"],
                            "tail": b["name"],
                            "type": typ,
                            "confidence": round(conf, 2),
                            "reason": reason,
                            "head_count": a["count"],
                            "tail_count": b["count"],
                        })
    pairs.sort(key=lambda x: -x["confidence"])
    return pairs


def stats(ents: dict[str, dict], pairs: list[dict]) -> str:
    lines = ["=== 实体同一性候选 ===", f"实体 {len(ents)} 个，候选对 {len(pairs)} 组", ""]
    by_conf = Counter(p["confidence"] for p in pairs)
    lines.append("置信度分布：")
    for c, n in sorted(by_conf.items(), reverse=True):
        lines.append(f"  {c:.2f}  {n:4d} 组")
    lines.append("")
    lines.append("高置信示例（0.80 以上，可直接并入别名表）：")
    for p in [x for x in pairs if x["confidence"] >= 0.8][:12]:
        lines.append(f"  {p['head']} ≈ {p['tail']}  （{p['reason']}）")
    lines.append("")
    lines.append("需人工确认（0.60~0.80）：")
    for p in [x for x in pairs if 0.6 <= x["confidence"] < 0.8][:10]:
        lines.append(f"  {p['head']} ≈ {p['tail']}  （{p['reason']}）")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="实体同一性判定")
    ap.add_argument("--src", default=str(DATA / "extract" / "kgv10_huanqiu_mil_2026-01.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--min-conf", type=float, default=0.6)
    a = ap.parse_args(argv)

    rows = []
    for line in Path(a.src).open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    ents = collect_entities(rows)
    pairs = find_pairs(ents, a.min_conf)
    print(stats(ents, pairs))

    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as g:
            for p in pairs:
                g.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
