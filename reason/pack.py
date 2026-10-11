"""把按日证据包压成给 LLM 的短 JSON。不含整图。"""

from __future__ import annotations

from typing import Any

from extract.validate import evidence_sentence


def _clip(text: str, n: int = 200) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _item_evidence(ev: dict, n: int) -> str:
    evd = (ev.get("evidence") or "").strip()
    if evd:
        return _clip(evd, n)
    return _clip(evidence_sentence(ev.get("chunk_text") or "", ev.get("trigger") or ""), n)


def serialize_pack(pack: dict[str, Any], *, evidence_chars: int = 200) -> dict[str, Any]:
    items = []
    for g in pack.get("groups") or []:
        doc = g.get("document") or {}
        for ev in g.get("events") or []:
            eid = str(ev.get("event_id") or "")
            cid = str(ev.get("chunk_id") or "")
            items.append(
                {
                    "cite": f"{eid[:8]}|{cid}",
                    "event_id": eid,
                    "chunk_id": cid,
                    "event_time": ev.get("event_time") or "",
                    "raw_time": ev.get("raw_time") or "",
                    "time_precision": ev.get("time_precision") or "",
                    "trigger": ev.get("trigger") or "",
                    "actors": ev.get("actors") or [],
                    "locations": ev.get("locations") or [],
                    "arguments": ev.get("arguments") or [],
                    "title": doc.get("title") or ev.get("title") or "",
                    "url": doc.get("url") or ev.get("url") or "",
                    "source": doc.get("source") or "huanqiu_mil",
                    "evidence": _item_evidence(ev, evidence_chars),
                }
            )
    items.sort(key=lambda x: (x.get("event_time") or "", x.get("title") or "", x.get("cite") or ""))
    pub_skip = [
        {"title": d.get("title") or "", "publish_time": d.get("publish_time") or ""}
        for d in pack.get("published_without_event") or []
    ]
    return {
        "day": pack.get("day"),
        "n_events": len(items),
        "source_note": "single_source：仅环球网报道主张，不得写成已确认或已核实。",
        "published_without_event": pub_skip,
        "items": items,
    }


def cite_index(serialized: dict[str, Any]) -> dict[str, dict]:
    """cite 短键 → 条目；同时收录完整 event_id 前缀。"""
    out: dict[str, dict] = {}
    for it in serialized.get("items") or []:
        cite = str(it.get("cite") or "")
        eid = str(it.get("event_id") or "")
        cid = str(it.get("chunk_id") or "")
        if cite:
            out[cite] = it
        if eid and cid:
            out[f"{eid}|{cid}"] = it
    return out
