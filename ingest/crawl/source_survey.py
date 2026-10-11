"""普查：哪些源能拿到正文（RSS 或 HTML），以及真实语言分布。

接口 lang 全是未校验的 en，本脚本用 langdetect 看标题/摘要/正文。

    python3 -m ingest.crawl.source_survey
    python3 -m ingest.crawl.source_survey --sample 5 --delay 0.5
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from langdetect import DetectorFactory, LangDetectException, detect_langs

from ingest.crawl.fulltext import Fetcher, api_get, classify, extract_fulltext, looks_blocked
from ingest.crawl.rss_fulltext_probe import DATA, _now, _preview

DetectorFactory.seed = 0
CST = timezone(timedelta(hours=8))

LANG_ZH = {
    "en": "英语",
    "ru": "俄语",
    "uk": "乌克兰语",
    "pl": "波兰语",
    "es": "西班牙语",
    "de": "德语",
    "fr": "法语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    "tr": "土耳其语",
    "ar": "阿拉伯语",
    "fa": "波斯语",
    "he": "希伯来语",
    "nl": "荷兰语",
    "ro": "罗马尼亚语",
    "cs": "捷克语",
    "hu": "匈牙利语",
    "sv": "瑞典语",
    "fi": "芬兰语",
    "el": "希腊语",
    "ja": "日语",
    "zh-cn": "中文",
    "zh-tw": "中文",
    "ko": "韩语",
    "id": "印尼语",
    "vi": "越南语",
    "bg": "保加利亚语",
    "sr": "塞尔维亚语",
    "hr": "克罗地亚语",
    "sk": "斯洛伐克语",
    "da": "丹麦语",
    "no": "挪威语",
    "nb": "挪威语",
    "ca": "加泰罗尼亚语",
    "unknown": "未知",
}

# 五源探针已验证可爬，跳过以免重复打站
KNOWN_CRAWL_OK = {
    "tass.com",
    "jpost.com",
    "ukrinform.net",
    "en.interfax.com.ua",
    "kyivpost.com",
}


def lang_name(code: str) -> str:
    return LANG_ZH.get(code, code)


def detect_lang(text: str) -> tuple[str, float]:
    blob = " ".join((text or "").split())
    if len(blob) < 40:
        return "unknown", 0.0
    try:
        ranked = detect_langs(blob)
    except LangDetectException:
        return "unknown", 0.0
    if not ranked:
        return "unknown", 0.0
    top = ranked[0]
    return top.lang, float(top.prob)


def item_text_for_lang(it: dict) -> str:
    ct = (it.get("contentText") or "").strip()
    if len(ct) >= 200:
        return ct
    parts = [it.get("title") or "", it.get("description") or "", ct]
    return "\n".join(p for p in parts if p)


def fetch_variant(variant: str) -> list[dict]:
    items: list[dict] = []
    offset = 0
    total = None
    while True:
        data = api_get({"variant": variant, "limit": 1000, "offset": offset})
        if total is None:
            total = int(data.get("total") or 0)
        batch = data.get("items") or []
        items.extend(batch)
        offset += len(batch)
        if not batch or (total is not None and offset >= total):
            break
        time.sleep(0.15)
    return items


def pick_samples(items: list[dict], n: int) -> list[dict]:
    if len(items) <= n:
        return list(items)
    idxs = {0, len(items) // 4, len(items) // 2, (3 * len(items)) // 4, len(items) - 1}
    ordered = sorted(idxs)
    extra = 0
    while len(ordered) < n:
        extra += 1
        cand = min(len(items) - 1, extra * (len(items) // (n + 1) or 1))
        if cand not in ordered:
            ordered.append(cand)
        if extra > n * 3:
            break
    ordered = sorted(set(ordered))[:n]
    return [items[i] for i in ordered]


def crawl_domain(fetcher: Fetcher, domain: str, samples: list[dict]) -> list[dict]:
    rows = []
    blocked_streak = 0
    for it in samples:
        url = it.get("url") or ""
        desc = it.get("description") or ""
        rec: dict[str, Any] = {
            "id": it.get("id"),
            "domain": domain,
            "title": it.get("title"),
            "url": url,
            "rss_desc_len": len(desc),
            "rss_content_len": len(it.get("contentText") or ""),
            "http": None,
            "status": "error",
            "body_len": 0,
            "method": None,
            "body_lang": None,
            "preview": "",
        }
        ok, robots_note = fetcher.robots_ok(url)
        rec["robots"] = robots_note
        if not ok:
            rec["status"] = "robots_disallow"
            rows.append(rec)
            blocked_streak += 1
            if blocked_streak >= 2:
                break
            continue
        http, final, html, err = fetcher.get_html(url)
        rec["http"] = http
        rec["final_url"] = final
        if err:
            rec["status"] = "error"
            rec["error"] = err
            rows.append(rec)
            blocked_streak += 1
            if blocked_streak >= 2:
                break
            continue
        blocked = looks_blocked(html) or http in (401, 403, 429)
        body, method = extract_fulltext(html, domain) if html else ("", "empty")
        rec["method"] = method
        rec["body_len"] = len(body)
        rec["status"] = classify(desc, body, http, blocked)
        rec["preview"] = _preview(body or desc, 140)
        if rec["status"] == "ok" and body:
            rec["body_lang"], rec["body_lang_prob"] = detect_lang(body)
            blocked_streak = 0
        elif rec["status"] in ("blocked", "http_error", "robots_disallow"):
            blocked_streak += 1
        else:
            blocked_streak = 0
        rows.append(rec)
        if blocked_streak >= 2 and rec["status"] in ("blocked", "http_error"):
            break
    return rows


def source_verdict(rss_ct_rate: float, crawl_rows: list[dict] | None, known_ok: bool) -> str:
    if rss_ct_rate >= 0.8:
        return "rss"
    if known_ok:
        return "crawl"

    crawl_ok = False
    crawl_blocked = False
    crawl_mixed = False
    if crawl_rows:
        n = len(crawl_rows)
        usable = sum(
            1
            for r in crawl_rows
            if r["status"] == "ok" or (r["status"] == "still_teaser" and r["body_len"] >= 200)
        )
        blocked_n = sum(
            1
            for r in crawl_rows
            if r["status"] in ("blocked", "http_error", "robots_disallow", "error")
        )
        if n and usable / n >= 0.6:
            crawl_ok = True
        elif n and blocked_n / n >= 0.5 and usable == 0:
            crawl_blocked = True
        elif usable > 0:
            crawl_mixed = True

    # RSS 已有一部分全文：即使 HTML 不稳，也能用 contentText
    if rss_ct_rate >= 0.2:
        return "rss_partial"
    if crawl_ok:
        return "crawl"
    if crawl_blocked:
        return "blocked"
    if crawl_mixed:
        return "mixed"
    return "no_text"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="信源可爬性与语言普查")
    p.add_argument("--sample", type=int, default=5)
    p.add_argument("--delay", type=float, default=0.55)
    p.add_argument("--include-intel", action="store_true", default=True)
    args = p.parse_args(argv)

    print("拉取 military …")
    mil = fetch_variant("military")
    print(f"  military {len(mil)}")
    intel: list[dict] = []
    if args.include_intel:
        print("拉取 intel …")
        intel = fetch_variant("intel")
        print(f"  intel {len(intel)}")

    # 按 url 去重，军事优先
    by_url: dict[str, dict] = {}
    for it in intel:
        u = it.get("url")
        if u:
            it["_variant"] = "intel"
            by_url[u] = it
    for it in mil:
        u = it.get("url")
        if u:
            it["_variant"] = "military"
            by_url[u] = it
    items = list(by_url.values())

    by_dom: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        src = it.get("source") or {}
        d = src.get("domain") or "(none)"
        it["_domain"] = d
        it["_name"] = src.get("name") or d
        it["_cat"] = it.get("category") or ""
        lang, prob = detect_lang(item_text_for_lang(it))
        it["_lang"] = lang
        it["_lang_prob"] = prob
        by_dom[d].append(it)

    print(f"去重后 {len(items)} 篇，{len(by_dom)} 个域名")

    need_crawl = []
    for domain, rows in sorted(by_dom.items(), key=lambda x: -len(x[1])):
        n = len(rows)
        ct = sum(1 for r in rows if (r.get("contentText") or "").strip())
        rate = ct / n if n else 0
        if domain in KNOWN_CRAWL_OK:
            continue
        if rate >= 0.8:
            continue
        need_crawl.append(domain)
    print(f"需抽样试爬：{len(need_crawl)} 个域名")

    fetcher = Fetcher(delay=args.delay, timeout=15.0)
    crawl_by_dom: dict[str, list[dict]] = {}
    for i, domain in enumerate(need_crawl, 1):
        samples = pick_samples(by_dom[domain], args.sample)
        print(f"[{i}/{len(need_crawl)}] crawl {domain} n_api={len(by_dom[domain])} sample={len(samples)}")
        rows = crawl_domain(fetcher, domain, samples)
        crawl_by_dom[domain] = rows
        st = Counter(r["status"] for r in rows)
        print(f"    {dict(st)}")

    source_rows = []
    for domain, arts in sorted(by_dom.items(), key=lambda x: -len(x[1])):
        n = len(arts)
        ct_n = sum(1 for r in arts if (r.get("contentText") or "").strip())
        rss_rate = ct_n / n if n else 0
        names = Counter(r["_name"] for r in arts)
        cats = Counter(r["_cat"] for r in arts)
        langs = Counter(r["_lang"] for r in arts)
        maj_lang, maj_n = langs.most_common(1)[0]
        crawl_rows = crawl_by_dom.get(domain)
        known = domain in KNOWN_CRAWL_OK
        verdict = source_verdict(rss_rate, crawl_rows, known)
        crawl_ok = 0
        crawl_n = 0
        crawl_http = None
        if known:
            crawl_n = 40
            crawl_ok = 40 if domain != "kyivpost.com" else 38
            if domain == "en.interfax.com.ua":
                crawl_ok = 39
        elif crawl_rows is not None:
            crawl_n = len(crawl_rows)
            crawl_ok = sum(
                1
                for r in crawl_rows
                if r["status"] == "ok"
                or (r["status"] == "still_teaser" and r["body_len"] >= 200)
            )
            https = [r.get("http") for r in crawl_rows if r.get("http") is not None]
            crawl_http = Counter(https).most_common(1)[0][0] if https else None
        source_rows.append(
            {
                "domain": domain,
                "name": names.most_common(1)[0][0],
                "n": n,
                "rss_ct": ct_n,
                "rss_rate": round(rss_rate, 3),
                "category": cats.most_common(1)[0][0],
                "lang": maj_lang,
                "lang_n": maj_n,
                "lang_share": round(maj_n / n, 3) if n else 0,
                "langs": dict(langs),
                "verdict": verdict,
                "crawl_ok": crawl_ok,
                "crawl_n": crawl_n,
                "crawl_http": crawl_http,
                "variants": sorted({r.get("_variant") for r in arts}),
            }
        )

    usable = [s for s in source_rows if s["verdict"] in ("rss", "crawl", "rss_partial")]
    blocked = [s for s in source_rows if s["verdict"] == "blocked"]
    mixed = [s for s in source_rows if s["verdict"] == "mixed"]
    no_text = [s for s in source_rows if s["verdict"] in ("no_text", "untested")]

    # 可入库正文的篇数估计：RSS 已有全文 + 可爬源中尚无 RSS 全文的篇
    crawlable_doms = {s["domain"] for s in source_rows if s["verdict"] in ("rss", "crawl", "rss_partial", "mixed")}
    rss_bodies = 0
    crawl_est = 0
    for it in items:
        has_ct = bool((it.get("contentText") or "").strip())
        if has_ct:
            rss_bodies += 1
        elif it["_domain"] in {s["domain"] for s in source_rows if s["verdict"] in ("crawl", "mixed")}:
            crawl_est += 1

    lang_all = Counter(it["_lang"] for it in items)
    lang_if_body = Counter()
    for it in items:
        has_ct = bool((it.get("contentText") or "").strip())
        v = next(s["verdict"] for s in source_rows if s["domain"] == it["_domain"])
        if has_ct or v in ("crawl", "mixed", "rss"):
            lang_if_body[it["_lang"]] += 1

    md = build_report(
        source_rows,
        lang_all,
        len(items),
        rss_bodies,
        crawl_est,
        crawl_by_dom,
    )
    out_dir = DATA / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "source_survey.md"
    json_path = out_dir / "source_survey.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "generatedAt": _now(),
                "n_items": len(items),
                "n_domains": len(source_rows),
                "sources": source_rows,
                "lang_all": dict(lang_all),
                "rss_bodies": rss_bodies,
                "crawl_est_extra": crawl_est,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    crawl_jsonl = out_dir / "source_survey_crawl.jsonl"
    with crawl_jsonl.open("w", encoding="utf-8") as f:
        for rows in crawl_by_dom.values():
            for rec in rows:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\n可拿正文源 {len(usable)}  拦爬 {len(blocked)}  不稳定 {len(mixed)}  无正文 {len(no_text)}")
    print(f"报告: {md_path}")
    return 0


def _fmt_langs(langs: dict, n: int) -> str:
    parts = []
    for code, c in sorted(langs.items(), key=lambda x: -x[1]):
        if c / n < 0.05 and len(parts) >= 1:
            continue
        parts.append(f"{lang_name(code)} {c}")
    return "、".join(parts[:4]) or "—"


def build_report(
    source_rows: list[dict],
    lang_all: Counter,
    n_items: int,
    rss_bodies: int,
    crawl_est: int,
    crawl_by_dom: dict,
) -> str:
    now = _now()
    groups = {
        "rss": [s for s in source_rows if s["verdict"] == "rss"],
        "crawl": [s for s in source_rows if s["verdict"] == "crawl"],
        "rss_partial": [s for s in source_rows if s["verdict"] == "rss_partial"],
        "mixed": [s for s in source_rows if s["verdict"] == "mixed"],
        "blocked": [s for s in source_rows if s["verdict"] == "blocked"],
        "no_text": [s for s in source_rows if s["verdict"] in ("no_text", "untested")],
    }
    n_ok_src = len(groups["rss"]) + len(groups["crawl"]) + len(groups["rss_partial"])
    n_ok_arts = sum(
        s["n"]
        for s in source_rows
        if s["verdict"] in ("rss", "crawl", "rss_partial")
    )
    lines = [
        "# 信源可爬性与语言普查",
        "",
        f"- 生成时间：`{now}`",
        f"- 语料：military + intel，按 url 去重后 **{n_items}** 篇、**{len(source_rows)}** 个域名",
        "- 语言：对标题+摘要+RSS正文做 `langdetect`（接口 `lang=en` 未校验，不用）",
        "- 可拿正文：RSS `contentText` 覆盖 ≥80%，或 HTML 抽样可用率 ≥60%（五源探针结果直接沿用）",
        "- 不绕过验证码 / Cloudflare / 登录墙",
        "",
        "## 结论摘要",
        "",
        f"- 可以稳定拿到正文的源：**{n_ok_src}** 个，覆盖 **{n_ok_arts}** 篇",
        f"  - RSS 已带全文：{len(groups['rss'])} 源",
        f"  - RSS 无全文但 HTML 可爬：{len(groups['crawl'])} 源",
        f"  - RSS 部分有全文：{len(groups['rss_partial'])} 源",
        f"- 抽样不稳定（mixed）：{len(groups['mixed'])} 源",
        f"- 明确拦爬 / 空页：{len(groups['blocked'])} 源",
        f"- 抽不到可用正文：{len(groups['no_text'])} 源",
        f"- 现成 RSS 全文 **{rss_bodies}** 篇；若加上可爬源里缺 RSS 正文的稿，大约还能补 **{crawl_est}** 篇",
        "",
        "## 语言分布（按篇，全部去重稿）",
        "",
        "| 语言 | 篇数 | 占比 |",
        "|---|---:|---:|",
    ]
    for code, c in lang_all.most_common():
        lines.append(f"| {lang_name(code)} `{code}` | {c} | {c / n_items:.1%} |")

    def src_table(title: str, rows: list[dict], extra_crawl: bool) -> None:
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("（无）")
            return
        n = sum(s["n"] for s in rows)
        lines.append(f"共 **{len(rows)}** 源、**{n}** 篇。")
        lines.append("")
        head = "| 域名 | 篇数 | RSS全文 | 多数语言 | 分类 |"
        sep = "|---|---:|---:|---|---|"
        if extra_crawl:
            head += " 试爬 |"
            sep += "---:|"
        lines.append(head)
        lines.append(sep)
        for s in sorted(rows, key=lambda x: -x["n"]):
            lang = f"{lang_name(s['lang'])} {s['lang_share']:.0%}"
            if len(s["langs"]) > 1:
                lang += f"（{_fmt_langs(s['langs'], s['n'])}）"
            row = (
                f"| `{s['domain']}` | {s['n']} | {s['rss_rate']:.0%} | {lang} | `{s['category']}` |"
            )
            if extra_crawl:
                if s["crawl_n"]:
                    row += f" {s['crawl_ok']}/{s['crawl_n']} |"
                else:
                    row += " — |"
            lines.append(row)

    src_table("一、RSS 已带全文（可直接用 contentText）", groups["rss"], False)
    src_table("二、RSS 无全文，但 HTML 可爬", groups["crawl"], True)
    src_table("三、RSS 部分有全文（覆盖 20%–80%）", groups["rss_partial"], True)
    src_table("四、试爬不稳定", groups["mixed"], True)
    src_table("五、拦爬 / 验证码 / 空页", groups["blocked"], True)
    src_table("六、抽不到正文", groups["no_text"], True)
    if any(s["domain"] == "en.topwar.ru" for s in groups["no_text"]):
        lines += [
            "",
            "`en.topwar.ru` 的 RSS 链到俄语站 `topwar.ru`，页面有正文，但通用抽取命中订阅/免责声明，5 篇样本都偏短。要补俄语全文需要单独写选择器，不算拦爬。",
        ]

    lines += [
        "",
        "## 可入库正文源的语言构成",
        "",
        "只统计 verdict 为 `rss` / `crawl` / `rss_partial` 的源。",
        "",
        "| 语言 | 篇数 | 占比 | 主要域名 |",
        "|---|---:|---:|---|",
    ]
    ok_src = [s for s in source_rows if s["verdict"] in ("rss", "crawl", "rss_partial")]
    ok_n = sum(s["n"] for s in ok_src)
    lang_ok = Counter()
    lang_doms: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for s in ok_src:
        lang_ok[s["lang"]] += s["lang_n"]
        # remaining langs
        for code, c in s["langs"].items():
            if code != s["lang"]:
                lang_ok[code] += c
        lang_doms[s["lang"]].append((s["n"], s["domain"]))
    # recount from langs dict more accurately
    lang_ok = Counter()
    for s in ok_src:
        for code, c in s["langs"].items():
            lang_ok[code] += c
    for code, c in lang_ok.most_common():
        tops = ", ".join(
            f"`{d}`" for _, d in sorted(lang_doms.get(code, []), reverse=True)[:5]
        )
        if not tops:
            # language is minority on sources whose majority is another lang
            tops = "（混在其他源内）"
        share = c / ok_n if ok_n else 0
        lines.append(f"| {lang_name(code)} `{code}` | {c} | {share:.1%} | {tops} |")

    lines += [
        "",
        "## 试爬失败样例",
        "",
    ]
    shown = 0
    for domain, rows in crawl_by_dom.items():
        bad = [r for r in rows if r["status"] not in ("ok",)]
        if not bad:
            continue
        r = bad[0]
        lines.append(
            f"- `{domain}` [{r['status']}] HTTP `{r.get('http')}` {r.get('title') or ''} "
            f"— {_preview(r.get('preview') or '', 100)}"
        )
        shown += 1
        if shown >= 20:
            break
    if shown == 0:
        lines.append("（无）")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
