"""原文时间短语 → 日历粒度 + 线索顺序。解不开不写发稿日。"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

ISO_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
YMD_CN = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
YM_YEAR = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?!\s*\d)")
MD_CN = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
D_CN = re.compile(r"(?<!\d)(\d{1,2})\s*日")
YM_CN = re.compile(r"(今年|去年)\s*(\d{1,2})\s*月(?!\s*\d)")
SPAN_MD = re.compile(
    r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[至到]\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日"
)
SPAN_D = re.compile(r"(?<!\d)(\d{1,2})\s*日\s*[至到]\s*(\d{1,2})\s*日")

EMPTY = {"", "空", "无", "未知", "none", "null"}
FUZZY_UNKNOWN = ("日前", "近日", "近几日", "近来", "近期")
FAR_UNKNOWN = ("过去", "近年", "以来", "多年来")
WEEK_AGO = ("上周", "上星期")
AFTER_CUES = ("随后", "之后", "接着", "然后")
SAME_DAY_CUES = ("当天", "同日")
TODAY_CUES = ("今日", "今天")
BEFORE_CUES = ("此前", "之前", "早前")
PUB_BEFORE_CUES = ("日前", "近日", "近几日", "近来", "近期")


def parse_pub_date(iso: str | None) -> date | None:
    raw = (iso or "")[:10]
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _safe_ymd(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        last = calendar.monthrange(y, m)[1]
        try:
            return date(y, m, min(d, last)).isoformat()
        except ValueError:
            return None


def _month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def _empty_meta(raw: str = "", *, precision: str = "unknown", tlink: str = "", cue: str = "") -> dict:
    return {
        "day": "",
        "end": "",
        "precision": precision,
        "background": False,
        "raw": raw,
        "tlink": tlink,
        "cue": cue,
    }


def _abs_day(prev_abs_day: str | None) -> str:
    day = prev_abs_day or ""
    return day if len(day) == 10 else ""


def last_abs_day_in_text(text: str, pub: date | None) -> str | None:
    """文本里最后一处可解析的绝对日（不含当天/随后）。"""
    if not text:
        return None
    hits: list[tuple[int, str]] = []
    covered: list[tuple[int, int]] = []
    for rx in (ISO_RE, YMD_CN, MD_CN):
        for m in rx.finditer(text):
            meta = resolve_meta(m.group(0), pub, None)
            day = meta.get("day") or ""
            if meta.get("precision") == "day" and len(day) == 10:
                hits.append((m.start(), day))
                covered.append((m.start(), m.end()))
    for m in D_CN.finditer(text):
        if any(a <= m.start() and m.end() <= b for a, b in covered):
            continue
        meta = resolve_meta(m.group(0), pub, None)
        day = meta.get("day") or ""
        if meta.get("precision") == "day" and len(day) == 10:
            hits.append((m.start(), day))
    for cue in ("昨天", "昨日", "前天", "今天", "今日"):
        start = 0
        while True:
            i = text.find(cue, start)
            if i < 0:
                break
            meta = resolve_meta(cue, pub, None)
            day = meta.get("day") or ""
            if meta.get("precision") == "day" and len(day) == 10:
                hits.append((i, day))
            start = i + 1
    if not hits:
        return None
    hits.sort(key=lambda x: x[0])
    return hits[-1][1]


def prefix_before_cue(chunk_text: str, evidence: str, raw_time: str) -> str:
    text = chunk_text or ""
    evd = (evidence or "").strip()
    cue = (raw_time or "").strip()
    if evd and evd in text:
        base = text.find(evd)
        if cue and cue in evd:
            return text[: base + evd.find(cue)]
        return text[:base]
    if cue and cue in text:
        return text[: text.find(cue)]
    return text


def inherit_anchor(
    prev_abs_day: str | None,
    chunk_text: str,
    evidence: str,
    raw_time: str,
    publish_time: str | None,
) -> str | None:
    """同块上一条绝对日；否则线索词之前最近的绝对日。不用见报日硬填。"""
    if _abs_day(prev_abs_day):
        return prev_abs_day
    pub = parse_pub_date(publish_time)
    prefix = prefix_before_cue(chunk_text, evidence, raw_time)
    return last_abs_day_in_text(prefix, pub)


def usable_occur_anchor(kind: str, day: str, precision: str, publish_time: str | None) -> bool:
    """只有「发生」且不晚于见报日的绝对日，才能当「当天/随后」继承锚。"""
    if (precision or "") != "day" or not _abs_day(day):
        return False
    if (kind or "") != "发生":
        return False
    pub = parse_pub_date(publish_time)
    if pub and day > pub.isoformat():
        return False
    return True


def same_sentence_abs_day(evidence: str, publish_time: str | None) -> str | None:
    return last_abs_day_in_text(evidence or "", parse_pub_date(publish_time))


def cue_from_text(text: str) -> tuple[str, str]:
    """(AFTER|BEFORE|SAME_DAY|'', cue)。"""
    t = text or ""
    for c in AFTER_CUES:
        if c in t:
            return "AFTER", c
    for c in SAME_DAY_CUES:
        if c in t:
            return "SAME_DAY", c
    for c in BEFORE_CUES:
        if c in t:
            return "BEFORE", c
    for c in PUB_BEFORE_CUES:
        if c in t:
            return "BEFORE", c
    return "", ""


def detect_tlink(ev: dict, prev: dict | None) -> tuple[str, str]:
    raw = str(ev.get("raw_time") or "")
    order, cue = cue_from_text(raw)
    if order:
        return order, cue
    evd = str(ev.get("evidence") or "")
    trig = str(ev.get("trigger") or "")
    order, cue = cue_from_text(evd)
    if order == "AFTER" and cue and trig:
        ci, ti = evd.find(cue), evd.find(trig)
        if ti < 0 or (ci >= 0 and ti < ci):
            order, cue = "", ""
    if order:
        return order, cue
    if prev and prev.get("trigger") and evd:
        ptrig = str(prev.get("trigger") or "")
        if ptrig and ptrig in evd:
            i, j = evd.find(ptrig), evd.find("后")
            if j > i >= 0:
                return "AFTER", "后"
    return "", ""


def resolve_meta(phrase: str | None, pub: date | None, prev_abs_day: str | None = None) -> dict:
    text = str(phrase or "").strip()
    tlink, cue = cue_from_text(text)
    if text.lower() in EMPTY:
        return _empty_meta("", precision="unknown")

    m = ISO_RE.search(text)
    if m:
        day = m.group(1)
        return {
            "day": day,
            "end": "",
            "precision": "day",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    m = YMD_CN.search(text)
    if m:
        day = _safe_ymd(int(m.group(1)), int(m.group(2)), int(m.group(3))) or ""
        return {
            "day": day,
            "end": "",
            "precision": "day" if day else "unknown",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    m = SPAN_MD.search(text)
    if m and pub is not None:
        m1, d1 = int(m.group(1)), int(m.group(2))
        m2, d2 = (int(m.group(3)) if m.group(3) else m1), int(m.group(4))
        year = pub.year
        start = _safe_ymd(year, m1, d1) or ""
        end = _safe_ymd(year, m2, d2) or ""
        return {
            "day": start,
            "end": end,
            "precision": "interval",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    m = SPAN_D.search(text)
    if m and pub is not None:
        d1, d2 = int(m.group(1)), int(m.group(2))
        start = _safe_ymd(pub.year, pub.month, d1) or ""
        end = _safe_ymd(pub.year, pub.month, d2) or ""
        return {
            "day": start,
            "end": end,
            "precision": "interval",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    m = YM_YEAR.search(text)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return {
                "day": f"{y:04d}-{mo:02d}",
                "end": "",
                "precision": "month",
                "background": False,
                "raw": text,
                "tlink": tlink,
                "cue": cue,
            }

    if any(k in text for k in WEEK_AGO) and pub is not None:
        day = (pub - timedelta(days=7)).isoformat()
        return {
            "day": day,
            "end": "",
            "precision": "week",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    if any(k in text for k in ("昨日", "昨天")) and pub is not None:
        return {
            "day": (pub - timedelta(days=1)).isoformat(),
            "end": "",
            "precision": "day",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    if "前天" in text and pub is not None:
        return {
            "day": (pub - timedelta(days=2)).isoformat(),
            "end": "",
            "precision": "day",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    m = MD_CN.search(text)
    if m and pub is not None:
        month, day_n = int(m.group(1)), int(m.group(2))
        year = pub.year
        if month > pub.month + 1:
            year -= 1
        day = _safe_ymd(year, month, day_n) or ""
        return {
            "day": day,
            "end": "",
            "precision": "day" if day else "unknown",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    m = D_CN.search(text)
    if m and pub is not None:
        day_n = int(m.group(1))
        if 1 <= day_n <= 31:
            year, month = pub.year, pub.month
            if day_n > pub.day and (day_n - pub.day) > 7:
                month -= 1
                if month < 1:
                    month, year = 12, year - 1
            day = _safe_ymd(year, month, day_n) or ""
            return {
                "day": day,
                "end": "",
                "precision": "day" if day else "unknown",
                "background": False,
                "raw": text,
                "tlink": tlink,
                "cue": cue,
            }

    if any(k in text for k in TODAY_CUES) and pub is not None:
        return {
            "day": pub.isoformat(),
            "end": "",
            "precision": "day",
            "background": False,
            "raw": text,
            "tlink": tlink,
            "cue": cue,
        }
    if any(k in text for k in SAME_DAY_CUES):
        day = _abs_day(prev_abs_day)
        if day:
            return {
                "day": day,
                "end": "",
                "precision": "day",
                "background": False,
                "raw": text,
                "tlink": "SAME_DAY",
                "cue": cue or "当天",
            }
        return _empty_meta(text, precision="unknown", tlink="SAME_DAY", cue=cue or "当天")

    m = YM_CN.search(text)
    if m and pub is not None:
        month = int(m.group(2))
        year = pub.year - (1 if m.group(1) == "去年" else 0)
        if 1 <= month <= 12:
            return {
                "day": f"{year:04d}-{month:02d}",
                "end": "",
                "precision": "month",
                "background": False,
                "raw": text,
                "tlink": tlink,
                "cue": cue,
            }

    if "年底" in text:
        year = pub.year if pub else 0
        ym = re.search(r"(20\d{2})", text)
        if ym:
            year = int(ym.group(1))
        elif pub and "去年" in text:
            year -= 1
        if year:
            return {
                "day": f"{year:04d}-12",
                "end": "",
                "precision": "month",
                "background": False,
                "raw": text,
                "tlink": tlink,
                "cue": cue,
            }

    if any(k in text for k in AFTER_CUES):
        day = _abs_day(prev_abs_day)
        if day:
            return {
                "day": day,
                "end": "",
                "precision": "day",
                "background": False,
                "raw": text,
                "tlink": "AFTER",
                "cue": cue or "随后",
            }
        return _empty_meta(text, precision="unknown", tlink="AFTER", cue=cue or "随后")
    if any(k in text for k in FUZZY_UNKNOWN):
        return _empty_meta(text, precision="fuzzy", tlink=tlink or "BEFORE", cue=cue or "日前")
    if any(k in text for k in FAR_UNKNOWN):
        meta = _empty_meta(text, precision="fuzzy", tlink=tlink, cue=cue)
        meta["background"] = True
        return meta
    if tlink:
        prec = "fuzzy" if tlink == "BEFORE" and cue in PUB_BEFORE_CUES else "unknown"
        return _empty_meta(text, precision=prec, tlink=tlink, cue=cue)
    return _empty_meta(text, precision="unknown")


def resolve_phrase(phrase: str | None, pub: date | None) -> str | None:
    """简报错日校验：只返回完整 YYYY-MM-DD。"""
    meta = resolve_meta(phrase, pub, None)
    day = meta.get("day") or ""
    if meta.get("precision") in ("day", "week") and len(day) == 10:
        return day
    return None


def _phrases(ev: dict) -> list[str]:
    out = [str(ev.get("raw_time") or "")]
    for arg in ev.get("arguments") or []:
        role = str(arg.get("role") or "")
        if "时间" in role or role in ("when", "time"):
            out.append(str(arg.get("text") or ""))
    return out


def _is_background(day: str, precision: str, pub: date | None) -> bool:
    if not day or not pub:
        return False
    try:
        if precision == "month" and len(day) == 7:
            d = date.fromisoformat(day + "-01")
        elif len(day) >= 10:
            d = date.fromisoformat(day[:10])
        else:
            return False
    except ValueError:
        return False
    return (pub - d).days >= 30


def event_meta(
    ev: dict,
    publish_time: str | None,
    triples: list[dict] | None = None,
    prev_abs_day: str | None = None,
) -> dict:
    """day / end / precision / background / tlink。不再用 pub、triple 回填。"""
    del triples  # 明确取消三元组日期回填
    pub = parse_pub_date(publish_time)
    last = None
    for phrase in _phrases(ev):
        text = str(phrase or "").strip()
        if text.lower() in EMPTY:
            continue
        last = resolve_meta(text, pub, prev_abs_day)
        last["background"] = last.get("background") or _is_background(
            last.get("day") or "", last.get("precision") or "", pub
        )
        if last.get("precision") != "unknown" or last.get("tlink"):
            return last
    if last:
        return last
    blob = " ".join(_phrases(ev))
    tlink, cue = cue_from_text(blob)
    if tlink:
        prec = "fuzzy" if cue in PUB_BEFORE_CUES else "unknown"
        return _empty_meta(blob.strip(), precision=prec, tlink=tlink, cue=cue)
    return _empty_meta("", precision="unknown")


def event_day(ev: dict, publish_time: str | None, triples: list[dict] | None = None) -> str:
    return event_meta(ev, publish_time, triples).get("day") or ""


def overlaps_window(event_time: str, precision: str, day_from: str, day_to: str, end: str = "") -> bool | None:
    """True 落入窗；False 明确在窗外；None 不可比（unknown/fuzzy）。"""
    if precision in ("pub", "triple", ""):
        precision = "day" if event_time and len(event_time) >= 10 else "unknown"
    if precision in ("unknown", "fuzzy") or not event_time:
        return None
    if precision in ("day", "week") and len(event_time) >= 10:
        day = event_time[:10]
        if day_from and day < day_from:
            return False
        if day_to and day > day_to:
            return False
        return True
    if precision == "interval" and len(event_time) >= 10:
        lo, hi = event_time[:10], (end or event_time)[:10]
        if hi < lo:
            lo, hi = hi, lo
        if day_to and lo > day_to:
            return False
        if day_from and hi < day_from:
            return False
        return True
    if precision in ("month", "year"):
        if precision == "year" and len(event_time) >= 4:
            start, stop = f"{event_time[:4]}-01-01", f"{event_time[:4]}-12-31"
        elif len(event_time) >= 7:
            ym = event_time[:7]
            start, stop = f"{ym}-01", _month_end(ym)
        else:
            return None
        if day_to and start > day_to:
            return False
        if day_from and stop < day_from:
            return False
        return True
    return None
