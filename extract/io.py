"""从 data/raw 读取去重后的 NewsDoc。"""

from __future__ import annotations

import json
from pathlib import Path

from ingest.envelope import NewsDoc, build_doc


def load_docs(
    raw_dir: Path,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
    include_undated: bool = False,
) -> list[NewsDoc]:
    by_url: dict[str, dict] = {}
    for path in sorted(Path(raw_dir).glob("*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            url = raw.get("url") or ""
            if not url:
                continue
            if source and raw.get("source") != source:
                continue
            day = (raw.get("publish_time") or "")[:10]
            if not day:
                if not include_undated:
                    continue
            else:
                if date_from and day < date_from:
                    continue
                if date_to and day > date_to:
                    continue
            by_url[url] = raw
    docs = []
    for raw in by_url.values():
        doc = build_doc(
            url=raw.get("url", ""),
            title=raw.get("title", ""),
            body=raw.get("body", ""),
            publish_time=raw.get("publish_time") or None,
            source=raw.get("source", ""),
            extra=raw.get("extra"),
            lang=raw.get("lang"),
        )
        if raw.get("source_name"):
            doc.source_name = raw["source_name"]
        docs.append(doc)
    docs.sort(key=lambda d: d.publish_time or "")
    return docs
