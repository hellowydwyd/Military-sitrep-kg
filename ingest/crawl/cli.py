"""命令行：smoke / once / backfill / report。

环球网军事为过渡源；合作方 RSS 用 --source rss（英语白名单，缺全文则补爬 HTML）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ingest.crawl import huanqiu_mil, rss_api
from ingest.crawl.base import DEFAULT_DELAY, PoliteSession
from ingest.crawl.fulltext import Fetcher
from ingest.crawl.report import write_report
from ingest.envelope import MIN_BODY_CHARS, SOURCE_RSS, NewsDoc, make_doc_id, should_skip_url
from ingest.store import DocStore

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CST = timezone(timedelta(hours=8))
SOURCES = {
    "huanqiu": ("huanqiu_mil", huanqiu_mil),
}


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if len(text) == 10:
        dt = datetime.strptime(text, "%Y-%m-%d")
    else:
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt


def _run_source(
    key: str,
    module,
    session: PoliteSession,
    store: DocStore,
    *,
    limit: int,
    force: bool,
    since: datetime | None,
    until: datetime | None = None,
) -> tuple[list[NewsDoc], dict]:
    stats = {
        "listed": 0,
        "written": 0,
        "skipped_seen": 0,
        "skipped_quality": 0,
        "errors": 0,
    }
    kwargs: dict = {"max_items": limit}
    if since is not None:
        kwargs["since"] = since
    if until is not None:
        kwargs["until"] = until
    items = module.fetch_list(session, **kwargs)
    stats["listed"] = len(items)
    docs: list[NewsDoc] = []
    for item in items:
        if should_skip_url(item.url):
            stats["skipped_quality"] += 1
            continue
        if not force:
            row = store.lookup(item.url)
            if row and row.get("status") == "ok":
                stats["skipped_seen"] += 1
                continue
        try:
            doc = module.fetch_article(session, item)
        except Exception as exc:
            stats["errors"] += 1
            store.mark(
                item.url,
                doc_id=make_doc_id(item.url),
                source=SOURCES[key][0],
                status="error",
                title=getattr(item, "title", "") or str(exc)[:80],
            )
            print(f"  [error] {item.url} ({exc})", file=sys.stderr)
            continue
        miss = doc.missing_fields()
        if "body_too_short" in miss or not doc.body:
            stats["skipped_quality"] += 1
            store.mark(
                doc.url,
                doc_id=doc.doc_id,
                html_hash=doc.html_hash,
                source=doc.source,
                status="skip_short",
                title=doc.title,
            )
            print(f"  [skip-short {doc.body_chars}<{MIN_BODY_CHARS}] {doc.title[:40]}")
            continue
        store.append(doc)
        docs.append(doc)
        stats["written"] += 1
        print(f"  [ok] {doc.publish_time[:16] if doc.publish_time else '?'}  {doc.title[:48]}")
    return docs, stats


def cmd_crawl(args: argparse.Namespace) -> int:
    keys = list(SOURCES) if args.source == "all" else [args.source]
    store = DocStore(DATA)
    all_docs: list[NewsDoc] = []
    print(f"输出目录: {DATA}")
    since = _parse_since(getattr(args, "since", None))
    until = _parse_since(getattr(args, "until", None))
    if since:
        print(f"起始日期: {since.isoformat()}（含）")
    if until:
        print(f"截止日期: {until.isoformat()}（含）")
    try:
        for key in keys:
            if key == "rss":
                print(f"\n== {SOURCE_RSS} variant={args.variant} lang={args.lang} ==")
                fetcher = Fetcher(delay=args.delay, timeout=20.0)
                docs, stats = rss_api.run(
                    store,
                    fetcher,
                    limit=args.limit,
                    force=args.force,
                    since=since,
                    until=until,
                    variant=args.variant,
                    lang=args.lang,
                    per_day=int(getattr(args, "per_day", 0) or 0),
                )
                all_docs.extend(docs)
                print(
                    f"  列表 {stats['listed']} | 写入 {stats['written']} | "
                    f"已见跳过 {stats['skipped_seen']} | 质量跳过 {stats['skipped_quality']} | "
                    f"语言跳过 {stats['skipped_lang']} | 失败 {stats['errors']}"
                )
                continue
            session = PoliteSession(delay=args.delay)
            source_id, module = SOURCES[key]
            print(f"\n== {source_id} ==")
            docs, stats = _run_source(
                key,
                module,
                session,
                store,
                limit=args.limit,
                force=args.force,
                since=since,
                until=until,
            )
            all_docs.extend(docs)
            print(
                f"  列表 {stats['listed']} | 写入 {stats['written']} | "
                f"已见跳过 {stats['skipped_seen']} | 质量跳过 {stats['skipped_quality']} | "
                f"失败 {stats['errors']}"
            )
    finally:
        store.close()

    snap = DATA / "reports" / "latest_run.jsonl"
    if args.cmd == "smoke" or all_docs:
        snap.parent.mkdir(parents=True, exist_ok=True)
        with snap.open("w", encoding="utf-8") as f:
            for doc in all_docs:
                f.write(doc.to_json() + "\n")
        print(f"\n本轮 JSONL: {snap}")
    report_name = "crawl_smoke.md" if args.cmd == "smoke" else f"crawl_{args.cmd}.md"
    report_path = DATA / "reports" / report_name
    if args.cmd == "smoke" or all_docs:
        write_report(
            all_docs,
            report_path,
            notes=(
                [
                    "source=rss_api；默认不按军事分组过滤。语言按 --lang，拦爬源不入库",
                    "RSS 有全文用 contentText，否则补爬 HTML；不覆盖环球网",
                ]
                if args.source == "rss"
                else None
            ),
        )
        print(f"验收报告:   {report_path}")
    print(f"合计写入 {len(all_docs)} 篇")
    ok = len(all_docs) >= args.min_docs
    if not ok:
        print(f"未达到最少篇数 {args.min_docs}", file=sys.stderr)
        return 1
    return 0


def cmd_report(_: argparse.Namespace) -> int:
    store = DocStore(DATA)
    from ingest.envelope import build_doc

    by_url: dict[str, dict] = {}
    for raw in store.iter_jsonl():
        if raw.get("url"):
            by_url[raw["url"]] = raw
    store.close()
    docs = []
    for raw in by_url.values():
        docs.append(
            build_doc(
                url=raw.get("url", ""),
                title=raw.get("title", ""),
                body=raw.get("body", ""),
                publish_time=raw.get("publish_time") or None,
                source=raw.get("source", ""),
                extra=raw.get("extra"),
            )
        )
    docs.sort(key=lambda d: d.publish_time or "", reverse=True)
    path = write_report(docs, DATA / "reports" / "crawl_all.md")
    print(f"全量报告: {path} （{len(docs)} 篇）")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="军事新闻采集（环球网过渡爬虫 / 合作方 RSS）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--source", choices=["all", "huanqiu", "rss"], default="all")
        sp.add_argument("--limit", type=int, default=8, help="最多抓正文篇数")
        sp.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="同主机请求间隔秒")
        sp.add_argument("--force", action="store_true", help="忽略 seen，强制重抓")
        sp.add_argument("--since", default=None, help="回溯起始日（含），如 2026-07-01")
        sp.add_argument("--until", default=None, help="截止日期（含），RSS 源可用")
        sp.add_argument(
            "--variant",
            default="all",
            help="RSS 分组。默认 all，不按军事或其他 variant 过滤；也可指定 military、intel、tech 等",
        )
        sp.add_argument(
            "--lang",
            default="en",
            help="RSS 语言：en 只收英语；all 不按语言过滤",
        )
        sp.add_argument("--min-docs", type=int, default=1)
        sp.add_argument(
            "--per-day",
            type=int,
            default=0,
            help="RSS：每个见报日最多入库篇数（0 不截）。优先保留已有全文的稿",
        )

    sm = sub.add_parser("smoke", help="验收：少量稿件并写报告")
    add_common(sm)
    sm.set_defaults(limit=8, min_docs=5, delay=1.6)

    once = sub.add_parser("once", help="增量：拉最新若干篇")
    add_common(once)
    once.set_defaults(limit=12, min_docs=1)

    bf = sub.add_parser("backfill", help="回溯翻页（可用 --since 截到某日）")
    add_common(bf)
    bf.set_defaults(limit=2500, min_docs=1)

    rp = sub.add_parser("report", help="根据已落盘 JSONL 重出报告")
    rp.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    if args.cmd == "report":
        return cmd_report(args)
    return cmd_crawl(args)


if __name__ == "__main__":
    raise SystemExit(main())
