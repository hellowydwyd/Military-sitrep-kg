"""从 sitrep.json 统计 Actor / Location 频次。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_graph(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def mention_counts(graph: dict, kind: str = "actors") -> Counter:
    c: Counter = Counter()
    key = "actors" if kind == "actors" else "locations"
    for ev in graph.get("events") or []:
        for name in ev.get(key) or []:
            name = str(name).strip()
            if name:
                c[name] += 1
    return c


def top_names(counts: Counter, *, n: int = 80, min_freq: int = 1) -> list[str]:
    return [k for k, v in counts.most_common(n) if v >= min_freq]
