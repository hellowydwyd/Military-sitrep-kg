"""A/B fact 级粗一致率。"""

from __future__ import annotations

import json
from typing import Any

from annotate.store import (
    ANNOTATORS,
    chunk_annot_path,
    fact_key,
    load_pilot,
)


def _load_events(annotator: str, chunk_id: str) -> list[dict] | None:
    p = chunk_annot_path(annotator, chunk_id)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("events") or [])


def compute_iaa() -> dict[str, Any]:
    pilot = load_pilot()
    both = 0
    exact = 0
    jaccards: list[float] = []
    disagreements: list[dict[str, Any]] = []
    only_a = 0
    only_b = 0

    for row in pilot:
        cid = row["chunk_id"]
        ea = _load_events("A", cid)
        eb = _load_events("B", cid)
        if ea is None and eb is None:
            continue
        if ea is None:
            only_b += 1
            continue
        if eb is None:
            only_a += 1
            continue
        both += 1
        sa = {fact_key(e) for e in ea}
        sb = {fact_key(e) for e in eb}
        inter = sa & sb
        union = sa | sb
        jac = (len(inter) / len(union)) if union else 1.0
        jaccards.append(jac)
        if sa == sb:
            exact += 1
        else:
            disagreements.append(
                {
                    "chunk_id": cid,
                    "title": row.get("title") or "",
                    "jaccard": round(jac, 4),
                    "only_a": sorted(sa - sb),
                    "only_b": sorted(sb - sa),
                    "shared": sorted(inter),
                    "n_a": len(ea),
                    "n_b": len(eb),
                }
            )

    avg_j = sum(jaccards) / len(jaccards) if jaccards else None
    return {
        "annotators": list(ANNOTATORS),
        "n_pilot": len(pilot),
        "n_both_annotated": both,
        "n_only_a": only_a,
        "n_only_b": only_b,
        "chunk_exact_match": exact,
        "chunk_exact_rate": (exact / both) if both else None,
        "avg_fact_jaccard": round(avg_j, 4) if avg_j is not None else None,
        "disagreements": disagreements,
    }
