"""中文维基条目按字节偏移取单篇。走镜像包，不用在线接口。

    python3 -m ingest.zhwiki fetch 本雅明·内塔尼亚胡
    python3 -m ingest.zhwiki stat
"""

from __future__ import annotations

import argparse
import bz2
import json
import re
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INDEX = DATA / "zhwiki" / "zhwiki-index.txt.bz2"
CACHE = DATA / "zhwiki" / "pages.jsonl"

DUMPS = {
    "umu": (
        "https://ftp.acc.umu.se/mirror/wikimedia.org/dumps/zhwiki/{stamp}/"
        "zhwiki-{stamp}-pages-articles-multistream"
    ),
    "wm": (
        "https://dumps.wikimedia.org/zhwiki/{stamp}/"
        "zhwiki-{stamp}-pages-articles-multistream"
    ),
}
STAMP = "20261001"
UA = "MilitarySitrepKG/0.3 (+local-research; zhwiki-offline)"

_TAG = re.compile(r"<[^>]+>")
_REF = re.compile(r"<ref[^>]*?/>|<ref[^>]*?>.*?</ref>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_FILELINK = re.compile(r"\[\[(?:File|Image|文件|图像):[^\]]*\]\]", re.I)
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_EXT_LINK = re.compile(r"\[https?://[^\]]*\]")


def _url(kind: str, host: str) -> str:
    return DUMPS[host].format(stamp=STAMP) + kind


def download_index(host: str = "umu", dest: Path = INDEX) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 10_000_000:
        return dest
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(
        _url("-index.txt.bz2", host), headers={"User-Agent": UA}
    )
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
    tmp.replace(dest)
    return dest


def iter_index(path: Path = INDEX):
    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split(":", 2)
            if len(parts) != 3:
                continue
            off, _pageid, title = parts
            try:
                yield int(off), title
            except ValueError:
                continue


_TITLE_INDEX: dict[str, int] | None = None
_TITLE_KEYS: list[str] | None = None


def build_title_dict(path: Path = INDEX) -> dict[str, int]:
    """一次扫描索引，建「标题 → 偏移」字典。之后查询走字典，不再逐行比对。"""
    idx: dict[str, int] = {}
    for off, title in iter_index(path):
        idx.setdefault(title, off)
    return idx


def load_title_dict(path: Path = INDEX, *, use_cache: bool = True) -> dict[str, int]:
    """带进程内缓存与磁盘缓存。磁盘缓存与索引同目录，索引更新后失效。"""
    global _TITLE_INDEX
    if use_cache and _TITLE_INDEX is not None:
        return _TITLE_INDEX
    cache = path.with_name(path.stem.replace(".txt", "") + "_titles.tsv")
    if use_cache and cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
        idx: dict[str, int] = {}
        with cache.open(encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t", 1)
                if len(parts) == 2 and parts[1]:
                    idx[parts[1]] = int(parts[0])
        _TITLE_INDEX = idx
        return idx
    idx = build_title_dict(path)
    if use_cache:
        with cache.open("w", encoding="utf-8") as f:
            for title, off in idx.items():
                f.write(f"{off}\t{title}\n")
    _TITLE_INDEX = idx
    return idx


def find_titles(needles: list[str], path: Path = INDEX, limit: int = 8) -> dict[str, list[tuple[int, str]]]:
    """按标题精确查找。只做同名匹配，不做子串回退。

    子串匹配会把「非洲」配到「非洲某模板页」这类无关条目，
    而且需要对每个未命中词全表扫描，量大时极慢。
    """
    out: dict[str, list[tuple[int, str]]] = {n: [] for n in needles}
    if not needles:
        return out
    idx = load_title_dict(path)
    for n in needles:
        off = idx.get(n)
        if off is not None:
            out[n].append((off, n))
    return out


def wikitext_to_plain(text: str) -> str:
    t = _COMMENT.sub(" ", text)
    t = _REF.sub(" ", t)
    t = _FILELINK.sub(" ", t)
    t = _TEMPLATE.sub(" ", t)
    t = _EXT_LINK.sub(" ", t)
    t = _WIKILINK.sub(lambda m: m.group(2) or m.group(1), t)
    t = _TAG.sub(" ", t)
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"^\s*[|!{}].*$", " ", t, flags=re.M)
    t = re.sub(r"[ \t]+", " ", t)
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())


BAD_NS = (
    "Template:", "模板:", "File:", "文件:", "Image:", "Category:", "分类:",
    "Wikipedia:", "维基百科:", "Portal:", "主题:", "Help:", "帮助:", "Draft:",
    "User:", "用户:", "Talk:", "讨论:", "Module:", "模块:", "Special:",
)


def is_article_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    if any(t.startswith(ns) for ns in BAD_NS):
        return False
    if re.match(r"^[A-Z][a-z]+:", t) and "·" not in t:
        return False
    return True


def fetch_page(title: str, offset: int, host: str = "umu", span: int = 1_500_000) -> dict:
    """按偏移取一段压缩块并解压，截出目标条目。"""
    url = _url(".xml.bz2", host)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Range": f"bytes={offset}-{offset + span - 1}",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
    dec = bz2.BZ2Decompressor()
    try:
        text = dec.decompress(raw).decode("utf-8", "replace")
    except (OSError, EOFError):
        text = dec.decompress(raw, max_length=0).decode("utf-8", "replace")
    start = text.find("<title>" + title + "</title>")
    if start < 0:
        start = text.find(">" + title + "<")
    if start < 0:
        return {"title": title, "offset": offset, "error": "page_not_in_chunk"}
    end = text.find("</page>", start)
    block = text[start:end if end > 0 else start + 200_000]
    m = re.search(r"<text[^>]*>(.*?)</text>", block, re.S)
    wikitext = m.group(1) if m else ""
    if not wikitext:
        return {"title": title, "offset": offset, "error": "no_wikitext"}
    wikitext = (
        wikitext.replace("&lt;", "<").replace("&gt;", ">")
        .replace("&amp;", "&").replace("&quot;", '"')
    )
    redirect = ""
    rm = re.match(r"\s*#(?:REDIRECT|重定向)\s*:?\s*\[\[([^\]|#]+)", wikitext, re.I)
    if rm:
        redirect = rm.group(1).strip()
    plain = wikitext_to_plain(wikitext)
    return {
        "title": title,
        "offset": offset,
        "plain": plain,
        "chars": len(plain),
        "redirect": redirect,
    }


def _first_sentence(plain: str) -> str:
    """取导语首句。跳过信息框残行和列表行，优先含括号注音的长句。"""
    candidates: list[str] = []
    for line in plain.splitlines():
        s = line.strip()
        if len(s) < 24:
            continue
        if s.startswith(("{{", "|", "}", "==", "*", "**", "#", "[[", "!",
                         ">", "0", "2")):
            continue
        if re.fullmatch(r"[\d\s\-:TZ\">*.]+", s):
            continue
        if "日期" in s[:12] and len(s) < 40:
            continue
        candidates.append(s)
        if len(candidates) >= 6:
            break
    if not candidates:
        return ""
    for s in candidates:
        if "（" in s and "）" in s and "，" in s:
            m = re.search(r"[。！？]", s)
            return s[: m.end()] if m else s[:200]
    s = candidates[0]
    m = re.search(r"[。！？]", s)
    return s[: m.end()] if m else s[:200]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="中文维基离线取条目")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="按标题取条目")
    f.add_argument("titles", nargs="+")
    f.add_argument("--host", default="umu", choices=sorted(DUMPS))
    f.add_argument("--span", type=int, default=1_500_000)
    s = sub.add_parser("stat", help="查看索引与缓存")
    s.add_argument("--host", default="umu", choices=sorted(DUMPS))
    a = p.parse_args(argv)

    if a.cmd == "stat":
        path = download_index(a.host)
        n = sum(1 for _ in iter_index(path))
        cached = 0
        if CACHE.is_file():
            cached = sum(1 for _ in CACHE.open(encoding="utf-8"))
        print(json.dumps({"index": str(path), "index_bytes": path.stat().st_size,
                          "index_lines": n, "cached_pages": cached}, ensure_ascii=False))
        return 0

    path = download_index(a.host)
    hits = find_titles(a.titles, path)
    rows = []
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("a", encoding="utf-8") as out:
        for needle, found in hits.items():
            if not found:
                print(f"[miss] {needle}")
                continue
            off, title = found[0]
            print(f"[hit] {needle} -> {title} @ {off}")
            try:
                page = fetch_page(title, off, a.host, a.span)
            except Exception as exc:
                print(f"  error {type(exc).__name__}: {exc}")
                continue
            page["first_sentence"] = _first_sentence(page.get("plain") or "")
            page["source_url"] = f"https://zh.wikipedia.org/wiki/{title}"
            rows.append(page)
            out.write(json.dumps({k: page.get(k) for k in
                                  ("title", "offset", "chars", "first_sentence", "source_url")},
                                 ensure_ascii=False) + "\n")
            print(f"  chars={page.get('chars')} first={page.get('first_sentence')[:80]}")
    print(json.dumps({"fetched": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
