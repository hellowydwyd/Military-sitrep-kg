"""把 OpenIE JSONL 写成可查询的三层图（JSON，不依赖 Neo4j）。"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from extract.validate import evidence_sentence
from graph.proximity import next_in_time_edges
from graph.time import detect_tlink, event_meta, inherit_anchor


def _eid(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _actors(ev: dict) -> list[str]:
    names = []
    for arg in ev.get("arguments") or []:
        role = str(arg.get("role") or "")
        text = str(arg.get("text") or "").strip()
        if text and any(k in role for k in ("主体", "发起", "参与")):
            names.append(text)
    return names


def _locations(ev: dict) -> list[str]:
    out = []
    for arg in ev.get("arguments") or []:
        if "地点" in str(arg.get("role") or "") or "位置" in str(arg.get("role") or ""):
            t = str(arg.get("text") or "").strip()
            if t:
                out.append(t)
    return out


def load_openie(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_graph(rows: Iterable[dict]) -> dict[str, Any]:
    documents, chunks, events = [], [], []
    open_rels = []
    tlink_edges: list[dict] = []
    seen_doc, seen_chunk, seen_rel = set(), set(), set()

    for row in rows:
        doc_id = row["doc_id"]
        if doc_id not in seen_doc:
            seen_doc.add(doc_id)
            documents.append(
                {
                    "doc_id": doc_id,
                    "title": row.get("title"),
                    "publish_time": row.get("publish_time"),
                    "url": row.get("url"),
                    "source": row.get("source"),
                }
            )
        for ch in row.get("chunks") or []:
            cid = ch["chunk_id"]
            if cid not in seen_chunk:
                seen_chunk.add(cid)
                chunks.append(
                    {
                        "chunk_id": cid,
                        "doc_id": doc_id,
                        "ord": ch.get("ord", 0),
                        "text": ch.get("text") or "",
                    }
                )
            tris = ch.get("triples") or []
            for tri in tris:
                key = (cid, tri["head"], tri["relation"], tri["tail"])
                if key in seen_rel:
                    continue
                seen_rel.add(key)
                open_rels.append(
                    {
                        "rel_id": _eid(cid, tri["head"], tri["relation"], tri["tail"]),
                        "chunk_id": cid,
                        "doc_id": doc_id,
                        "head": tri["head"],
                        "relation": tri["relation"],
                        "tail": tri["tail"],
                        "t_start": tri.get("t_start") or "",
                        "t_end": tri.get("t_end") or "",
                        "evidence": tri.get("evidence") or "",
                    }
                )
            chunk_evs: list[dict] = []
            prev_abs = None
            chunk_text = ch.get("text") or ""
            for ev in ch.get("events") or []:
                args = ev.get("arguments") or []
                arg_sig = ";".join(f"{a.get('role')}={a.get('text')}" for a in args)
                eid = _eid(cid, ev.get("trigger") or "", arg_sig)
                trigger = ev.get("trigger") or ""
                evidence = ev.get("evidence") or evidence_sentence(chunk_text, trigger)
                anchor = inherit_anchor(
                    prev_abs,
                    chunk_text,
                    evidence,
                    str(ev.get("raw_time") or ""),
                    row.get("publish_time"),
                )
                meta = event_meta(ev, row.get("publish_time"), None, anchor)
                built = {
                    "event_id": eid,
                    "chunk_id": cid,
                    "doc_id": doc_id,
                    "trigger": trigger,
                    "kind": ev.get("kind") or "",
                    "raw_time": ev.get("raw_time") or "",
                    "event_time": meta.get("day") or "",
                    "event_time_end": meta.get("end") or "",
                    "time_precision": meta.get("precision") or "",
                    "time_background": bool(meta.get("background")),
                    "evidence": evidence,
                    "arguments": args,
                    "actors": _actors(ev),
                    "locations": _locations(ev),
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "publish_time": row.get("publish_time"),
                }
                events.append(built)
                chunk_evs.append(built)
                if meta.get("precision") == "day" and len(meta.get("day") or "") == 10:
                    prev_abs = meta.get("day")
            for i, cur in enumerate(chunk_evs):
                prev = chunk_evs[i - 1] if i else None
                order, cue = detect_tlink(cur, prev)
                if not order:
                    continue
                pub_cues = ("日前", "近日", "近几日", "近来", "近期")
                if order == "BEFORE" and cue in pub_cues:
                    target = doc_id
                elif prev is None:
                    continue
                else:
                    target = prev["event_id"]
                tlink_edges.append(
                    {
                        "type": "TLINK",
                        "from": cur["event_id"],
                        "to": target,
                        "order": order,
                        "cue": cue,
                        "scope": "chunk",
                        "chunk_id": cid,
                    }
                )

    edges = list(tlink_edges) + next_in_time_edges(events)
    for ch in chunks:
        edges.append({"type": "IN_DOCUMENT", "from": ch["chunk_id"], "to": ch["doc_id"]})
    for ev in events:
        edges.append({"type": "SOURCED_FROM", "from": ev["event_id"], "to": ev["chunk_id"]})
        for name in ev.get("actors") or []:
            edges.append({"type": "PARTICIPANT", "from": ev["event_id"], "to": name})
        for loc in ev.get("locations") or []:
            edges.append({"type": "LOCATED_AT", "from": ev["event_id"], "to": loc})
    for rel in open_rels:
        edges.append({"type": "OPEN_REL", "from": rel["rel_id"], "to": rel["chunk_id"]})

    return {
        "documents": documents,
        "chunks": chunks,
        "events": events,
        "open_rels": open_rels,
        "edges": edges,
    }


def save_graph(graph: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_graph(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def stats(graph: dict) -> dict:
    by_day = defaultdict(int)
    by_prec = defaultdict(int)
    empty_raw_dated = 0
    jan1_day = 0
    for ev in graph.get("events") or []:
        by_day[ev.get("event_time") or "unknown"] += 1
        prec = ev.get("time_precision") or ""
        by_prec[prec] += 1
        raw = str(ev.get("raw_time") or "").strip()
        has_time_arg = any(
            "时间" in str(a.get("role") or "") and str(a.get("text") or "").strip()
            for a in ev.get("arguments") or []
        )
        if not raw and not has_time_arg and ev.get("event_time"):
            empty_raw_dated += 1
        if (ev.get("event_time") or "")[:10] == "2026-01-01" and prec == "day":
            jan1_day += 1
    n_tlink = sum(1 for e in graph.get("edges") or [] if e.get("type") == "TLINK")
    n_next = sum(1 for e in graph.get("edges") or [] if e.get("type") == "NEXT_IN_TIME")
    return {
        "n_docs": len(graph.get("documents") or []),
        "n_chunks": len(graph.get("chunks") or []),
        "n_events": len(graph.get("events") or []),
        "n_open_rels": len(graph.get("open_rels") or []),
        "n_tlink": n_tlink,
        "n_next_in_time": n_next,
        "empty_raw_dated": empty_raw_dated,
        "jan1_day": jan1_day,
        "by_precision": dict(sorted(by_prec.items())),
        "events_by_day": dict(sorted(by_day.items())),
    }
