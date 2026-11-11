"""事件去碎片化。

同一篇稿件里常出现同一句被切成多个伪事件的情况，例如
「拦截并摧毁胡塞武装射向利雅得的弹道导弹」与「胡塞武装射向利雅得的弹道导弹」。
这里按结构与包含关系合并，并把被删事件上的边改挂到保留的事件。

规则：
1. 最短长度：中文 < 6 字、英文 < 3 词的事件丢弃
2. 空壳事件：不含动词、或只有「宣布 / 报道 / 抵达 / 暂无回应」这类无对象短语的丢弃
3. 子串包含：A 是 B 的子串时只留 B，A 上的边改挂 B
4. 同证据重复：两个事件 evidence 相同则只留信息更完整的一条
5. 名词短语：以「的」结尾或形如「…的一枚导弹」的丢弃

    python3 -m extract.events_fix --src data/extract/batch13_clean.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

MIN_ZH = 6
MIN_EN_WORDS = 3

# 没有动作、只有引述或状态词的空壳
HOLLOW = re.compile(
    r"^(宣布|报道|称|表示|指出|强调|抵达|暂无回应|回应|离开|发生|出现|继续|进行|此次袭击|中断谈判)$"
)
# 名词短语特征：以「的」结尾
NOUN_PHRASE = re.compile(r"的$")
# 词性判断容易漏的动词，作为补充白名单
VERB_HINT = re.compile(
    r"袭击|打击|空袭|发射|拦截|摧毁|击落|部署|抵达|离开|签署|通过|举行|召开|宣布|"
    r"批准|拒绝|谴责|警告|威胁|逮捕|击毙|打死|受伤|死亡|撤离|进入|占领|控制|"
    r"交付|采购|引进|试射|起飞|坠落|爆炸|起火|受损|救援|运送|提供|访问|会谈|会晤|"
    r"达成|决定|要求|呼吁|禁止|限制|制裁|增派|调动|巡逻|演习|训练|"
    r"通过|会谈|座谈|进行|加入|加入|参与|出席|视察|展示|亮相|损毁|瓦解|靠泊|击退|"
    r"反攻|推进|围困|封锁|扣押|释放|交换|移交|承认|否认|驳斥|回应|承诺|保证"
)
EN_VERB_HINT = re.compile(
    r"\b(struck|attacked|launched|fired|intercepted|destroyed|deployed|arrived|"
    r"left|signed|approved|passed|held|announced|rejected|condemned|warned|"
    r"arrested|killed|injured|withdrew|entered|captured|delivered|test-fired|"
    r"crashed|exploded|caught fire|damaged|rescued|met|agreed|decided|demanded)\b",
    re.I,
)


def _is_en(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", text or "")) and not re.search(r"[\u4e00-\u9fff]", text or "")


def _has_verb(text: str) -> bool:
    """判断事件文本里是否含动词。词性判断为主，线索词表兜底。"""
    t = (text or "").strip()
    if not t:
        return False
    if _is_en(t):
        return bool(EN_VERB_HINT.search(t))
    # 线索词优先：部分词被 jieba 标成介词或副词，但确实是事件谓语
    if VERB_HINT.search(t):
        return True
    try:
        import jieba.posseg as pseg

        for _word, flag in pseg.cut(t):
            if flag.startswith("v"):
                return True
    except Exception:
        pass
    return False


def invalid_reason(text: str) -> str:
    """返回不该单独成事件的理由，空字符串表示合格。"""
    t = (text or "").strip()
    if not t:
        return "empty"
    if _is_en(t):
        if len(t.split()) < MIN_EN_WORDS:
            return "too_short_en"
        if not EN_VERB_HINT.search(t):
            return "no_verb_en"
        return ""
    if len(t) < MIN_ZH:
        return "too_short"
    if HOLLOW.match(t):
        return "hollow"
    if NOUN_PHRASE.search(t):
        return "noun_phrase"
    if not _has_verb(t):
        return "no_verb"
    return ""


def fix_row(row: dict) -> tuple[dict, Counter]:
    stats = Counter()
    ents = row.get("entities") or []
    events = [e for e in ents if e.get("type") == "事件"]
    others = [e for e in ents if e.get("type") != "事件"]

    # 一、结构过滤
    kept: list[dict] = []
    for ev in events:
        why = invalid_reason(ev.get("text") or "")
        if why:
            stats[f"drop_{why}"] += 1
            continue
        kept.append(ev)
    stats["drop_short_or_hollow"] = sum(
        v for k, v in stats.items() if k.startswith("drop_")
    )

    # 二、子串合并：短串并入长串
    texts = sorted({e["text"] for e in kept}, key=len)
    merge: dict[str, str] = {}
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            if a != b and a in b:
                merge[a] = b
                break
    if merge:
        stats["merged_substring"] += len(merge)
    # 传递合并
    def resolve(t: str) -> str:
        seen = set()
        while t in merge and t not in seen:
            seen.add(t)
            t = merge[t]
        return t

    # 三、同证据重复：evidence 相同的两条，留文字更长的
    by_ev: dict[str, list[dict]] = {}
    for ev in kept:
        by_ev.setdefault(ev.get("text") or "", []).append(ev)
    keep_texts: set[str] = set()
    for t in texts:
        r = resolve(t)
        keep_texts.add(r)
    best_by_ev: dict[str, str] = {}
    for ev in kept:
        ev_text = ev.get("text") or ""
        ev_sent = (ev.get("evidence") or "")
        if not ev_sent:
            continue
        cur = best_by_ev.get(ev_sent)
        if not cur or len(ev_text) > len(cur):
            best_by_ev[ev_sent] = ev_text
    for ev in kept:
        t = ev.get("text") or ""
        sent = ev.get("evidence") or ""
        if sent and best_by_ev.get(sent) not in (None, resolve(t)):
            merge[t] = best_by_ev[sent]
            stats["merged_same_evidence"] += 1

    final_texts = {resolve(t) for t in keep_texts}
    final_texts = {t for t in final_texts if not invalid_reason(t)}

    # 四、重建事件实体，并把边改挂
    new_events = []
    kind_of = {e["text"]: e.get("kind") or "发生" for e in events}
    for t in final_texts:
        src = next((e for e in events if e["text"] == t), None)
        rec = {"type": "事件", "text": t, "kind": (src or {}).get("kind") or kind_of.get(t, "发生")}
        new_events.append(rec)

    rels = []
    seen = set()
    for rel in row.get("relations") or []:
        head = resolve(rel.get("head") or "")
        tail = resolve(rel.get("tail") or "")
        if head in final_texts or tail in final_texts or True:
            pass
        rec = {**rel, "head": head, "tail": tail}
        if rel.get("head") != head or rel.get("tail") != tail:
            stats["edge_remapped"] += 1
        key = (rec["name"], head, tail)
        if key in seen:
            stats["edge_dedup"] += 1
            continue
        seen.add(key)
        rels.append(rec)

    out = {
        **row,
        "entities": others + new_events,
        "relations": rels,
        "events_before": len(events),
        "events_after": len(new_events),
    }
    return out, stats


def run(src: Path, out: Optional[Path] = None) -> dict:
    if out is None:
        out = src.with_name(src.stem + "_evfix.jsonl")
    total = Counter()
    before = after = 0
    rows = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with src.open(encoding="utf-8") as f, out.open("w", encoding="utf-8") as g:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("error"):
                g.write(json.dumps(row, ensure_ascii=False) + "\n")
                continue
            n_ev = sum(1 for e in row.get("entities") or [] if e.get("type") == "事件")
            new_row, st = fix_row(row)
            total.update(st)
            before += n_ev
            after += new_row["events_after"]
            rows += 1
            g.write(json.dumps(new_row, ensure_ascii=False) + "\n")
    return {
        "docs": rows,
        "events_before": before,
        "events_after": after,
        "reduction": f"{(1 - after / max(1, before)):.1%}",
        "actions": dict(total),
        "out": str(out),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="事件去碎片化")
    p.add_argument("--src", default=str(DATA / "extract" / "batch13_clean.jsonl"))
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    print(json.dumps(run(Path(a.src), Path(a.out) if a.out else None),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
