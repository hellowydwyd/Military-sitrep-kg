"""环球网军事：channel_pc + list API + article 页。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from ingest.crawl.base import PoliteSession, html_to_text
from ingest.envelope import (
    SOURCE_HUANQIU,
    NewsDoc,
    build_doc,
    canonical_url,
    should_skip_url,
    ts_to_iso,
)

HOST = "https://mil.huanqiu.com"
CHANNEL_API = f"{HOST}/api/channel_pc"
# 军情动态；跳过图说/军视
KEEP_PATH_HINTS = ("军情动态",)
SKIP_PATH_HINTS = ("图说", "军视", "视频")


@dataclass
class ListItem:
    url: str
    title: str
    publish_time: Optional[str]
    aid: str
    host: str = ""
    addltype: str = "normal"


def _walk_nodes(node: dict, acc: list[tuple[str, str]]) -> None:
    path_name = node.get("path_name") or node.get("node_name") or ""
    nid = node.get("node") or ""
    children = node.get("children") or {}
    if children:
        for child in children.values():
            _walk_nodes(child, acc)
    elif nid:
        acc.append((nid, path_name))


def _pick_nodes(tree: dict) -> list[str]:
    acc: list[tuple[str, str]] = []
    _walk_nodes(tree, acc)
    keep = []
    for nid, name in acc:
        if any(h in name for h in SKIP_PATH_HINTS):
            continue
        if any(h in name for h in KEEP_PATH_HINTS) or name in ("军事",):
            keep.append(nid)
    # 只要叶子里的军情动态；若空则退回全部非视频节点
    if not keep:
        keep = [nid for nid, name in acc if not any(h in name for h in SKIP_PATH_HINTS)]
    # 优先军情动态
    dyn = [nid for nid, name in acc if "军情动态" in name]
    return dyn or keep


_HQ_API_HEADERS = {"Referer": f"{HOST}/", "Accept": "application/json,text/plain,*/*"}
_HQ_HTML_HEADERS = {"Referer": f"{HOST}/", "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}


def _iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def fetch_list(
    session: PoliteSession,
    *,
    max_items: int = 40,
    page_size: int = 20,
    since: Optional[datetime] = None,
) -> list[ListItem]:
    tree = session.get(CHANNEL_API, json_mode=True, headers=_HQ_API_HEADERS).json()
    nodes = _pick_nodes(tree)
    node_q = ",".join('"{}"'.format(n) for n in nodes)
    items: list[ListItem] = []
    offset = 0
    seen = set()
    hit_old = False
    while len(items) < max_items and not hit_old:
        node_enc = quote(node_q, safe=',"/')
        url = f"{HOST}/api/list?node={node_enc}&offset={offset}&limit={page_size}"
        payload = session.get(url, json_mode=True, headers=_HQ_API_HEADERS).json()
        rows = payload.get("list") or []
        got = 0
        for row in rows:
            if not isinstance(row, dict) or not row.get("aid"):
                continue
            host = row.get("host") or ""
            if host and host != "mil.huanqiu.com":
                continue
            addl = row.get("addltype") or "normal"
            typed = row.get("typedata") or {}
            video = ((typed.get("video") or {}).get("members")) or []
            gallery = ((typed.get("gallery") or {}).get("members")) or []
            if addl in ("video", "gallery") or video or gallery:
                continue
            aid = row["aid"]
            art_url = canonical_url(f"{HOST}/article/{aid}")
            if should_skip_url(art_url) or art_url in seen:
                continue
            pub = ts_to_iso(row.get("ctime") or row.get("xtime"))
            pub_dt = _iso_dt(pub)
            if since and pub_dt and pub_dt < since:
                hit_old = True
                continue
            seen.add(art_url)
            items.append(
                ListItem(
                    url=art_url,
                    title=(row.get("title") or "").strip(),
                    publish_time=pub,
                    aid=aid,
                    host=host,
                    addltype=addl,
                )
            )
            got += 1
            if len(items) >= max_items:
                break
        if got == 0 and not hit_old:
            break
        print(f"  [list] offset={offset} collected={len(items)} hit_old={hit_old}")
        offset += page_size
    return items[:max_items]


def fetch_article(session: PoliteSession, item: ListItem) -> NewsDoc:
    resp = session.get(item.url, headers=_HQ_HTML_HEADERS)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    def ta(cls: str) -> str:
        el = soup.select_one(f"textarea.{cls}")
        return (el.get_text() if el else "").strip()

    title = ta("article-title") or item.title
    raw_html = ta("article-content")
    body = html_to_text(raw_html) if raw_html else ""
    pub = ts_to_iso(ta("article-time")) or item.publish_time
    source_name = html_to_text(ta("article-source-name")) if ta("article-source-name") else ""
    extra = {"origin_source": source_name, "aid": item.aid} if (source_name or item.aid) else None
    return build_doc(
        url=item.url,
        title=title,
        body=body,
        publish_time=pub,
        source=SOURCE_HUANQIU,
        extra=extra,
    )
