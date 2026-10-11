"""JSONL 落盘 + SQLite 去重。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Optional

from ingest.envelope import NewsDoc

CST = timezone(timedelta(hours=8))


class DocStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "seen.sqlite"
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                url TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                html_hash TEXT,
                source TEXT,
                fetched_at TEXT,
                status TEXT,
                title TEXT
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def lookup(self, url: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT url, doc_id, html_hash, source, fetched_at, status FROM seen WHERE url = ?",
            (url,),
        )
        row = cur.fetchone()
        if not row:
            return None
        keys = ["url", "doc_id", "html_hash", "source", "fetched_at", "status"]
        return dict(zip(keys, row))

    def should_skip(self, url: str, html_hash: str) -> bool:
        row = self.lookup(url)
        return bool(row and row.get("html_hash") == html_hash and row.get("status") == "ok")

    def mark(
        self,
        url: str,
        *,
        doc_id: str,
        html_hash: str = "",
        source: str = "",
        status: str = "ok",
        title: str = "",
    ) -> None:
        now = datetime.now(CST).replace(microsecond=0).isoformat()
        self._conn.execute(
            """
            INSERT INTO seen (url, doc_id, html_hash, source, fetched_at, status, title)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                doc_id=excluded.doc_id,
                html_hash=excluded.html_hash,
                source=excluded.source,
                fetched_at=excluded.fetched_at,
                status=excluded.status,
                title=excluded.title
            """,
            (url, doc_id, html_hash, source, now, status, title),
        )
        self._conn.commit()

    def append(self, doc: NewsDoc) -> Path:
        day = (doc.publish_time or doc.fetched_at or "")[:10] or datetime.now(CST).strftime("%Y-%m-%d")
        path = self.raw_dir / doc.source / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(doc.to_json() + "\n")
        self.mark(
            doc.url,
            doc_id=doc.doc_id,
            html_hash=doc.html_hash,
            source=doc.source,
            status="ok",
            title=doc.title,
        )
        return path

    def write_run_snapshot(self, docs: Iterable[NewsDoc], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for doc in docs:
                f.write(doc.to_json() + "\n")
        return path

    def iter_jsonl(self) -> Iterable[dict]:
        for path in sorted(self.raw_dir.glob("*/*.jsonl")):
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        yield json.loads(line)
