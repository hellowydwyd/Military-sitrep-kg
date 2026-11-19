"""按 doc_id 精确重抽指定集合，输出与新槽位体系一致的结果。

与 kg_batch 的区别：只抽给定 doc_id，不按日期范围扫全部，保证数据集构成不变。

    python3 -m extract.reextract --ids-from data/extract/kgv8_huanqiu_mil_2026-01.jsonl \
        --out data/extract/kgv9_huanqiu_mil_2026-01.jsonl

支持断点续跑：输出文件已存在的 doc_id 会跳过。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from extract.kg_extract import NewsDoc, extract_doc

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_ids(path: Path) -> list[str]:
    ids = []
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if line:
            ids.append(json.loads(line)["doc_id"])
    return ids


def load_raw_by_ids(ids: set[str], source: str = "huanqiu_mil") -> dict[str, dict]:
    """只读需要的正文，避免把整个 raw 目录读进内存。"""
    found: dict[str, dict] = {}
    for path in sorted((DATA / "raw" / source).glob("*.jsonl")):
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            did = r.get("doc_id")
            if did in ids and did not in found:
                found[did] = r
        if len(found) == len(ids):
            break
    return found


def to_doc(r: dict, source: str) -> NewsDoc:
    return NewsDoc(
        doc_id=r.get("doc_id") or "",
        title=r.get("title") or "",
        body=r.get("body") or "",
        publish_time=r.get("publish_time") or "",
        source=r.get("source") or source,
        source_name=r.get("source_name") or "",
        url=r.get("url") or "",
        html_hash=r.get("html_hash") or "",
        lang=r.get("lang") or "zh",
        fetched_at=r.get("fetched_at") or "",
        channel=r.get("channel") or "",
        extra=r.get("extra"),
        title_orig=r.get("title_orig") or "",
        body_orig=r.get("body_orig") or "",
        translated=bool(r.get("translated")),
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="按 doc_id 精确重抽")
    ap.add_argument("--ids-from", required=True, help="含 doc_id 的 jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", default="huanqiu_mil")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    out = Path(a.out)
    ids = load_ids(Path(a.ids_from))
    if a.limit:
        ids = ids[: a.limit]

    done: set[str] = set()
    if out.is_file():
        for line in out.open(encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["doc_id"])
                except Exception:
                    pass
    todo = [i for i in ids if i not in done]
    print(f"目标 {len(ids)} 篇，已完成 {len(done)}，待抽 {len(todo)}", flush=True)

    raw = load_raw_by_ids(set(todo), a.source)
    missing = [i for i in todo if i not in raw]
    if missing:
        print(f"!! 正文缺失 {len(missing)} 篇: {missing[:3]}", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ok = err = 0
    with out.open("a", encoding="utf-8") as g:
        for n, did in enumerate(todo, 1):
            r = raw.get(did)
            if not r:
                continue
            doc = to_doc(r, a.source)
            try:
                row = extract_doc(doc)
            except Exception:
                err += 1
                print(f"[{n}/{len(todo)}] {doc.title[:26]} 异常", flush=True)
                traceback.print_exc()
                continue
            # 结果里补回正文，数据集构建需要
            row["body"] = doc.body
            row["title"] = doc.title
            row["source"] = doc.source
            row["source_name"] = doc.source_name
            row["publish_time"] = doc.publish_time
            row["url"] = doc.url
            row["lang"] = doc.lang
            g.write(json.dumps(row, ensure_ascii=False) + "\n")
            g.flush()
            ok += 1
            el = time.time() - t0
            eta = el / n * (len(todo) - n) / 60
            ne = len(row.get("events") or [])
            na = sum(1 for x in row.get("relations") or []
                     if x.get("head") in {e["text"] for e in row.get("events") or []})
            print(f"[{n}/{len(todo)}] {doc.title[:24]}  事件{ne} 论元{na}  剩 {eta:.0f} 分钟",
                  flush=True)

    print(f"\n完成：成功 {ok}，异常 {err}，用时 {(time.time()-t0)/60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
