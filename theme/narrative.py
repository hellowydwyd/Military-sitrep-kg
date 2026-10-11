"""查询窗核心议题：按周标题+发生事件归纳前因后果，不写稿量。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from theme.config import DATA, THEME_DIR
from theme.name import sample_titles

CACHE = THEME_DIR / "narrative_cache.json"
ROLES = ("起因", "推进", "转折", "后果")
EXTRACTS = (
    DATA / "extract" / "openie_event.jsonl",
    DATA / "extract" / "openie_event_rss.jsonl",
)

SYSTEM = (
    "你是军事态势简报编辑。输入是一条议题在时间窗内按周排列的标题和已抽取事件。"
    "任务：梳理这条线上实际发生了什么，写出前因后果，不要写报道量、热度、关注度、篇数。"
    "规则："
    "1. lede 两句：先说因何而起，再说窗末日落到哪一步。"
    "2. beats 3 到 6 条，按时间。role 只能是 起因/推进/转折/后果。"
    "   第一条尽量起因，最后一条尽量后果；中间有明显变向才标转折，其余标推进。"
    "3. 每条 beat 一句，说清主体、动作、对象或地点。能从前一周推出后续的，用「随后」「作为回应」标明。"
    "4. 专名、数字、地点必须来自输入。禁止编造未出现的战果、协议或伤亡。"
    "5. 安静的周不要凑条。from/to 用输入 weeks 的日期。"
    "只输出 JSON："
    '{"lede":"...","beats":[{"from":"YYYY-MM-DD","to":"YYYY-MM-DD","role":"起因","text":"..."}]}'
)


def _parse(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).removesuffix("```").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        return {}
    lede = str(data.get("lede") or "").strip()
    beats = []
    for b in data.get("beats") or []:
        if not isinstance(b, dict):
            continue
        role = str(b.get("role") or "").strip()
        if role not in ROLES:
            role = "推进"
        text_b = str(b.get("text") or "").strip()
        if not text_b:
            continue
        beats.append(
            {
                "from": str(b.get("from") or "")[:10],
                "to": str(b.get("to") or "")[:10],
                "role": role,
                "text": text_b[:120],
            }
        )
    if not beats and not lede:
        return {}
    return {"lede": lede[:180], "beats": beats[:6]}


def _load_cache() -> dict:
    if not CACHE.is_file():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict) -> None:
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fmt_event(ev: dict) -> str:
    args = {
        a.get("role"): (a.get("text") or "").strip()
        for a in (ev.get("arguments") or [])
        if a.get("role") and a.get("text")
    }
    subj = args.get("主体") or ""
    obj = args.get("对象") or ""
    trig = (ev.get("trigger") or "").strip()
    if not trig or not (subj or obj):
        return ""
    return f"{subj}{trig}{obj}"[:48]


def _event_index() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for path in EXTRACTS:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            did = rec.get("doc_id") or ""
            if not did:
                continue
            happen, other = [], []
            for ch in rec.get("chunks") or []:
                for ev in ch.get("events") or []:
                    bit = _fmt_event(ev)
                    if not bit:
                        continue
                    kind = ev.get("kind") or ""
                    if kind == "发生":
                        happen.append(bit)
                    elif kind != "表态":
                        other.append(bit)
            seen, packed = set(), []
            for bit in happen + other:
                if bit in seen:
                    continue
                seen.add(bit)
                packed.append(bit)
                if len(packed) >= 4:
                    break
            if packed:
                out[did] = ((rec.get("publish_time") or "")[:10], "；".join(packed))
    return out


_EVENTS: dict[str, tuple[str, str]] | None = None


def events_of(doc_ids: list[str]) -> list[str]:
    global _EVENTS
    if _EVENTS is None:
        _EVENTS = _event_index()
    seen, rows = set(), []
    for did in doc_ids:
        pair = (_EVENTS or {}).get(did)
        if not pair:
            continue
        bit = pair[1]
        if bit in seen:
            continue
        seen.add(bit)
        rows.append(bit)
        if len(rows) >= 8:
            break
    return rows


def _weeks(theme: dict, docs: list[dict], columns: list[dict]) -> list[dict]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for a in docs:
        by_day[a.get("day") or ""].append(a)
    spans = [(c.get("from") or "", c.get("to") or "") for c in (columns or []) if c.get("from")]
    if not spans:
        days = sorted(d for d in by_day if d)
        if not days:
            return []
        spans = [(days[0], days[-1])]
    weeks = []
    for a, b in spans:
        chunk = [x for d, xs in by_day.items() if a <= d <= b for x in xs]
        if not chunk:
            continue
        titles = sample_titles(chunk, 8)
        evs = events_of([x.get("doc_id") or "" for x in chunk])
        weeks.append({"from": a, "to": b, "n": len(chunk), "titles": titles, "events": evs})
    if len(weeks) <= 8:
        return weeks
    mid = sorted(weeks[1:-1], key=lambda w: -w["n"])[:6]
    keep = [weeks[0], *sorted(mid, key=lambda w: w["from"]), weeks[-1]]
    seen, uniq = set(), []
    for w in keep:
        if w["from"] in seen:
            continue
        seen.add(w["from"])
        uniq.append(w)
    return uniq


def _cache_key(window: dict, theme: dict, weeks: list[dict]) -> str:
    blob = json.dumps(
        {
            "w": [window.get("from"), window.get("to")],
            "id": theme.get("theme_id"),
            "name": theme.get("name"),
            "weeks": [(w["from"], w["to"], w["titles"], w["events"]) for w in weeks],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _fallback(theme: dict, weeks: list[dict]) -> dict:
    beats = []
    roles = ["起因", "推进", "推进", "转折", "后果", "后果"]
    usable = [w for w in weeks if w.get("titles") or w.get("events")]
    n = len(usable)
    for i, w in enumerate(usable[:6]):
        if n == 1:
            role = "起因"
        elif i == 0:
            role = "起因"
        elif i == n - 1:
            role = "后果"
        else:
            role = roles[min(i, len(roles) - 1)]
        text = (w.get("events") or [None])[0] or (w.get("titles") or [""])[0]
        if not text:
            continue
        beats.append({"from": w["from"], "to": w["to"], "role": role, "text": text[:120]})
    name = theme.get("name") or "该议题"
    lede = f"{name}在本窗内按见报标题可串起以上事态。"
    return {"lede": lede, "beats": beats, "source": "fallback"}


def narrate_theme(window: dict, theme: dict, docs: list[dict], columns: list[dict], *, force: bool = False) -> dict:
    weeks = _weeks(theme, docs, columns)
    if not weeks:
        return {"lede": "", "beats": [], "source": "empty"}
    key = _cache_key(window, theme, weeks)
    cache = _load_cache()
    if not force and key in cache:
        return cache[key]
    payload = {
        "window": window,
        "theme": theme.get("name") or theme.get("theme_id"),
        "weeks": [{"from": w["from"], "to": w["to"], "titles": w["titles"], "events": w["events"]} for w in weeks],
    }
    try:
        from extract.llm import chat_json

        raw = chat_json(SYSTEM, json.dumps(payload, ensure_ascii=False), temperature=0.0, timeout=120)
        parsed = _parse(raw)
        if not parsed.get("beats"):
            raise RuntimeError("empty beats")
        rec = {"lede": parsed.get("lede") or "", "beats": parsed["beats"], "source": "llm"}
    except Exception:
        rec = _fallback(theme, weeks)
    cache[key] = rec
    _save_cache(cache)
    return rec


def fill_slice(out: dict, *, force: bool = False) -> int:
    """给核心议题写 narrative。返回新写入条数。"""
    window = out.get("window") or {}
    columns = out.get("columns") or []
    by_theme: dict[str, list[dict]] = defaultdict(list)
    for d in out.get("docs") or []:
        tid = d.get("theme_id") or ""
        if tid:
            by_theme[tid].append(d)
    n = 0
    for t in out.get("themes") or []:
        had = t.get("narrative") or {}
        if not force and (had.get("beats") or had.get("lede")):
            continue
        rec = narrate_theme(window, t, by_theme.get(t["theme_id"]) or [], columns, force=force)
        t["narrative"] = {"lede": rec.get("lede") or "", "beats": rec.get("beats") or []}
        n += 1
    return n
