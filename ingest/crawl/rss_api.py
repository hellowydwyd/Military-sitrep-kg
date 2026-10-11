"""合作方 RSS 归档 → NewsDoc。白名单源；RSS 有全文则用之，否则礼貌抓 HTML。"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from ingest.crawl.allowlist import ALLOW_DOMAINS, BLOCKED_DOMAINS, EN_DOMAINS
from ingest.crawl.fulltext import (
    Fetcher,
    api_get,
    extract_fulltext,
    looks_blocked,
    strip_html,
)

from ingest.envelope import (
    MIN_BODY_CHARS,
    SOURCE_RSS,
    NewsDoc,
    build_doc,
    make_doc_id,
    ts_to_iso,
)

# 与环球网军事稿更可比的英文源（冲突/官方口径，不当百科铺开）
PREFERRED_DOMAINS = frozenset(
    {
        "tass.com",
        "jpost.com",
        "ukrinform.net",
        "en.mehrnews.com",
        "en.interfax.com.ua",
        "defensemirror.com",
        "kyivpost.com",
        "kyivindependent.com",
        "ynetnews.com",
    }
)
KINETIC_MARK = (
    "missile",
    "drone",
    "strike",
    "attack",
    "troops",
    "forces",
    "idf",
    "irgc",
    "houthi",
    "ukraine",
    "israel",
    "iran",
    "saudi",
    "yemen",
    "intercept",
    "airstrike",
    "shelling",
    "killed",
)

try:
    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = 0
except ImportError:  # pragma: no cover
    detect = None  # type: ignore[assignment]
    LangDetectException = Exception  # type: ignore[misc,assignment]


@dataclass
class RssItem:
    api_id: int
    url: str
    title: str
    description: str
    content_text: str
    published_at: str
    variant: str
    category: str
    domain: str
    source_name: str
    lang_api: str
    raw: dict


def _detect_lang(*parts: str) -> str:
    blob = " ".join(p for p in parts if p)
    blob = " ".join(blob.split())
    if len(blob) < 40 or detect is None:
        return "unknown"
    try:
        return detect(blob)
    except LangDetectException:
        return "unknown"


def fetch_list(
    *,
    max_items: int,
    variant: str = "military",
    since: datetime | None = None,
    until: datetime | None = None,
    lang: str = "en",
) -> list[RssItem]:
    params: dict[str, Any] = {"variant": variant, "limit": min(1000, max(1, max_items))}
    if since is not None:
        params["from"] = since.date().isoformat()
    if until is not None:
        params["to"] = until.date().isoformat()
    items: list[RssItem] = []
    offset = 0
    total: Optional[int] = None
    while len(items) < max_items:
        params["offset"] = offset
        params["limit"] = min(1000, max_items - len(items))
        data = api_get(params)
        if total is None:
            total = int(data.get("total") or 0)
        batch = data.get("items") or []
        if not batch:
            break
        for raw in batch:
            item = _parse_item(raw)
            if item is None:
                continue
            if _skip_preview(item, lang):
                continue
            items.append(item)
            if len(items) >= max_items:
                break
        offset += len(batch)
        if total is not None and offset >= total:
            break
    return items


def _parse_item(raw: dict) -> Optional[RssItem]:
    url = (raw.get("url") or "").strip()
    if not url:
        return None
    src = raw.get("source") or {}
    domain = (src.get("domain") or urlparse(url).netloc or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return RssItem(
        api_id=int(raw.get("id") or 0),
        url=url,
        title=(raw.get("title") or "").strip(),
        description=strip_html(raw.get("description") or ""),
        content_text=strip_html(raw.get("contentText") or ""),
        published_at=raw.get("publishedAt") or "",
        variant=raw.get("variant") or "",
        category=raw.get("category") or "",
        domain=domain,
        source_name=(src.get("name") or domain),
        lang_api=raw.get("lang") or "",
        raw=raw,
    )


def overlap_score(item: RssItem) -> tuple:
    body = item.content_text or ""
    blob = f"{item.title} {item.description} {body}".lower()
    kin = sum(1 for k in KINETIC_MARK if k in blob)
    full = 1 if len(body.strip()) >= MIN_BODY_CHARS else 0
    pref = 1 if item.domain in PREFERRED_DOMAINS else 0
    return (full, pref, kin, len(body))


def cap_per_day(items: list[RssItem], per_day: int) -> list[RssItem]:
    if per_day <= 0:
        return items
    by_day: dict[str, list[RssItem]] = defaultdict(list)
    for item in items:
        by_day[(item.published_at or "")[:10]].append(item)
    out: list[RssItem] = []
    for day in sorted(by_day):
        ranked = sorted(by_day[day], key=overlap_score, reverse=True)
        out.extend(ranked[:per_day])
    return out


def _skip_preview(item: RssItem, lang: str) -> bool:
    if item.domain in BLOCKED_DOMAINS:
        return True
    if item.domain not in ALLOW_DOMAINS:
        return True
    if lang in ("en", "en-only") and item.domain not in EN_DOMAINS:
        return True
    return False


def fetch_article(fetcher: Fetcher, item: RssItem) -> NewsDoc:
    rss_body = item.content_text.strip()
    body = rss_body
    body_from = "rss"
    method = "contentText"
    if len(rss_body) < MIN_BODY_CHARS:
        ok, robots = fetcher.robots_ok(item.url)
        if not ok:
            raise PermissionError(f"robots.txt 禁止 ({robots}): {item.url}")
        http, _final, html, err = fetcher.get_html(item.url)
        if err:
            raise RuntimeError(err)
        if looks_blocked(html) or http in (401, 403, 429) or http >= 500:
            raise RuntimeError(f"页面不可用 HTTP {http}")
        if http != 200:
            raise RuntimeError(f"HTTP {http}")
        body, method = extract_fulltext(html, item.domain)
        body_from = "html"
    lang = _detect_lang(item.title, item.description, body)
    pub = ts_to_iso(item.published_at) or item.published_at
    extra = {
        "rss_id": item.api_id,
        "domain": item.domain,
        "variant": item.variant,
        "category": item.category,
        "body_from": body_from,
        "extract_method": method,
        "outlet": item.source_name,
        "lang_detect": lang,
    }
    doc = build_doc(
        url=item.url,
        title=item.title,
        body=body,
        publish_time=pub,
        source=SOURCE_RSS,
        extra=extra,
        lang=lang if lang != "unknown" else "en",
    )
    doc.source_name = item.source_name
    doc.channel = "军事" if item.variant != "intel" else "情报安全"
    return doc


def run(
    store,
    fetcher: Fetcher,
    *,
    limit: int,
    force: bool,
    since: datetime | None,
    until: datetime | None,
    variant: str,
    lang: str,
    per_day: int = 0,
) -> tuple[list[NewsDoc], dict]:
    stats = {
        "listed": 0,
        "written": 0,
        "skipped_seen": 0,
        "skipped_quality": 0,
        "skipped_lang": 0,
        "skipped_source": 0,
        "errors": 0,
    }
    # 按日抽样时把窗口内白名单列表拉全，再打分截断
    list_cap = 20000 if per_day else max(limit * 4, limit)
    listed = fetch_list(
        max_items=list_cap,
        variant=variant,
        since=since,
        until=until,
        lang=lang,
    )
    listed = cap_per_day(listed, per_day)
    stats["listed"] = len(listed)
    docs: list[NewsDoc] = []
    for item in listed:
        if len(docs) >= limit:
            break
        if not force:
            row = store.lookup(item.url)
            if row and row.get("status") == "ok":
                stats["skipped_seen"] += 1
                continue
        try:
            doc = fetch_article(fetcher, item)
        except PermissionError as exc:
            stats["errors"] += 1
            store.mark(
                item.url,
                doc_id=make_doc_id(item.url),
                source=SOURCE_RSS,
                status="skip_robots",
                title=item.title,
            )
            print(f"  [robots] {item.url} ({exc})", file=sys.stderr)
            continue
        except Exception as exc:
            stats["errors"] += 1
            store.mark(
                item.url,
                doc_id=make_doc_id(item.url),
                source=SOURCE_RSS,
                status="error",
                title=item.title or str(exc)[:80],
            )
            print(f"  [error] {item.url} ({exc})", file=sys.stderr)
            continue
        if lang in ("en", "en-only") and not (doc.lang or "").startswith("en"):
            stats["skipped_lang"] += 1
            store.mark(
                doc.url,
                doc_id=doc.doc_id,
                html_hash=doc.html_hash,
                source=SOURCE_RSS,
                status="skip_lang",
                title=doc.title,
            )
            print(f"  [skip-lang {doc.lang}] {doc.title[:40]}")
            continue
        miss = doc.missing_fields()
        if "body_too_short" in miss or not doc.body:
            stats["skipped_quality"] += 1
            store.mark(
                doc.url,
                doc_id=doc.doc_id,
                html_hash=doc.html_hash,
                source=SOURCE_RSS,
                status="skip_short",
                title=doc.title,
            )
            print(f"  [skip-short {doc.body_chars}<{MIN_BODY_CHARS}] {doc.title[:40]}")
            continue
        store.append(doc)
        docs.append(doc)
        stats["written"] += 1
        via = (doc.extra or {}).get("body_from")
        print(
            f"  [ok {via} {doc.lang}] {doc.publish_time[:16] if doc.publish_time else '?'}  "
            f"{doc.title[:48]}"
        )
    return docs, stats
