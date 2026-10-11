"""HTML 正文抽取与礼貌抓取（RSS 补全文、探针共用）。"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from urllib.parse import urlencode, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from ingest.envelope import MIN_BODY_CHARS

API = "http://8.130.80.151/rss-api/api/public/news"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
CRAWLER_UA = "MilitarySitrepKG/0.3 (+local-research; rss-ingest)"

GENERIC_SELECTORS = [
    "div[itemprop='articleBody']",
    "div.entry-content",
    "div.post-content",
    "div.article-body",
    "div.article-content",
    "div.news-content",
    "article",
]

SELECTORS = {
    "tass.com": [
        "div.text-block",
        "div.news-content",
        "div.text-content",
        "article",
        "div[itemprop='articleBody']",
    ],
    "jpost.com": [
        "div.article-inner-content",
        "div#article-content",
        "div[itemprop='articleBody']",
        "article",
    ],
    "ukrinform.net": [
        "div.newsText",
        "div.article-content",
        "div.news-text",
        "article",
    ],
    "en.interfax.com.ua": [
        "div.article-content",
        "div#articleData",
        "div[itemprop='articleBody']",
        "article",
    ],
    "kyivpost.com": [
        "div.post-content",
        "div.entry-content",
        "div.article-content",
        "article",
    ],
}

SKIP_LINE = re.compile(
    r"^(subscribe|sign in|log in|cookie|we use cookies|we’d like to set|"
    r"advertisement|related (news|stories|articles)|read more|follow us|"
    r"share this|please enable javascript|this site uses cookies|"
    r"manage (cookie|consent)|accept (all )?cookies)",
    re.I,
)
BLOCK_HINTS = (
    "just a moment",
    "verify you are human",
    "cf-challenge",
    "attention required",
    "access denied",
    "request unsuccessful",
)


def _p_text(node) -> str:
    paras = []
    for p in node.find_all("p"):
        t = " ".join(p.get_text(" ", strip=True).split())
        if len(t) < 40 or SKIP_LINE.search(t):
            continue
        if "<script" in t.lower() or "telegram-widget" in t.lower():
            continue
        paras.append(t)
    return "\n".join(paras)


def _jsonld_article_body(soup: BeautifulSoup) -> str:
    best = ""
    for sc in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = sc.string or sc.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack: list[Any] = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, list):
                stack.extend(cur)
            elif isinstance(cur, dict):
                body = cur.get("articleBody")
                if isinstance(body, str) and len(body) > len(best):
                    best = body
                graph = cur.get("@graph")
                if graph is not None:
                    stack.append(graph)
    if not best:
        return ""
    return BeautifulSoup(best, "html.parser").get_text("\n", strip=True)


def strip_html(text: str) -> str:
    if not text:
        return ""
    if "<" not in text:
        return " ".join(text.split()).strip() if "\n" not in text[:200] else text.strip()
    return BeautifulSoup(text, "html.parser").get_text("\n", strip=True)


def extract_fulltext(html: str, domain: str) -> tuple[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    ld = _jsonld_article_body(BeautifulSoup(html or "", "html.parser"))
    picked = ""
    method = "none"
    for sel in SELECTORS.get(domain, GENERIC_SELECTORS):
        node = soup.select_one(sel)
        if not node:
            continue
        text = _p_text(node)
        if len(text) > len(picked):
            picked = text
            method = f"css:{sel}"
    if not picked:
        article = soup.find("article")
        if article:
            picked = _p_text(article)
            method = "article"
    if not picked:
        picked = _p_text(soup)
        method = "all-p"
    if ld and len(ld) >= 400:
        if not picked or len(picked) < len(ld) * 1.25:
            return ld.strip(), "jsonld"
        return picked.strip(), method + "+jsonld-ignored"
    if picked:
        return picked.strip(), method
    if ld:
        return ld.strip(), "jsonld-short"
    return "", method


def classify(rss_desc: str, body: str, http: int, blocked: bool) -> str:
    if blocked or http in (401, 403, 429, 503) or http >= 500:
        return "blocked"
    if http != 200:
        return "http_error"
    n = len(body)
    d = max(len(rss_desc or ""), 1)
    if n < MIN_BODY_CHARS:
        return "too_short"
    if n < 400 and n < 1.5 * d:
        return "still_teaser"
    if n >= 400 or n >= 2 * d:
        return "ok"
    return "still_teaser"


def looks_blocked(html: str) -> bool:
    low = (html or "")[:4000].lower()
    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    blob = title + " " + low
    return any(h in blob for h in BLOCK_HINTS)


class Fetcher:
    def __init__(self, delay: float, timeout: float = 20.0):
        self.delay = delay
        self.timeout = timeout
        self.sess = requests.Session()
        self.sess.headers.update(
            {
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._last: dict[str, float] = {}
        self._robots: dict[str, Optional[RobotFileParser]] = {}

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last.get(host, 0.0)
        wait = self.delay - (time.time() - last)
        if wait > 0:
            time.sleep(wait)

    def robots_ok(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                r = self.sess.get(origin + "/robots.txt", timeout=10)
                self._last[urlparse(origin).netloc] = time.time()
                if r.status_code == 200 and r.text:
                    rp.parse(r.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None
            except requests.RequestException:
                self._robots[origin] = None
        rp = self._robots.get(origin)
        if rp is None:
            return True, "no-robots"
        ok = rp.can_fetch("*", url)
        return ok, "allow" if ok else "disallow"

    def get_html(self, url: str) -> tuple[int, str, str, Optional[str]]:
        self._wait(url)
        try:
            r = self.sess.get(url, timeout=self.timeout, allow_redirects=True)
            self._last[urlparse(url).netloc] = time.time()
            ctype = (r.headers.get("content-type") or "").lower()
            html = (
                r.text
                if ("html" in ctype or r.content[:20].lstrip().startswith(b"<"))
                else ""
            )
            return r.status_code, r.url, html, None
        except requests.RequestException as exc:
            self._last[urlparse(url).netloc] = time.time()
            return 0, url, "", f"{type(exc).__name__}: {exc}"


def api_get(params: dict, timeout: float = 30.0) -> dict:
    r = requests.get(
        API, params=params, timeout=timeout, headers={"User-Agent": CRAWLER_UA}
    )
    r.raise_for_status()
    return r.json()


def api_url(params: dict) -> str:
    return f"{API}?{urlencode(params)}"
