"""事件间关系：时序、因果、子事件、共指。

向主流对齐（MAVEN-ERE 的四类划分），但按任务特点选择判定方式：

| 关系 | 判定方式 | 理由 |
| --- | --- | --- |
| 子事件 subevent | 复合事件生成时附带 | 层次结构自带，无需独立判定 |
| 时序 temporal | 规则比较时间戳 | LLM 判时序弱（ACL 2024 明确指出）|
| 因果 causal | 规则预筛 + LLM 判定 | LLM 判因果强（COPA 95.2 / e-CARE 79.6）|
| 共指 coref | 规则预筛 + LLM 确认 | 合并节点，不建边 |

设计约束：
- 时序不用 LLM。两个事件的时间戳直接比较就能定先后，让模型判反而更差。
- 因果必须用 LLM，但只对规则筛出的候选判定（200 篇全配对是 27 万对，不可能全扫）。
- 每个判定都存理由，便于人工抽检验证。

    python3 -m extract.event_links --src data/extract/kgv13_....jsonl --stats
    python3 -m extract.event_links --src ... --out /tmp/links.jsonl --llm
    python3 -m extract.event_links --src ... --stats --no-llm   # 只用规则，不调模型
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 时序：同一参与方的两个事件，时间差不超过这个天数才建边。
# 语料跨数月，同一国家的相关事件往往隔几周，窗口太窄就全断了。
TEMPORAL_WINDOW_DAYS = 14
# 每个事件最多连出多少条时序边，避免枢纽事件扇出过大
MAX_TEMPORAL_PER_EVENT = 3

# 因果候选的类型对：前者可能是后者的原因
CAUSE_PAIRS = {
    ("对抗", "灾伤"),   # 打击/空袭 → 伤亡
    ("对抗", "治理"),   # 打击 → 立法/制裁
    ("治理", "对抗"),   # 授权动武 → 打击
    ("对抗", "表态"),   # 打击 → 谴责
    ("部署", "对抗"),   # 兵力调动 → 交火
    # 对抗→对抗 是报复性升级的主要场景：「A 空袭 B」→「B 反击 A」。
    # 实测这类候选最多（64 对，占候选池 74%），漏掉会丢失大部分真实因果。
    ("对抗", "对抗"),
}
# 因果候选的时间窗：原因必须早于结果，且不超过这个天数
CAUSE_WINDOW_DAYS = 3

PARTY_ROLES = (
    "打击方", "受击方", "拦截方", "袭击方", "控制方", "受控方",
    "部署方", "表态方", "甲方", "乙方", "援助方", "受援方",
    "打击对象", "控制对象", "受灾对象", "施动方", "主体",
)


def load(src: Path) -> list[dict]:
    rows = []
    for line in src.open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def day_diff(a: str, b: str) -> int:
    """b - a 的天数。任一为空返回 9999。"""
    if not a or not b:
        return 9999
    try:
        from datetime import date
        ya, ma, da = (int(x) for x in a.split("-")[:3])
        yb, mb, db = (int(x) for x in b.split("-")[:3])
        return (date(yb, mb, db) - date(ya, ma, da)).days
    except Exception:
        return 9999


def collect(rows: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """收集原子事件及其参与方。复合事件不参与关系判定，只作为层次结构。"""
    events = []
    docs = {}
    for row in rows:
        doc_id = row.get("doc_id") or ""
        docs[doc_id] = {
            "title": row.get("title") or "",
            "publish_time": row.get("publish_time") or "",
            "body": row.get("body") or "",
        }
        evs = {e["text"]: e for e in (row.get("events") or [])}
        argmap: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for rel in row.get("relations") or []:
            if rel.get("head") in evs and rel["name"] != "包含":
                argmap[rel["head"]].append((rel["name"], rel.get("tail") or ""))
        for text, ev in evs.items():
            parties = {v for r, v in argmap.get(text, []) if r in PARTY_ROLES and v}
            events.append({
                "eid": f"{doc_id}#{abs(hash(text)) & 0xFFFFFF:06x}",
                "doc": doc_id,
                "text": text,
                "type": ev.get("event_type") or "",
                "subtype": ev.get("subtype") or "",
                "time": (ev.get("time_norm") or "")[:10],
                "parties": parties,
                "args": argmap.get(text, []),
            })
    return events, docs


# ---------------------------------------------------------------- 时序

def build_temporal(events: list[dict]) -> list[dict]:
    """时序关系：同一参与方的相邻事件，按时间戳直接定先后。

    不用 LLM —— 两个事件的时间戳一比就知道谁在前，模型反而容易错。
    """
    by_party: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if not e["time"]:
            continue
        for p in e["parties"]:
            by_party[p].append(e)

    links: list[dict] = []
    seen = set()
    for party, group in by_party.items():
        group = sorted(group, key=lambda e: e["time"])
        for i, a in enumerate(group):
            n = 0
            for b in group[i + 1:]:
                d = day_diff(a["time"], b["time"])
                if d > TEMPORAL_WINDOW_DAYS:
                    break
                if a["eid"] == b["eid"] or a["doc"] == b["doc"] or d <= 0:
                    continue
                key = (a["eid"], b["eid"])
                if key in seen:
                    continue
                seen.add(key)
                links.append({
                    "rel": "temporal",
                    "subtype": "before",
                    "from": a["eid"], "to": b["eid"],
                    "from_text": a["text"], "to_text": b["text"],
                    "from_time": a["time"], "to_time": b["time"],
                    "from_doc": a["doc"], "to_doc": b["doc"],
                    "reason": f"共同参与方 {party}，相隔 {d} 天",
                    "by": "rule",
                })
                n += 1
                if n >= MAX_TEMPORAL_PER_EVENT:
                    break
    return links


# ---------------------------------------------------------------- 因果

def causal_candidates(events: list[dict]) -> list[tuple[dict, dict, str]]:
    """规则预筛因果候选。目的是降成本，不追求精确。"""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e["time"]:
            by_type[e["type"]].append(e)

    out = []
    seen = set()
    for (a_type, b_type) in CAUSE_PAIRS:
        for a in by_type.get(a_type, []):
            for b in by_type.get(b_type, []):
                if a["eid"] == b["eid"] or a["doc"] == b["doc"]:
                    continue
                d = day_diff(a["time"], b["time"])
                # 原因必须早于或同日于结果
                if d < 0 or d > CAUSE_WINDOW_DAYS:
                    continue
                # 参与者必须有交集，否则只是同类型的巧合
                common = a["parties"] & b["parties"]
                if not common:
                    continue
                key = (a["eid"], b["eid"])
                if key in seen:
                    continue
                seen.add(key)
                out.append((a, b, "、".join(sorted(common)[:3])))
    return out


CAUSE_SYSTEM = """你判断两个新闻事件之间是否存在因果关系。只输出 JSON。

判定标准（严格）：
- cause：A 直接导致了 B。B 是 A 的后果，A 结束后 B 才发生或加剧。
- precondition：A 是 B 的前提，A 没发生则 B 不会发生。
- none：不是因果。

**三种必须判 none 的情况，这是最容易错的：**

1. **同一事件的不同表述** —— A 与 B 描述的是同一件事，只是一方更详细或视角不同。
   反例：「乌克兰无人机袭击圣彼得堡」/「圣彼得堡市长称该市遭大规模无人机袭击」→ 这是同一事件，判 none。
   反例：「美以对伊朗发动突袭」/「美军攻击机和航母正在执行空袭任务」→ 后者是前者的展开，判 none。
2. **同一行动的不同阶段** —— A 是 B 的开端或组成部分。
   反例：「以军正在打击真主党目标」/「以军已袭击超320个目标」→ 后者是前者的战果统计，判 none。
3. **仅共享参与方** —— 两国都出现在两个事件里，不代表其中一个导致了另一个。

真正的因果长这样：
  正例：「美以袭击伊朗石油设施」/「伊朗随后报复性打击海湾国家石油设施」→ cause（一方行动引发另一方反应）
  正例：「美军袭击伊朗南部」/「伊朗军方声明为回应美军袭击而发动无人机打击」→ cause（原文有「回应」「报复」等明确因果连接）

判断依据优先级：原文中是否有明确的因果连接词（因此、导致、随后、为回应、报复、作为…的回应）> 时间先后 > 参与方重合。

**方向性要求**：如果判定存在因果，原因必须是时间上更早、且在语义上被回应/报复的那一方。
若 A 晚于 B，而 B 才是被回应的一方，则应判 none（不要反向硬凑因果）。"""

CAUSE_USER = """事件 A（{a_time}）：
{a_text}

事件 B（{b_time}）：
{b_text}

原文片段：
{context}

两者共享的参与方：{common}

请判断 A 与 B 的关系。JSON：
{{"relation": "cause|precondition|none", "confidence": "high|medium|low", "reason": "一句话说明"}}"""


def judge_causal(a: dict, b: dict, common: str, docs: dict[str, dict]) -> dict | None:
    """调 LLM 判定一对事件是否因果。返回 None 表示判为无关。"""
    from extract.llm import chat_json
    from extract.prompt import parse_llm_json

    # 给模型看原文，避免只凭两个事件文本瞎猜
    ctx_parts = []
    for d in (a["doc"], b["doc"]):
        body = (docs.get(d) or {}).get("body") or ""
        if body:
            ctx_parts.append(body[:600])
    context = "\n---\n".join(ctx_parts) or "（无原文）"

    user = CAUSE_USER.format(
        a_time=a["time"] or "未知", a_text=a["text"],
        b_time=b["time"] or "未知", b_text=b["text"],
        context=context, common=common,
    )
    try:
        parsed = parse_llm_json(chat_json(CAUSE_SYSTEM, user))
    except Exception:
        return None

    rel = str(parsed.get("relation") or "").strip().lower()
    if rel not in ("cause", "precondition"):
        return None
    return {
        "rel": "causal",
        "subtype": rel,
        "from": a["eid"], "to": b["eid"],
        "from_text": a["text"], "to_text": b["text"],
        "from_time": a["time"], "to_time": b["time"],
        "from_doc": a["doc"], "to_doc": b["doc"],
        "confidence": str(parsed.get("confidence") or "").strip(),
        "reason": f"[{rel}] {str(parsed.get('reason') or '').strip()[:80]}",
        "by": "llm",
    }


# ---------------------------------------------------------------- 子事件

def build_subevent(rows: list[dict]) -> list[dict]:
    """子事件关系：复合事件 → 原子事件。

    复合事件由 extract/composite.py 生成，relations 里已有「包含」边。
    这里转成统一的 subevent 表示，供图谱使用。

    注意：端点 id 必须与 collect() 生成的 eid 同一编码，
    否则孤立率统计会把已覆盖的原子事件算成孤立。
    """
    links = []
    for row in rows:
        doc_id = row.get("doc_id") or ""
        evs = {e["text"]: e for e in (row.get("events") or [])}

        def eid_of(text: str) -> str:
            return f"{doc_id}#{abs(hash(text)) & 0xFFFFFF:06x}"

        for rel in row.get("relations") or []:
            if rel.get("name") != "包含":
                continue
            head, tail = rel.get("head") or "", rel.get("tail") or ""
            # 头是复合事件，尾是原子事件；尾必须在 events 里才能对上 eid
            if tail not in evs:
                continue
            links.append({
                "rel": "subevent",
                "subtype": "contains",
                "from": eid_of(head),
                "to": eid_of(tail),
                "from_text": head,
                "to_text": tail,
                "from_doc": doc_id, "to_doc": doc_id,
                "reason": "复合事件包含原子事件",
                "by": "hierarchy",
            })
    return links


# ---------------------------------------------------------------- 汇总

def build_links(rows: list[dict], *, use_llm: bool = False,
                limit_causal: int = 0) -> tuple[list[dict], dict]:
    events, docs = collect(rows)

    temporal = build_temporal(events)
    subevent = build_subevent(rows)

    cands = causal_candidates(events)
    causal: list[dict] = []
    stat_causal = Counter()
    if use_llm:
        todo = cands[:limit_causal] if limit_causal else cands
        for i, (a, b, common) in enumerate(todo, 1):
            got = judge_causal(a, b, common, docs)
            if got:
                causal.append(got)
                stat_causal[got["subtype"]] += 1
            else:
                stat_causal["none"] += 1

    stat = {
        "events": len(events),
        "temporal": len(temporal),
        "subevent": len(subevent),
        "causal_candidates": len(cands),
        "causal": len(causal),
        "causal_detail": dict(stat_causal),
    }
    # 因果是有方向的：同一对事件若正反两个方向都判成立，说明模型判断不稳。
    # 不全部丢弃 —— 按时间先后保留「早→晚」那条，因为因果必须是原因在前。
    if causal:
        seen_pairs: dict[tuple, list[dict]] = {}
        for c in causal:
            key = tuple(sorted([c["from"], c["to"]]))
            seen_pairs.setdefault(key, []).append(c)
        kept = []
        for key, group in seen_pairs.items():
            if len(group) == 1:
                kept.append(group[0])
                continue
            stat["causal_conflict"] = stat.get("causal_conflict", 0) + len(group)
            # 按 from_time 升序，保留最早 → 最晚的那条
            group_sorted = sorted(group, key=lambda c: c.get("from_time") or "9999")
            kept.append(group_sorted[0])
            stat["causal_conflict_kept"] = stat.get("causal_conflict_kept", 0) + 1
        causal = kept
        stat["causal"] = len(causal)

    return temporal + subevent + causal, stat

    return temporal + subevent + causal, stat


def stats_text(links: list[dict], stat: dict, rows: list[dict]) -> str:
    by_rel = Counter(l["rel"] for l in links)
    # 复合事件（子事件的起点）不算原子事件；孤立率只统计原子事件
    sub_heads = {l["from"] for l in links if l["rel"] == "subevent"}
    touched = set()
    for l in links:
        if l["rel"] == "subevent":
            touched.add(l["to"])
        else:
            touched.add(l["from"])
            touched.add(l["to"])

    # 用每个原子事件的 eid 精确比对，避免文本重名造成误判
    atom_eids = {e["eid"] for e in collect(rows)[0]}
    isolated = len(atom_eids - touched)

    lines = [
        "=== 事件间关系 ===",
        f"原子事件 {len(atom_eids)}，关系边 {len(links)}",
        "",
        "按类型：",
    ]
    for r in ("temporal", "causal", "subevent"):
        lines.append(f"  {r:10s} {by_rel.get(r, 0):5d}")
    lines.append("")
    lines.append(f"复合事件（子事件起点）{len(sub_heads)}")
    lines.append(f"因果候选（规则预筛）{stat['causal_candidates']}"
                 f"，LLM 判定成立 {stat['causal']}")
    if stat.get("causal_detail"):
        lines.append(f"  细分：{stat['causal_detail']}")
    lines.append("")
    lines.append(f"孤立原子事件 {isolated} 个（{isolated/max(len(atom_eids),1):.1%}）")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="事件间关系抽取")
    ap.add_argument("--src", default=str(DATA / "extract" / "kgv13_huanqiu_mil_2026-01.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--llm", action="store_true", help="启用 LLM 判因果")
    ap.add_argument("--no-llm", action="store_true", help="只用规则")
    ap.add_argument("--limit-causal", type=int, default=0, help="最多判定多少对因果候选")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)

    rows = load(Path(a.src))
    use_llm = a.llm and not a.no_llm
    links, stat = build_links(rows, use_llm=use_llm, limit_causal=a.limit_causal)
    print(stats_text(links, stat, rows))

    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as g:
            for l in links:
                g.write(json.dumps(l, ensure_ascii=False) + "\n")
        print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
