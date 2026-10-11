"""抽样：对 RSS 无全文的 5 个高产源，顺着 url 抓原文并统计成功率。

不入库、不绕过验证码/登录墙。失败保留摘要口径。

    python -m ingest.crawl.rss_fulltext_probe
    python -m ingest.crawl.rss_fulltext_probe --per-source 20 --delay 0.6
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from ingest.crawl.fulltext import (
    Fetcher,
    api_get,
    classify,
    extract_fulltext,
    looks_blocked,
)
from ingest.envelope import MIN_BODY_CHARS

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CST = timezone(timedelta(hours=8))

# 上次实测：RSS 全文覆盖率 0%，HTML 可直接打开
SOURCES = (
    ("tass.com", "TASS"),
    ("jpost.com", "Jerusalem Post"),
    ("ukrinform.net", "Ukrinform"),
    ("en.interfax.com.ua", "Interfax Ukraine"),
    ("kyivpost.com", "Kyiv Post"),
)


def _now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def _preview(text: str, n: int = 180) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def sample_items(domain: str, n: int, rng: random.Random) -> tuple[list[dict], int]:
    head = api_get({"variant": "military", "source": domain, "limit": 1})
    total = int(head.get("total") or 0)
    if total <= 0:
        return [], 0
    take = min(n, total)
    newest_n = min(take // 2, total)
    older_n = take - newest_n
    items: list[dict] = []
    seen: set[int] = set()
    if newest_n:
        data = api_get(
            {"variant": "military", "source": domain, "limit": newest_n, "offset": 0}
        )
        for it in data.get("items") or []:
            if it.get("id") not in seen:
                seen.add(it["id"])
                items.append(it)
    if older_n and total > newest_n:
        max_off = max(0, total - older_n)
        offset = rng.randint(newest_n, max_off) if max_off > newest_n else newest_n
        data = api_get(
            {
                "variant": "military",
                "source": domain,
                "limit": older_n,
                "offset": offset,
            }
        )
        for it in data.get("items") or []:
            if it.get("id") not in seen:
                seen.add(it["id"])
                items.append(it)
    return items[:take], total


def probe_one(fetcher: Fetcher, item: dict, domain: str) -> dict:
    url = item.get("url") or ""
    desc = item.get("description") or ""
    ct = item.get("contentText") or ""
    rec: dict[str, Any] = {
        "id": item.get("id"),
        "domain": domain,
        "title": item.get("title"),
        "url": url,
        "publishedAt": item.get("publishedAt"),
        "rss_desc_len": len(desc),
        "rss_content_len": len(ct),
        "http": None,
        "robots": None,
        "method": None,
        "body_len": 0,
        "ratio": None,
        "status": "error",
        "preview": "",
        "error": None,
    }
    ok, robots_note = fetcher.robots_ok(url)
    rec["robots"] = robots_note
    if not ok:
        rec["status"] = "robots_disallow"
        rec["error"] = "robots.txt disallow"
        rec["preview"] = _preview(desc)
        return rec
    http, final, html, err = fetcher.get_html(url)
    rec["http"] = http
    rec["final_url"] = final
    if err:
        rec["status"] = "error"
        rec["error"] = err
        rec["preview"] = _preview(desc)
        return rec
    blocked = looks_blocked(html) or http in (401, 403, 429)
    body, method = extract_fulltext(html, domain) if html else ("", "empty")
    rec["method"] = method
    rec["body_len"] = len(body)
    rec["ratio"] = round(len(body) / max(len(desc), 1), 2)
    rec["status"] = classify(desc, body, http, blocked)
    rec["preview"] = _preview(body or desc)
    if rec["status"] == "blocked" and not rec["error"]:
        rec["error"] = f"HTTP {http} or challenge page"
    return rec


def pct(ok: int, n: int) -> str:
    return "n/a" if n == 0 else f"{ok / n:.0%}"


def _median(xs: list[int]) -> Optional[int]:
    if not xs:
        return None
    return int(statistics.median(xs))


def build_report(rows: list[dict], notes: list[str], per_source: int) -> str:
    now = _now()
    by_dom: dict[str, list[dict]] = {}
    for r in rows:
        by_dom.setdefault(r["domain"], []).append(r)
    lines = [
        "# RSS 五源补全文抽样报告",
        "",
        f"- 生成时间：`{now}`",
        f"- 每源抽样：{per_source} 篇（新稿一半 + 较旧一半）",
        "- 范围：`tass.com` / `jpost.com` / `ukrinform.net` / `en.interfax.com.ua` / `kyivpost.com`",
        "- 规则：不绕过验证码、登录墙、Cloudflare challenge；robots.txt 禁止则跳过",
        "- `ok`：抽到正文 ≥400 字，或至少为 RSS 摘要的 2 倍且 ≥80 字",
        "",
        "## 总览",
        "",
        "| 域名 | 抽样 | ok | still_teaser | too_short | blocked | 其他 | ok率 | 正文中位数 | 相对摘要 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for domain, _name in SOURCES:
        rs = by_dom.get(domain, [])
        st = Counter(r["status"] for r in rs)
        ok_n = st.get("ok", 0)
        bodies = [r["body_len"] for r in rs if r["status"] == "ok"]
        ratios = [r["ratio"] for r in rs if r["status"] == "ok" and r.get("ratio") is not None]
        other = len(rs) - ok_n - st.get("still_teaser", 0) - st.get("too_short", 0) - st.get("blocked", 0)
        med = _median(bodies)
        ratio_s = f"{statistics.median(ratios):.1f}×" if ratios else "—"
        lines.append(
            f"| `{domain}` | {len(rs)} | {ok_n} | {st.get('still_teaser', 0)} | "
            f"{st.get('too_short', 0)} | {st.get('blocked', 0)} | {other} | "
            f"{pct(ok_n, len(rs))} | {med if med is not None else '—'} | {ratio_s} |"
        )
    all_ok = sum(1 for r in rows if r["status"] == "ok")
    lines += [
        "",
        f"合计 **{all_ok}/{len(rows)}** 篇可补出可用全文（{pct(all_ok, len(rows))}）。",
        "",
        "## 抽取方法",
        "",
    ]
    methods = Counter(r.get("method") or "—" for r in rows if r["status"] == "ok")
    for m, c in methods.most_common():
        lines.append(f"- `{m}`：{c}")
    if notes:
        lines += ["", "## 说明", ""]
        for n in notes:
            lines.append(f"- {n}")
    lines += ["", "## 样本（每源最多 3 篇 ok + 失败各 1）", ""]
    for domain, name in SOURCES:
        rs = by_dom.get(domain, [])
        lines.append(f"### {name} (`{domain}`)")
        lines.append("")
        oks = [r for r in rs if r["status"] == "ok"][:3]
        fails = [r for r in rs if r["status"] != "ok"][:1]
        show = oks + fails
        if not show:
            lines.append("（无样本）")
            lines.append("")
            continue
        for r in show:
            lines.append(f"- **[{r['status']}]** {r.get('title') or '(无标题)'}")
            lines.append(f"  - HTTP `{r.get('http')}` · {r.get('method')} · 正文 {r.get('body_len')} 字 · 摘要 {r.get('rss_desc_len')} 字 · ×{r.get('ratio')}")
            lines.append(f"  - {r.get('url')}")
            lines.append(f"  - {_preview(r.get('preview') or '', 160)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="五源 RSS 补全文抽样")
    p.add_argument("--per-source", type=int, default=40)
    p.add_argument("--delay", type=float, default=0.7)
    p.add_argument("--seed", type=int, default=2)
    args = p.parse_args(argv)
    rng = random.Random(args.seed)
    fetcher = Fetcher(delay=args.delay)
    rows: list[dict] = []
    notes: list[str] = []
    print(f"每源 {args.per_source} 篇，间隔 {args.delay}s")
    for domain, name in SOURCES:
        try:
            items, total = sample_items(domain, args.per_source, rng)
        except Exception as exc:
            notes.append(f"{domain} 拉列表失败：{exc}")
            print(f"  [list-fail] {domain}: {exc}")
            continue
        print(f"\n== {name}  API={total}  sample={len(items)}")
        for i, item in enumerate(items, 1):
            rec = probe_one(fetcher, item, domain)
            rows.append(rec)
            flag = rec["status"]
            print(
                f"  [{flag:13}] {rec.get('http')} {rec['body_len']:5}c  "
                f"{(item.get('title') or '')[:52]}"
            )
            if i == 1 and rec["status"] != "ok":
                notes.append(
                    f"{domain} 首篇非 ok：status={rec['status']} http={rec.get('http')} "
                    f"method={rec.get('method')} err={rec.get('error')}"
                )
    out_dir = DATA / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "rss_fulltext_probe.jsonl"
    md_path = out_dir / "rss_fulltext_probe.md"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    md_path.write_text(build_report(rows, notes, args.per_source), encoding="utf-8")
    print(f"\nJSONL: {jsonl_path}")
    print(f"报告:  {md_path}")
    ok_n = sum(1 for r in rows if r["status"] == "ok")
    print(f"ok {ok_n}/{len(rows)}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
