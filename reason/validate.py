"""简报硬校验：引用必须在证据包内；时间必须能在所引条目的摘录里对上。"""

from __future__ import annotations

import re
from typing import Any

from graph.time import parse_pub_date, resolve_phrase

DATE_TOKEN_RE = re.compile(
    r"20\d{2}-\d{2}-\d{2}|20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*月\s*\d{1,2}\s*日|(?<!\d)\d{1,2}\s*日"
)
YEAR_RE = re.compile(r"(20\d{2})\s*年")
SPAN_RE = re.compile(r"(\d{1,2})\s*日\s*[至到]\s*(\d{1,2})\s*日")
SPAN_MD_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[至到]\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日")
CITE_RE = re.compile(r"\[(?:cite[:：]\s*)?([0-9a-fA-F]{6,16})\|([A-Za-z0-9_]+)\]")
CITE_NORM_RE = re.compile(r"\[(?:cite[:：]\s*)([0-9a-fA-F]{6,16}\|[^]]+)\]", re.I)
BANNED = ("已确认", "已核实", "可以确认", "必然", "由此证明")
REL_CUES = ("上周", "上星期", "日前", "近日", "近几日", "今年")

_SKIP_PREFIX = ("#", "```")
_SKIP_CONTAINS = (
    "不得写成",
    "不得视为",
    "single_source",
    "证据不足",
    "校验",
    "程序清单",
    "据环球",
    "报道主张",
)


def _cite_blob(hits: list[dict]) -> str:
    parts: list[str] = []
    for h in hits:
        parts.append(str(h.get("evidence") or ""))
        parts.append(str(h.get("raw_time") or ""))
        parts.append(str(h.get("event_time") or ""))
        for a in h.get("arguments") or []:
            parts.append(str(a.get("text") or ""))
    return "\n".join(parts)


def _norm_time_token(tok: str) -> str:
    return re.sub(r"\s+", "", tok or "")


def _token_in_blob(tok: str, blob: str) -> bool:
    if not tok:
        return False
    if tok in blob:
        return True
    compact = _norm_time_token(tok)
    return bool(compact) and compact in _norm_time_token(blob)


def date_not_in_cites(line: str, day: str, hits: list[dict]) -> bool:
    """简报日期必须能在所引条目的 evidence/raw_time 里找到，或等于查询日。"""
    blob = _cite_blob(hits)
    pub = parse_pub_date(day)
    if not pub:
        return False
    for cue in REL_CUES:
        if cue in line and cue not in blob:
            return True
    for m in YEAR_RE.finditer(line):
        if not _token_in_blob(m.group(0), blob) and m.group(1) not in blob:
            # 查询年如 2026 年允许（简报常写「2026年8月21日」）
            if m.group(1) != f"{pub.year:04d}":
                return True
    skip = []
    for m in SPAN_RE.finditer(line):
        if _token_in_blob(m.group(0), blob):
            skip.append(m.span())
            continue
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        if lo <= pub.day <= hi:
            skip.append(m.span())
    for m in SPAN_MD_RE.finditer(line):
        if _token_in_blob(m.group(0), blob):
            skip.append(m.span())
            continue
        month, d1, month2, d2 = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        m2 = int(month2) if month2 else month
        if month == pub.month == m2 and min(d1, d2) <= pub.day <= max(d1, d2):
            skip.append(m.span())
    for m in DATE_TOKEN_RE.finditer(line):
        if any(s <= m.start() < e for s, e in skip):
            continue
        tok = m.group(0)
        if _token_in_blob(tok, blob):
            continue
        got = resolve_phrase(tok, pub)
        if got == day:
            continue
        if got:
            return True
        # 「24日」resolve 成别的日，且摘录没有该子串
        if "日" in tok and not _token_in_blob(tok, blob):
            day_n = re.search(r"(\d{1,2})\s*日", tok)
            if day_n and int(day_n.group(1)) != pub.day:
                return True
    return False


def _norm_cites(text: str) -> str:
    return CITE_NORM_RE.sub(r"[\1]", text)


def _lookup(eid: str, cid: str, index: dict[str, dict]) -> dict | None:
    key = f"{eid}|{cid}"
    if key in index:
        return index[key]
    for it in index.values():
        full = str(it.get("event_id") or "")
        chunk = str(it.get("chunk_id") or "")
        if chunk == cid and (full == eid or full.startswith(eid) or eid.startswith(full[:8])):
            return it
    return None


def _is_meta(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s.startswith(_SKIP_PREFIX):
        return True
    if any(k in s for k in _SKIP_CONTAINS):
        return True
    if s.startswith("- 查询日"):
        return True
    return False


def _is_claim(line: str) -> bool:
    s = line.strip()
    if _is_meta(s):
        return False
    return True


def validate_brief(text: str, serialized: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    text = _norm_cites(text or "")
    index = {}
    for it in serialized.get("items") or []:
        cite = str(it.get("cite") or "")
        if cite:
            index[cite] = it
        eid = str(it.get("event_id") or "")
        cid = str(it.get("chunk_id") or "")
        if eid and cid:
            index[f"{eid}|{cid}"] = it

    day = str(serialized.get("day") or "")
    kept: list[str] = []
    dropped: list[str] = []
    n_cite = 0
    n_bad_cite = 0
    n_claim = 0
    n_claim_ok = 0

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        if _is_meta(line) or not _is_claim(line):
            if any(b in line for b in BANNED) and _is_claim(line):
                dropped.append(line.strip())
                continue
            kept.append(line)
            continue
        n_claim += 1
        if any(b in line for b in BANNED):
            dropped.append(line.strip() + "  # banned")
            continue
        cites = CITE_RE.findall(line)
        if not cites:
            dropped.append(line.strip() + "  # no_cite")
            continue
        hits: list[dict] = []
        ok = True
        for eid, cid in cites:
            n_cite += 1
            hit = _lookup(eid, cid, index)
            if hit is None:
                n_bad_cite += 1
                ok = False
                break
            et = str(hit.get("event_time") or "")
            if day and et and et != day:
                n_bad_cite += 1
                ok = False
                break
            hits.append(hit)
        if not ok:
            dropped.append(line.strip() + "  # bad_cite")
            continue
        if day and date_not_in_cites(line, day, hits):
            dropped.append(line.strip() + "  # date_leak")
            continue
        n_claim_ok += 1
        kept.append(line)

    body = "\n".join(kept).strip() + "\n"
    cite_ok = n_cite - n_bad_cite
    rate = (n_claim_ok / n_claim) if n_claim else 1.0
    report = {
        "n_claim": n_claim,
        "n_claim_kept": n_claim_ok,
        "n_claim_dropped": n_claim - n_claim_ok,
        "n_cite": n_cite,
        "n_cite_ok": cite_ok,
        "n_bad_cite": n_bad_cite,
        "cite_rate": round(rate, 3),
        "ok": rate >= 0.9 and n_bad_cite == 0,
        "dropped": dropped[:20],
    }
    return body, report
