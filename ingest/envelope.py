"""下游唯一输入：NewsDoc 信封。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from urllib.parse import urldefrag, urlparse, urlunparse

CST = timezone(timedelta(hours=8))

SOURCE_HUANQIU = "huanqiu_mil"
SOURCE_CMNEE = "cmnee"
SOURCE_RSS = "rss_api"
SOURCE_NAMES = {
    SOURCE_HUANQIU: "环球网军事",
    SOURCE_CMNEE: "CMNEE",
    SOURCE_RSS: "RSS军事新闻",
}

MIN_BODY_CHARS = 80
SKIP_URL_PARTS = (
    "/slide",
    "slide.",
    "/video",
    "/photo/",
    "/live/",
    "/zt/",
)


def canonical_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", "", ""))


def make_doc_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()


def body_hash(body: str) -> str:
    norm = re.sub(r"\s+", " ", (body or "").strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def iso_now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def ts_to_iso(value: Any) -> Optional[str]:
    """Unix 秒 / 毫秒 / 常见字符串 → +08:00 ISO。解不开返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=CST)
        return dt.astimezone(CST).replace(microsecond=0).isoformat()
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        n = int(value)
        if n > 10**12:
            n = n / 1000.0
        elif n > 10**11:
            n = n / 1000.0
        dt = datetime.fromtimestamp(n, tz=CST)
        return dt.replace(microsecond=0).isoformat()
    text = str(value).strip()
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt.astimezone(CST).replace(microsecond=0).isoformat()
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y年%m月%d日 %H:%M",
        "%Y年%m月%d日%H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=CST)
            return dt.replace(microsecond=0).isoformat()
        except ValueError:
            continue
    return None


def should_skip_url(url: str) -> bool:
    low = (url or "").lower()
    return any(p in low for p in SKIP_URL_PARTS)


@dataclass
class NewsDoc:
    doc_id: str
    title: str
    body: str
    publish_time: str
    source: str
    source_name: str
    url: str
    html_hash: str
    lang: str = "zh"
    fetched_at: str = ""
    channel: str = "军事"
    extra: Optional[dict] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d.get("extra"):
            d.pop("extra", None)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @property
    def body_chars(self) -> int:
        return len(re.sub(r"\s+", "", self.body or ""))

    def missing_fields(self) -> list[str]:
        miss = []
        for key in ("doc_id", "title", "body", "publish_time", "source", "url"):
            if not getattr(self, key):
                miss.append(key)
        if self.body_chars < MIN_BODY_CHARS:
            miss.append("body_too_short")
        return miss


def build_doc(
    *,
    url: str,
    title: str,
    body: str,
    publish_time: Optional[str],
    source: str,
    extra: Optional[dict] = None,
    lang: Optional[str] = None,
) -> NewsDoc:
    url = canonical_url(url)
    title = re.sub(r"\s+", " ", (title or "").strip())
    body = (body or "").strip()
    return NewsDoc(
        doc_id=make_doc_id(url),
        title=title,
        body=body,
        publish_time=publish_time or "",
        source=source,
        source_name=SOURCE_NAMES.get(source, source),
        url=url,
        html_hash=body_hash(body),
        lang=lang or ("zh" if source != SOURCE_RSS else "en"),
        fetched_at=iso_now(),
        extra=extra or None,
    )
