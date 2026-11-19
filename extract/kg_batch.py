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
                title_orig=r.get("title_orig") or "",
                body_orig=r.get("body_orig") or "",
                translated=bool(r.get("translated")),
            ))
    docs.sort(key=lambda d: d.publish_time or "")
    return docs


def sample_docs(
    docs: list[NewsDoc],
    limit: int,
    body_min: int = 0,
    body_max: int = 0,
    per_site: int = 0,
    seed: int = 7,
) -> list[NewsDoc]:
    """按长度与站点配额采样，保持时间顺序。

    长稿成本高、结构关系稀，短稿信息量少，所以优先取中间长度。
    per_site 给每个来源站点设上限，保证来源多样性。
    """
    import random

    pool = []
    for d in docs:
        n = len(d.body or "")
        if body_min and n < body_min:
            continue
        if body_max and n > body_max:
            continue
        pool.append(d)
    if not pool:
        pool = list(docs)

    # 中间长度优先：按与中位数的距离排序，同距离随机打散
    lens = sorted(len(d.body or "") for d in pool)
    mid = lens[len(lens) // 2]
    rnd = random.Random(seed)
    keyed = [(abs(len(d.body or "") - mid), rnd.random(), d) for d in pool]
    keyed.sort(key=lambda x: (x[0], x[1]))

    out: list[NewsDoc] = []
    per_site_n: dict[str, int] = {}
    for _, _, d in keyed:
        if limit and len(out) >= limit:
            break
        site = d.source_name or (d.extra or {}).get("domain") or ""
        if per_site:
            if per_site_n.get(site, 0) >= per_site:
                continue
        out.append(d)
        per_site_n[site] = per_site_n.get(site, 0) + 1

    out.sort(key=lambda d: d.publish_time or "")
    return out


def load_docs_file(path: Path) -> list[NewsDoc]:
    """从单个 jsonl 读稿件。译文文件与原始稿格式兼容。"""
    docs: list[NewsDoc] = []
    seen: set[str] = set()
    if not path.is_file():
        return docs
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
            source=r.get("source") or "translated",
            source_name=r.get("source_name") or "",
            url=url,
            html_hash=r.get("html_hash") or "",
            lang=r.get("lang") or "zh",
            fetched_at=r.get("fetched_at") or "",
            channel=r.get("channel") or "",
            extra=r.get("extra"),
            title_orig=r.get("title_orig") or "",
            body_orig=r.get("body_orig") or "",
            translated=bool(r.get("translated")),
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
    p.add_argument("--body-min", type=int, default=0, help="只取正文不少于该字数的稿")
    p.add_argument("--body-max", type=int, default=0, help="只取正文不超过该字数的稿")
    p.add_argument("--per-site", type=int, default=0, help="每个来源站点最多取多少篇")
    p.add_argument("--seed", type=int, default=7, help="采样随机种子")
    p.add_argument("--src", default="", help="直接指定输入 jsonl（如译文文件），忽略 --source")
    p.add_argument("--out", default="", help="直接指定输出路径")
    a = p.parse_args(argv)

    if a.src:
        docs = load_docs_file(Path(a.src))
        print(f"输入 {len(docs)} 篇（{a.src}）", flush=True)
        source_name = Path(a.src).stem
    else:
        docs = load_docs(a.source, a.date_from, a.date_to)
        print(f"范围内 {len(docs)} 篇", flush=True)
        source_name = a.source
    if a.limit or a.body_min or a.body_max or a.per_site:
        docs = sample_docs(
            docs, a.limit, a.body_min, a.body_max, a.per_site, a.seed,
        )
        print(
            f"采样后 {len(docs)} 篇"
            f"（长度 {a.body_min or 0}–{a.body_max or '∞'}，站点上限 {a.per_site or '∞'}）",
            flush=True,
        )
    path = Path(a.out) if a.out else out_path(source_name, a.date_from, a.date_to, a.tag)
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
