"""论元必须是原文子串；丢掉代词实体。"""

from __future__ import annotations

import re
from typing import Any

PRONOUNS = (
    "他",
    "她",
    "它",
    "他们",
    "其",
    "该部队",
    "该组织",
    "上述",
    "双方",
    "前者",
    "后者",
    "有关方面",
)
EN_PRONOUNS = frozenset(
    {
        "he",
        "she",
        "they",
        "them",
        "it",
        "we",
        "him",
        "her",
        "the latter",
        "the former",
        "the group",
        "the organization",
        "the forces",
    }
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def locate_span(needle: str, haystack: str) -> tuple[int, int] | None:
    """必须能给出真实偏移；去空白后的假匹配不算找到。"""
    n, h = needle.strip(), haystack
    if not n:
        return None
    i = h.find(n)
    if i >= 0:
        return i, i + len(n)
    return None


def evidence_sentence(haystack: str, needle: str, *, max_len: int = 120) -> str:
    """取 trigger 所在句，避免时间线只用 chunk 开头。"""
    text, n = haystack or "", (needle or "").strip()
    if not text or not n:
        return ""
    loc = locate_span(n, text)
    if not loc:
        return ""
    i, j = loc
    start = 0
    for sep in ("。", "！", "？", "\n", "；", ". ", "? ", "! "):
        p = text.rfind(sep, 0, i)
        if p + 1 > start:
            start = p + len(sep) if sep.endswith(" ") else p + 1
    end = len(text)
    for sep in ("。", "！", "？", "\n", ". ", "? ", "! "):
        p = text.find(sep, j)
        if 0 <= p < end:
            end = p if sep == "\n" else p + len(sep)
    sent = " ".join(text[start:end].split())
    if len(sent) > max_len:
        sent = sent[: max_len - 1] + "…"
    return sent


SPAN_MD = re.compile(
    r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[至到]\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日"
)
SPAN_D = re.compile(r"(?<!\d)(\d{1,2})\s*日\s*[至到]\s*(\d{1,2})\s*日")
YMD_CN = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
ISO_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
YM_YEAR = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?!\s*\d)")
YM_CN = re.compile(r"(今年|去年)\s*(\d{1,2})\s*月(?!\s*\d)")
MD_CN = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
D_CN = re.compile(r"(?<!\d)(\d{1,2})\s*日")
LEX_TIME = (
    "上星期",
    "近几日",
    "上周",
    "日前",
    "近日",
    "近来",
    "近期",
    "当天",
    "同日",
    "今日",
    "今天",
    "昨日",
    "昨天",
    "前天",
    "随后",
    "之后",
    "接着",
    "然后",
    "此前",
    "之前",
    "早前",
    "年底",
)


def recover_raw_time(evidence: str, hay: str, args: list[dict] | None = None) -> str:
    """LLM 漏填时，从摘录/论元/正文拷贝原文时间子串，不换算。"""
    for arg in args or []:
        if "时间" in str(arg.get("role") or ""):
            t = str(arg.get("text") or "").strip()
            if t and locate_span(t, hay):
                return t
    blob = evidence or hay or ""
    for rx in (SPAN_MD, SPAN_D, YMD_CN, ISO_RE, YM_YEAR, YM_CN, MD_CN):
        m = rx.search(blob)
        if m and locate_span(m.group(0), hay):
            return m.group(0)
    for lex in LEX_TIME:
        if lex in blob and locate_span(lex, hay):
            return lex
    m = D_CN.search(blob)
    if m and locate_span(m.group(0), hay):
        return m.group(0)
    return ""


ALLOWED_ROLES = ("主体", "对象", "地点", "装备")
ROLE_ALIAS = {
    "发起": "主体",
    "参与": "主体",
    "主语": "主体",
    "客体": "对象",
    "目标": "对象",
    "位置": "地点",
    "地域": "地点",
    "武器": "装备",
    "兵力": "装备",
    "舰机": "装备",
}


def canonicalize_role(role: str) -> str | None:
    r = (role or "").strip()
    if r in ALLOWED_ROLES:
        return r
    if r in ROLE_ALIAS:
        return ROLE_ALIAS[r]
    for canon in ALLOWED_ROLES:
        if canon in r:
            return canon
    return None


_VAGUE = re.compile(
    r"^(?:这|该|此|其|上述|前述)"
    r"(?:些|批|种|类)?"
    r"(?:[一二两三四五六七八九十数多几]|\d+)?"
    r"(?:艘|架|名|位|个|批|支|家|辆|枚|部|座|处|款|群|队)?"
)


def is_pronoun(text: str) -> bool:
    t = (text or "").strip()
    if t in PRONOUNS or t in ("该", "此", "这", "其"):
        return True
    if _VAGUE.match(t):
        return True
    return t.casefold() in EN_PRONOUNS


def validate_extraction(data: dict[str, Any], *, title: str, body: str) -> dict[str, Any]:
    hay = (title or "") + "\n" + (body or "")
    kept_t, drop_t = [], []
    for tri in data.get("triples") or []:
        if not isinstance(tri, dict):
            continue
        head, rel, tail = str(tri.get("head") or "").strip(), str(tri.get("relation") or "").strip(), str(tri.get("tail") or "").strip()
        reasons = []
        if not head or not rel or not tail:
            reasons.append("empty")
        if is_pronoun(head) or is_pronoun(tail):
            reasons.append("pronoun")
        if not locate_span(head, hay):
            reasons.append("head_not_in_text")
        if not locate_span(tail, hay):
            reasons.append("tail_not_in_text")
        item = {
            "head": head,
            "relation": rel,
            "tail": tail,
            "t_start": str(tri.get("t_start") or "").strip(),
            "t_end": str(tri.get("t_end") or "").strip(),
            "evidence": str(tri.get("evidence") or "").strip()[:80],
        }
        if reasons:
            item["drop"] = reasons
            drop_t.append(item)
        else:
            kept_t.append(item)

    kept_e, drop_e = [], []
    for ev in data.get("events") or []:
        if not isinstance(ev, dict):
            continue
        trigger = str(ev.get("trigger") or "").strip()
        raw_time = str(ev.get("raw_time") or "").strip()
        if raw_time in ("空", "无", "未知", "none", "null"):
            raw_time = ""
        kind = str(ev.get("kind") or "").strip()
        if kind not in ("发生", "表态", "计划"):
            kind = ""
        reasons = []
        if not trigger or is_pronoun(trigger):
            reasons.append("bad_trigger")
        if trigger and not locate_span(trigger, hay):
            reasons.append("trigger_not_in_text")
        if raw_time and not locate_span(raw_time, hay):
            reasons.append("raw_time_not_in_text")
        evidence = str(ev.get("evidence") or "").strip()
        if not evidence or trigger not in evidence or not locate_span(evidence, hay):
            evidence = evidence_sentence(hay, trigger)
        if trigger and (not evidence or trigger not in evidence):
            reasons.append("evidence_missing")
        args_ok = []
        for arg in ev.get("arguments") or []:
            if not isinstance(arg, dict):
                continue
            role = canonicalize_role(str(arg.get("role") or ""))
            text = str(arg.get("text") or "").strip()
            if not role or not text or is_pronoun(text) or not locate_span(text, hay):
                continue
            loc = locate_span(text, hay)
            rec = {"role": role, "text": text}
            if loc:
                rec["offset"] = [loc[0], loc[1]]
            args_ok.append(rec)
        if not raw_time:
            raw_time = recover_raw_time(evidence, hay, ev.get("arguments") if isinstance(ev.get("arguments"), list) else [])
        trig_loc = locate_span(trigger, hay) if trigger else None
        item = {
            "trigger": trigger,
            "raw_time": raw_time,
            "kind": kind,
            "evidence": evidence[:120],
            "arguments": args_ok,
        }
        if trig_loc:
            item["trigger_offset"] = [trig_loc[0], trig_loc[1]]
        rt_loc = locate_span(raw_time, hay) if raw_time else None
        if rt_loc:
            item["raw_time_offset"] = [rt_loc[0], rt_loc[1]]
        if reasons or not args_ok:
            item["drop"] = reasons or ["no_valid_args"]
            drop_e.append(item)
        else:
            kept_e.append(item)

    kept_e = _dedupe_events(kept_e)
    return {
        "triples": kept_t,
        "events": kept_e,
        "dropped_triples": drop_t,
        "dropped_events": drop_e,
    }


def _primary_actor(ev: dict) -> str:
    for a in ev.get("arguments") or []:
        if any(k in str(a.get("role") or "") for k in ("主体", "发起", "参与")):
            return str(a.get("text") or "")
    return ""


def _dedupe_events(events: list[dict]) -> list[dict]:
    """同 trigger + 同主体 + 同摘录句只留一条。"""
    seen: set[tuple[str, str, str]] = set()
    out = []
    for ev in events:
        key = (ev.get("trigger") or "", _primary_actor(ev), (ev.get("evidence") or "")[:40])
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out
