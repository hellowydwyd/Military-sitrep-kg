"""跨文档事件邻近边 NEXT_IN_TIME。共享实体 × 时间衰减，不是因果。"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

CAL_PREC = ("day", "week", "interval")
TOP_K = 6
ALPHA = 0.1


def _anchor_day(ev: dict) -> date | None:
    if ev.get("time_background"):
        return None
    if (ev.get("time_precision") or "") not in CAL_PREC:
        return None
    raw = (ev.get("event_time") or "")[:10]
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _entities(ev: dict) -> frozenset[str]:
    """有对齐层用 canonical id，否则用原文。"""
    aligned = ev.get("actors_aligned") or []
    if aligned:
        names = [
            str(a.get("id") or a.get("canonical") or "").strip()
            for a in aligned
        ]
    else:
        names = [str(x).strip() for x in (ev.get("actors") or []) if str(x).strip()]
    loc_aligned = ev.get("locations_aligned") or []
    if loc_aligned:
        names += [
            str(a.get("id") or a.get("canonical") or "").strip()
            for a in loc_aligned
        ]
    else:
        names += [str(x).strip() for x in (ev.get("locations") or []) if str(x).strip()]
    return frozenset(n for n in names if n)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return len(inter) / len(a | b)


def _chunk_ids(ev: dict) -> set[str]:
    extra = ev.get("_chunk_ids") or []
    out = {str(x) for x in extra if x}
    cid = ev.get("chunk_id") or ev.get("_chunk_id") or ""
    if cid:
        out.add(str(cid))
    return out


def _doc_ids(ev: dict) -> set[str]:
    extra = ev.get("_doc_ids") or []
    out = {str(x) for x in extra if x}
    did = ev.get("doc_id") or ev.get("_doc_id") or ""
    if did:
        out.add(str(did))
    return out


def next_in_time_edges(
    events: list[dict],
    *,
    k: int = TOP_K,
    alpha: float = ALPHA,
    max_delta_days: int | None = None,
    skip_pairs: set[tuple[str, str]] | None = None,
    skip_same_chunk: bool = True,
    same_doc_fallback: float = 0.0,
) -> list[dict]:
    """共享实体 × 时间衰减。默认同块不连（交给 TLINK）。每点最多 k 条有向边。不是因果。"""
    dated: list[tuple[dict, date, frozenset[str]]] = []
    for ev in events:
        day = _anchor_day(ev)
        ents = _entities(ev)
        if day is None:
            continue
        if not ents and same_doc_fallback <= 0:
            continue
        dated.append((ev, day, ents))

    banned = skip_pairs or set()
    cands: dict[str, list[tuple[float, str, list[str], int]]] = defaultdict(list)
    for i, (ei, ti, si) in enumerate(dated):
        for ej, tj, sj in dated[i + 1 :]:
            a, b = ei["event_id"], ej["event_id"]
            if (a, b) in banned or (b, a) in banned:
                continue
            chunks_i, chunks_j = _chunk_ids(ei), _chunk_ids(ej)
            if skip_same_chunk and chunks_i and chunks_j and chunks_i & chunks_j:
                continue
            sim = _jaccard(si, sj)
            shared = sorted(si & sj)
            if sim <= 0 and same_doc_fallback > 0:
                docs_i, docs_j = _doc_ids(ei), _doc_ids(ej)
                if docs_i and docs_j and docs_i & docs_j:
                    sim = same_doc_fallback
                    shared = [f"doc:{sorted(docs_i & docs_j)[0][:12]}"]
            if sim <= 0:
                continue
            dt = abs((ti - tj).days)
            if max_delta_days is not None and dt > max_delta_days:
                continue
            weight = sim * math.exp(-alpha * dt)
            if weight < 1e-6:
                continue
            cands[a].append((weight, b, shared, dt))
            cands[b].append((weight, a, shared, dt))

    edges = []
    for src, lst in cands.items():
        lst.sort(key=lambda x: -x[0])
        for weight, dst, shared, dt in lst[:k]:
            edges.append(
                {
                    "type": "NEXT_IN_TIME",
                    "from": src,
                    "to": dst,
                    "score": round(weight, 4),
                    "weight": round(weight, 4),
                    "delta_days": dt,
                    "shared": shared[:8],
                    "scope": "graph",
                }
            )
    return edges
