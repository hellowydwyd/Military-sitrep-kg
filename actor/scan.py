"""无抽取月份：在标题和正文里找已有词典名。最长匹配，不新造百科。"""

from __future__ import annotations

from pathlib import Path

from extract.io import load_docs
from actor.aliases import SEED_GROUPS
from actor.clean import canonicalize_surface
from actor.config import DATA, KEEP_ROLES
from actor.types import COUNTRIES, COUNTRY_ALIAS, ORG_SEED, PERSON_SEED, rule_type
from ingest.datatypes import classify


def _lexicon() -> list[str]:
    bag = set()
    bag.update(COUNTRIES)
    bag.update(COUNTRY_ALIAS)
    bag.update(ORG_SEED)
    bag.update(PERSON_SEED)
    for g in SEED_GROUPS:
        bag.update(g)
    return sorted((x for x in bag if x and len(x) >= 2), key=len, reverse=True)


def _hits(hay: str, names: list[str]) -> list[str]:
    if not hay:
        return []
    covered = [False] * len(hay)
    found: list[str] = []
    seen: set[str] = set()
    for name in names:
        start = 0
        while True:
            i = hay.find(name, start)
            if i < 0:
                break
            j = i + len(name)
            if not any(covered[i:j]):
                for k in range(i, j):
                    covered[k] = True
                if name not in seen:
                    found.append(name)
                    seen.add(name)
            start = i + 1
    return found


def harvest_scan(
    *,
    date_from: str = "2026-04-01",
    date_to: str = "2026-09-30",
    raw_dir: Path | None = None,
) -> list[dict]:
    names = _lexicon()
    docs = load_docs(
        raw_dir or (DATA / "raw"),
        date_from=date_from,
        date_to=date_to,
        source="huanqiu_mil",
    )
    rows: list[dict] = []
    mid = 0
    for doc in docs:
        hay = f"{doc.title}\n{doc.body or ''}"
        day = (doc.publish_time or "")[:10]
        for surface in _hits(hay, names):
            text = canonicalize_surface(surface, "主体")
            if not text:
                continue
            typ = rule_type(text, "主体") or ""
            rows.append(
                {
                    "mention_id": f"g{mid:06d}",
                    "doc_id": doc.doc_id,
                    "day": day,
                    "url": doc.url,
                    "title": doc.title,
                    "source": doc.source,
                    "source_name": doc.source_name or "环球网军事",
                    "data_type": classify(doc.source, doc.extra or {}, doc.channel),
                    "chunk_i": 0,
                    "event_i": 0,
                    "trigger": "",
                    "kind": "",
                    "role": "提及",
                    "surface": text,
                    "type_rule": typ,
                    "evidence": (doc.title or "")[:180],
                    "via": "gazetteer",
                }
            )
            mid += 1
    return rows
