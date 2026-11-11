"""从开放抽取 JSONL 收主体/对象/装备提及。地点不进主体排行。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from extract.validate import canonicalize_role, is_pronoun
from actor.clean import canonicalize_surface
from ingest.datatypes import classify
from actor.person import parse_person_surface, raw_type_index
from actor.config import (
    ACTOR_DIR,
    EXTRACT_PATH,
    KEEP_ROLES,
    MAX_NAME_CHARS,
    MIN_NAME_CHARS,
    RSS_EXTRACT_PATH,
)
from actor.types import rule_type


def iter_extract(path: Path | None = None):
    src = path or EXTRACT_PATH
    with src.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def harvest_extract(path: Path | None = None) -> list[dict]:
    rows: list[dict] = []
    mid = 0
    for rec in iter_extract(path):
        doc_id = rec.get("doc_id") or ""
        title = rec.get("title") or ""
        day = (rec.get("publish_time") or "")[:10]
        url = rec.get("url") or ""
        source = rec.get("source") or ""
        lang = rec.get("lang") or ""
        extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
        source_name = rec.get("source_name") or extra.get("outlet") or ""
        if source == "huanqiu_mil":
            source_name = source_name or "环球网军事"
        elif source == "rss_api":
            source_name = source_name or "RSS"
        data_type = str(rec.get("data_type") or "") or classify(source, extra)
        if not data_type and doc_id:
            data_type = raw_type_index().get(doc_id, "")
        for ci, ch in enumerate(rec.get("chunks") or []):
            hay = ch.get("text") or ""
            for ei, ev in enumerate(ch.get("events") or []):
                trig = str(ev.get("trigger") or "").strip()
                kind = str(ev.get("kind") or "").strip()
                raw_time = str(ev.get("raw_time") or "").strip()
                places: list[str] = []
                subjects: list[str] = []
                objects: list[str] = []
                for arg in ev.get("arguments") or []:
                    if not isinstance(arg, dict):
                        continue
                    role = canonicalize_role(str(arg.get("role") or ""))
                    text = str(arg.get("text") or "").strip()
                    if not text or is_pronoun(text):
                        continue
                    if role == "地点":
                        places.append(text)
                    elif role == "主体":
                        subjects.append(text)
                    elif role == "对象":
                        objects.append(text)
                place = "、".join(dict.fromkeys(places))
                for arg in ev.get("arguments") or []:
                    if not isinstance(arg, dict):
                        continue
                    role = canonicalize_role(str(arg.get("role") or ""))
                    if role not in KEEP_ROLES:
                        continue
                    raw = str(arg.get("text") or "")
                    if is_pronoun(raw):
                        continue
                    text = canonicalize_surface(raw, role)
                    if not text:
                        continue
                    if len(text) < MIN_NAME_CHARS or len(text) > MAX_NAME_CHARS:
                        continue
                    typ = rule_type(text, role) or ""
                    identity = parse_person_surface(raw)
                    pool = objects if role == "主体" else subjects if role == "对象" else []
                    argument = raw.strip()
                    rows.append(
                        {
                            "mention_id": f"m{mid:06d}",
                            "doc_id": doc_id,
                            "day": day,
                            "url": url,
                            "title": title,
                            "source": source,
                            "source_name": source_name,
                            "lang": lang,
                            "data_type": data_type,
                            "chunk_i": ci,
                            "event_i": ei,
                            "trigger": trig,
                            "kind": kind,
                            "raw_time": raw_time,
                            "place": place,
                            "counterpart": "、".join(
                                dict.fromkeys(x for x in pool if x != text and x != argument)
                            ),
                            "role": role,
                            "surface": text,
                            "argument": argument,
                            "post": identity["post"],
                            "nationality": identity["nationality"],
                            "org": identity["org"],
                            "type_rule": typ,
                            "evidence": (hay[:180] if hay else title),
                            "via": "extract",
                        }
                    )
                    mid += 1
    return rows


def _merge_rows(extract_rows: list[dict], scan_rows: list[dict]) -> list[dict]:
    seen = {(r["doc_id"], r["surface"]) for r in extract_rows}
    out = list(extract_rows)
    extra = 0
    for r in scan_rows:
        key = (r["doc_id"], r["surface"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        extra += 1
    return out


def default_extract_paths() -> list[Path]:
    paths = [EXTRACT_PATH]
    if RSS_EXTRACT_PATH.is_file():
        paths.append(RSS_EXTRACT_PATH)
    return paths


def harvest(path: Path | None = None, *, scan: bool = True) -> list[dict]:
    paths = [path] if path is not None else default_extract_paths()
    rows: list[dict] = []
    for src in paths:
        if src and Path(src).is_file():
            rows.extend(harvest_extract(Path(src)))
    if scan:
        from actor.scan import harvest_scan

        rows = _merge_rows(rows, harvest_scan())
    for i, row in enumerate(rows):
        row["mention_id"] = f"m{i:06d}"
    return rows


def write_mentions(rows: list[dict], dest: Path | None = None) -> Path:
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    out = dest or (ACTOR_DIR / "mentions.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def load_mentions(path: Path | None = None) -> list[dict]:
    src = path or (ACTOR_DIR / "mentions.jsonl")
    rows = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def harvest_stats(rows: list[dict]) -> dict:
    return {
        "n_mentions": len(rows),
        "n_docs": len({r["doc_id"] for r in rows}),
        "n_surfaces": len({r["surface"] for r in rows}),
        "roles": dict(Counter(r["role"] for r in rows)),
        "via": dict(Counter(r.get("via") or "extract" for r in rows)),
        "sources": dict(Counter(r.get("source") or "" for r in rows)),
        "data_types": dict(Counter(r.get("data_type") or "" for r in rows)),
        "langs": dict(Counter(r.get("lang") or "" for r in rows)),
        "typed": sum(1 for r in rows if r.get("type_rule")),
        "days": sorted({r["day"] for r in rows if r.get("day")}),
        "months": sorted({(r["day"] or "")[:7] for r in rows if r.get("day")}),
    }
