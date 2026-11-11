"""加载试点队列、预标，读写 A/B 定稿。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from extract.validate import (
    ALLOWED_ROLES,
    canonicalize_role,
    evidence_sentence,
    is_pronoun,
    locate_span,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PILOT_PATH = DATA / "eval" / "pilot20.json"
PRELABEL_PATH = DATA / "extract" / "openie_event_pilot.jsonl"
ANNOT_ROOT = DATA / "eval" / "annotations"
KINDS = ("发生", "表态", "计划")
ANNOTATORS = ("A", "B")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def annot_dir(annotator: str) -> Path:
    a = (annotator or "A").strip().upper()
    if a not in ANNOTATORS:
        raise ValueError(f"annotator must be A or B, got {annotator!r}")
    d = ANNOT_ROOT / a
    d.mkdir(parents=True, exist_ok=True)
    return d


def meta_path(annotator: str) -> Path:
    return annot_dir(annotator) / "_meta.json"


def load_meta(annotator: str) -> dict[str, Any]:
    p = meta_path(annotator)
    if not p.is_file():
        return {"status": {}}
    data = _read_json(p)
    if not isinstance(data, dict):
        return {"status": {}}
    data.setdefault("status", {})
    return data


def save_meta(annotator: str, meta: dict[str, Any]) -> None:
    _write_json(meta_path(annotator), meta)


def chunk_annot_path(annotator: str, chunk_id: str) -> Path:
    safe = chunk_id.replace("/", "_")
    return annot_dir(annotator) / f"{safe}.json"


def load_pilot() -> list[dict[str, Any]]:
    data = _read_json(PILOT_PATH)
    return list(data.get("chunks") or [])


def load_prelabel_index() -> dict[str, dict[str, Any]]:
    """chunk_id -> {doc meta + chunk}."""
    out: dict[str, dict[str, Any]] = {}
    if not PRELABEL_PATH.is_file():
        return out
    for line in PRELABEL_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        doc = json.loads(line)
        for ch in doc.get("chunks") or []:
            cid = ch.get("chunk_id")
            if not cid:
                continue
            out[cid] = {
                "doc_id": doc.get("doc_id"),
                "title": doc.get("title") or "",
                "publish_time": doc.get("publish_time") or "",
                "url": doc.get("url") or "",
                "source": doc.get("source") or "",
                "chunk_id": cid,
                "ord": ch.get("ord"),
                "text": ch.get("text") or "",
                "events": list(ch.get("events") or []),
            }
    return out


def queue(annotator: str = "A") -> dict[str, Any]:
    meta = load_meta(annotator)
    status = meta.get("status") or {}
    pre = load_prelabel_index()
    items = []
    n_done = 0
    for row in load_pilot():
        cid = row["chunk_id"]
        st = status.get(cid) or "pending"
        if st == "done":
            n_done += 1
        has_save = chunk_annot_path(annotator, cid).is_file()
        items.append(
            {
                "chunk_id": cid,
                "doc_id": row.get("doc_id"),
                "title": row.get("title") or "",
                "publish_time": row.get("publish_time") or "",
                "url": row.get("url") or "",
                "stratum": row.get("stratum") or "",
                "status": st,
                "has_annotation": has_save,
                "has_prelabel": cid in pre,
            }
        )
    return {
        "annotator": annotator.upper(),
        "n": len(items),
        "n_done": n_done,
        "items": items,
    }


def _arg_text(ev: dict, role: str) -> str:
    for a in ev.get("arguments") or []:
        if a.get("role") == role:
            return str(a.get("text") or "").strip()
    return ""


def validate_events_for_save(text: str, events: list[Any]) -> tuple[list[dict], list[str]]:
    """严格校验：非法子串拒绝。返回 (规范化 events, errors)。"""
    hay = text or ""
    errors: list[str] = []
    cleaned: list[dict] = []
    if not isinstance(events, list):
        return [], ["events must be a list"]

    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            errors.append(f"event[{i}] not an object")
            continue
        trigger = str(ev.get("trigger") or "").strip()
        kind = str(ev.get("kind") or "").strip()
        raw_time = str(ev.get("raw_time") or "").strip()
        if raw_time in ("空", "无", "未知", "none", "null"):
            raw_time = ""
        evidence = str(ev.get("evidence") or "").strip()
        prefix = f"event[{i}]"

        if not trigger:
            errors.append(f"{prefix}: empty trigger")
            continue
        if is_pronoun(trigger):
            errors.append(f"{prefix}: trigger is pronoun")
        if not locate_span(trigger, hay):
            errors.append(f"{prefix}: trigger not in text: {trigger!r}")
        if kind and kind not in KINDS:
            errors.append(f"{prefix}: bad kind {kind!r}")
        if not kind:
            errors.append(f"{prefix}: kind required")
        if raw_time and not locate_span(raw_time, hay):
            errors.append(f"{prefix}: raw_time not in text: {raw_time!r}")

        if not evidence or trigger not in evidence or not locate_span(evidence, hay):
            evidence = evidence_sentence(hay, trigger) or evidence
        if trigger and (not evidence or trigger not in evidence):
            errors.append(f"{prefix}: evidence missing or without trigger")

        args_out: list[dict] = []
        for j, arg in enumerate(ev.get("arguments") or []):
            if not isinstance(arg, dict):
                errors.append(f"{prefix}.arg[{j}] not an object")
                continue
            role = canonicalize_role(str(arg.get("role") or ""))
            at = str(arg.get("text") or "").strip()
            if not at:
                continue
            if not role:
                errors.append(f"{prefix}.arg[{j}]: bad role {arg.get('role')!r}")
                continue
            if is_pronoun(at):
                errors.append(f"{prefix}.arg[{j}]: pronoun {at!r}")
                continue
            loc = locate_span(at, hay)
            if not loc:
                errors.append(f"{prefix}.arg[{j}]: text not in chunk: {at!r}")
                continue
            rec = {"role": role, "text": at, "offset": [loc[0], loc[1]]}
            args_out.append(rec)

        if not args_out:
            errors.append(f"{prefix}: need at least one valid argument")

        item: dict[str, Any] = {
            "trigger": trigger,
            "kind": kind,
            "raw_time": raw_time,
            "evidence": (evidence or "")[:120],
            "arguments": args_out,
        }
        tloc = locate_span(trigger, hay)
        if tloc:
            item["trigger_offset"] = [tloc[0], tloc[1]]
        if raw_time:
            rloc = locate_span(raw_time, hay)
            if rloc:
                item["raw_time_offset"] = [rloc[0], rloc[1]]
        cleaned.append(item)

    if errors:
        return [], errors
    return cleaned, []


def get_chunk(chunk_id: str, annotator: str = "A") -> dict[str, Any]:
    pilot_map = {r["chunk_id"]: r for r in load_pilot()}
    if chunk_id not in pilot_map:
        raise KeyError(chunk_id)
    meta_row = pilot_map[chunk_id]
    pre = load_prelabel_index().get(chunk_id) or {}
    text = pre.get("text") or ""
    source = "prelabel"
    notes = ""
    status = (load_meta(annotator).get("status") or {}).get(chunk_id) or "pending"
    saved = chunk_annot_path(annotator, chunk_id)
    if saved.is_file():
        ann = _read_json(saved)
        events = list(ann.get("events") or [])
        notes = str(ann.get("notes") or "")
        source = "annotation"
        if ann.get("text"):
            text = ann["text"]
    else:
        events = list(pre.get("events") or [])

    return {
        "chunk_id": chunk_id,
        "doc_id": meta_row.get("doc_id") or pre.get("doc_id"),
        "title": meta_row.get("title") or pre.get("title") or "",
        "publish_time": meta_row.get("publish_time") or pre.get("publish_time") or "",
        "url": meta_row.get("url") or pre.get("url") or "",
        "stratum": meta_row.get("stratum") or "",
        "text": text,
        "events": events,
        "notes": notes,
        "status": status,
        "source": source,
        "annotator": annotator.upper(),
        "roles": list(ALLOWED_ROLES),
        "kinds": list(KINDS),
    }


def save_chunk(
    chunk_id: str,
    annotator: str,
    *,
    events: list[Any],
    notes: str = "",
    status: str = "done",
) -> dict[str, Any]:
    data = get_chunk(chunk_id, annotator)
    text = data["text"]
    cleaned, errors = validate_events_for_save(text, events)
    if errors:
        return {"ok": False, "errors": errors}

    payload = {
        "chunk_id": chunk_id,
        "doc_id": data["doc_id"],
        "title": data["title"],
        "publish_time": data["publish_time"],
        "url": data["url"],
        "stratum": data.get("stratum") or "",
        "text": text,
        "events": cleaned,
        "notes": notes or "",
        "status": status if status in ("done", "skip", "pending") else "done",
    }
    _write_json(chunk_annot_path(annotator, chunk_id), payload)

    meta = load_meta(annotator)
    meta.setdefault("status", {})[chunk_id] = payload["status"]
    save_meta(annotator, meta)
    return {"ok": True, "chunk": get_chunk(chunk_id, annotator)}


def export_gold(annotator: str) -> Path:
    """按 pilot 顺序写出 gold JSONL（一块一行）。"""
    out = DATA / "eval" / f"pilot20_gold_{annotator.upper()}.jsonl"
    lines: list[str] = []
    for row in load_pilot():
        cid = row["chunk_id"]
        path = chunk_annot_path(annotator, cid)
        if path.is_file():
            ann = _read_json(path)
            rec = {
                "chunk_id": cid,
                "doc_id": ann.get("doc_id") or row.get("doc_id"),
                "title": ann.get("title") or row.get("title"),
                "publish_time": ann.get("publish_time") or row.get("publish_time"),
                "url": ann.get("url") or row.get("url"),
                "stratum": ann.get("stratum") or row.get("stratum"),
                "text": ann.get("text") or "",
                "events": ann.get("events") or [],
                "notes": ann.get("notes") or "",
                "status": ann.get("status") or "done",
                "annotator": annotator.upper(),
            }
        else:
            pre = load_prelabel_index().get(cid) or {}
            rec = {
                "chunk_id": cid,
                "doc_id": row.get("doc_id"),
                "title": row.get("title"),
                "publish_time": row.get("publish_time"),
                "url": row.get("url"),
                "stratum": row.get("stratum"),
                "text": pre.get("text") or "",
                "events": pre.get("events") or [],
                "notes": "",
                "status": "prelabel_only",
                "annotator": annotator.upper(),
            }
        lines.append(json.dumps(rec, ensure_ascii=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def fact_key(ev: dict) -> str:
    trigger = str(ev.get("trigger") or "").strip()
    kind = str(ev.get("kind") or "").strip()
    subj = _arg_text(ev, "主体")
    obj = _arg_text(ev, "对象")
    return f"{kind}|{trigger}|{subj}|{obj}"
