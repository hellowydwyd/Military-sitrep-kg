"""批量抽取：事件中心方案。支持断点续跑。

    python3 -m extract.kg_batch --source huanqiu_mil --from 2026-10-01 --to 2026-10-31
    python3 -m extract.kg_batch --source rss_api --from 2026-11-01 --to 2026-11-02 --limit 50
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from extract.kg_extract import extract_doc
from ingest.envelope import NewsDoc

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_docs(source: str, date_from: str, date_to: str) -> list[NewsDoc]:
    """读取 raw 目录下的日文件，跳过损坏行。"""
    docs: list[NewsDoc] = []
    seen: set[str] = set()
    for path in sorted((DATA / "raw" / source).glob("*.jsonl")):
        day = path.stem
        if day < date_from or day > date_to:
            continue
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = r.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            docs.append(NewsDoc(
                doc_id=r.get("doc_id") or "",
                title=r.get("title") or "",
                body=r.get("body") or "",
                publish_time=r.get("publish_time") or "",
                source=r.get("source") or source,
                source_name=r.get("source_name") or "",
                url=url,
                html_hash=r.get("html_hash") or "",
                lang=r.get("lang") or "zh",
                fetched_at=r.get("fetched_at") or "",
                channel=r.get("channel") or "",
                extra=r.get("extra"),
            ))
    docs.sort(key=lambda d: d.publish_time or "")
    return docs


def out_path(source: str, date_from: str, date_to: str, tag: str) -> Path:
    return DATA / "extract" / f"{tag}_{source}_{date_from[:7]}.jsonl"


def done_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.is_file():
        return ids
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("doc_id") and not row.get("errors"):
            ids.add(row["doc_id"])
    return ids


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="事件中心批量抽取")
    p.add_argument("--source", default="huanqiu_mil")
    p.add_argument("--from", dest="date_from", default="2026-10-01")
    p.add_argument("--to", dest="date_to", default="2026-10-31")
    p.add_argument("--limit", type=int, default=0, help="0 表示不截断")
    p.add_argument("--tag", default="kg", help="输出文件名前缀")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--sleep", type=float, default=0.2)
    p.add_argument("--max-fail", type=int, default=3, help="连续失败多少篇后中止")
    a = p.parse_args(argv)

    docs = load_docs(a.source, a.date_from, a.date_to)
    if a.limit:
        docs = docs[: a.limit]
    path = out_path(a.source, a.date_from, a.date_to, a.tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    skip = done_ids(path) if a.resume else set()
    todo = [d for d in docs if d.doc_id not in skip]
    print(f"范围内 {len(docs)} 篇 | 已完成 {len(skip)} | 待抽 {len(todo)} | 输出 {path}", flush=True)

    mode = "a" if a.resume and path.is_file() else "w"
    ok = err = 0
    consecutive_err = 0
    t0 = time.time()
    with path.open(mode, encoding="utf-8") as f:
        for i, doc in enumerate(todo, 1):
            try:
                row = extract_doc(doc)
                if row.get("errors"):
                    err += 1
                    consecutive_err += 1
                else:
                    ok += 1
                    consecutive_err = 0
                print(
                    f"[{i}/{len(todo)}] {doc.publish_time[:10]} {doc.title[:30]} "
                    f"E{len(row['entities'])} R{len(row['relations'])} V{len(row['events'])}",
                    flush=True,
                )
            except Exception as exc:
                err += 1
                consecutive_err += 1
                row = {
                    "doc_id": doc.doc_id,
                    "source": doc.source,
                    "title": doc.title,
                    "publish_time": doc.publish_time,
                    "url": doc.url,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "entities": [],
                    "relations": [],
                    "events": [],
                    "persons": [],
                }
                print(f"[{i}/{len(todo)}] ERROR {doc.title[:28]} {exc}", flush=True)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            # 连续失败说明接口或账户有问题，立即中止，避免整批产生空数据
            if consecutive_err >= a.max_fail:
                print(
                    f"\n连续失败 {consecutive_err} 篇，疑似接口或余额问题，已中止。"
                    f"最后错误：{(row.get('errors') or [''])[0][:200]}",
                    file=sys.stderr, flush=True,
                )
                break
            if a.sleep:
                time.sleep(a.sleep)
            if i % 20 == 0:
                rate = i / max(1e-9, time.time() - t0)
                left = (len(todo) - i) / max(1e-9, rate)
                print(f"  -- {i}/{len(todo)} {rate*60:.1f} 篇/分 约剩 {left/60:.0f} 分", flush=True)

    print(json.dumps({
        "docs": len(docs), "ok": ok, "error": err,
        "out": str(path), "elapsed_s": round(time.time() - t0, 1),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
