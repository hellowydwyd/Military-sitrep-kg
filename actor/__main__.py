"""python -m actor harvest | link | slice"""

from __future__ import annotations

import argparse
import json

from actor.config import DEFAULT_FROM, DEFAULT_TO
from actor.harvest import harvest, harvest_stats, load_mentions, write_mentions
from actor.link import link, write_link
from actor.slice import build_slice, write_slice

MONTHS = [
    ("2026-04-01", "2026-04-30"),
    ("2026-05-01", "2026-05-31"),
    ("2026-06-01", "2026-06-30"),
    ("2026-07-01", "2026-07-31"),
    ("2026-08-01", "2026-08-31"),
    ("2026-09-01", "2026-09-30"),
]
OVERLAP = [
    ("2026-10-02", "2026-10-02"),
    ("2026-09-20", "2026-10-02"),
]
WINDOWS = [*MONTHS, (DEFAULT_FROM, DEFAULT_TO), *OVERLAP]


def cmd_harvest(paths: list[str] | None, *, scan: bool) -> int:
    from pathlib import Path

    from actor.harvest import _merge_rows, default_extract_paths, harvest_extract

    srcs = [Path(p) for p in paths] if paths else default_extract_paths()
    rows: list[dict] = []
    for src in srcs:
        rows.extend(harvest_extract(src))
    if scan:
        from actor.scan import harvest_scan

        rows = _merge_rows(rows, harvest_scan())
    for i, row in enumerate(rows):
        row["mention_id"] = f"m{i:06d}"
    dest = write_mentions(rows)
    stats = harvest_stats(rows)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"提及 {dest}")
    return 0


def cmd_link(*, use_llm: bool) -> int:
    packed = link(load_mentions(), use_llm=use_llm)
    dest = write_link(packed)
    print(
        json.dumps(
            {
                "n_entities": packed["n_entities"],
                "n_llm_typed": packed["n_llm_typed"],
                "n_untyped": packed["n_untyped"],
                "n_dropped": packed.get("n_dropped", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"实体 {dest}")
    return 0


def cmd_slice(*, day_from: str, day_to: str, use_llm: bool) -> int:
    out = build_slice(ta=day_from, tb=day_to, use_llm=use_llm)
    jp, md = write_slice(out)
    print(json.dumps(out["metrics"], ensure_ascii=False, indent=2))
    print(f"切片 {jp}")
    print(f"报告 {md}")
    return 0


def cmd_build(*, scan: bool, use_llm: bool) -> int:
    cmd_harvest(None, scan=scan)
    cmd_link(use_llm=use_llm)
    for a, b in WINDOWS:
        cmd_slice(day_from=a, day_to=b, use_llm=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="多主体：提及、对齐、查询窗切片")
    sub = p.add_subparsers(dest="cmd", required=True)
    hp = sub.add_parser("harvest", help="从抽取 JSONL 收提及，并扫描无抽取月份的稿面")
    hp.add_argument(
        "--extract",
        action="append",
        default=None,
        help="抽取 JSONL；可重复。默认同时读环球网与 RSS",
    )
    hp.add_argument("--no-scan", action="store_true", help="只用地图抽取，不扫原稿")
    lp = sub.add_parser("link", help="分类 + 别名合并")
    lp.add_argument("--llm", action="store_true", help="剩余高频名问 LLM 分类")
    sp = sub.add_parser("slice", help="查询窗排行与共现图")
    sp.add_argument("--from", dest="day_from", default=DEFAULT_FROM)
    sp.add_argument("--to", dest="day_to", default=DEFAULT_TO)
    sp.add_argument("--llm", action="store_true")
    bp = sub.add_parser("build", help="harvest + link + 按月切片 + 全程")
    bp.add_argument("--no-scan", action="store_true")
    bp.add_argument("--llm", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "harvest":
        return cmd_harvest(args.extract, scan=not args.no_scan)
    if args.cmd == "link":
        return cmd_link(use_llm=args.llm)
    if args.cmd == "slice":
        return cmd_slice(day_from=args.day_from, day_to=args.day_to, use_llm=args.llm)
    if args.cmd == "build":
        return cmd_build(scan=not args.no_scan, use_llm=args.llm)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
