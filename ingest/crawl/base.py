"""限速 HTTP、正文清洗。"""

from __future__ import annotations

import random
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "MilitarySitrepKG/0.1 (+local-research; polite crawler; "
    "source=huanqiu_mil)"
)
DEFAULT_DELAY = 2.0


class PoliteSession:
    def __init__(self, delay: float = DEFAULT_DELAY, timeout: float = 20.0):
        self.delay = delay
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            }
        )
        self._last_by_host: dict[str, float] = {}
        self._robots: dict[str, Optional[RobotFileParser]] = {}

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_by_host.get(host, 0.0)
        wait = self.delay + random.uniform(0.0, 0.4) - (time.time() - last)
        if wait > 0:
            time.sleep(wait)

    def robots_ok(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            robots_url = origin + "/robots.txt"
            try:
                r = self._session.get(robots_url, timeout=10)
                if r.status_code == 200 and r.text:
                    rp.parse(r.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None
            except requests.RequestException:
                self._robots[origin] = None
        rp = self._robots.get(origin)
        if rp is None:
            return True
        return rp.can_fetch(USER_AGENT, url)

    def get(
        self,
        url: str,
        *,
        json_mode: bool = False,
        retries: int = 3,
        headers: Optional[dict] = None,
    ) -> requests.Response:
        if not self.robots_ok(url):
            raise PermissionError(f"robots.txt 禁止: {url}")
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            self._wait(url)
            try:
                resp = self._session.get(url, timeout=self.timeout, headers=headers)
                self._last_by_host[urlparse(url).netloc] = time.time()
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep((2**attempt) + random.uniform(0.2, 0.8))
                    last_err = RuntimeError(f"HTTP {resp.status_code}")
                    continue
                resp.raise_for_status()
                if json_mode:
                    resp.json()
                return resp
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_err = exc
                time.sleep((2**attempt) * 0.5)
        raise RuntimeError(f"请求失败 {url}: {last_err}")


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    paras = []
    nodes = soup.find_all(["p", "div"])
    if not nodes:
        text = soup.get_text("\n", strip=True)
        return _clean_lines(text.splitlines())
    for node in soup.find_all("p"):
        t = node.get_text(" ", strip=True)
        if not t:
            continue
        if t in ("相关阅读", "责任编辑", "版权声明"):
            break
        if t.startswith("责任编辑") or t.startswith("特别声明"):
            continue
        paras.append(t)
    if paras:
        return "\n".join(paras)
    return _clean_lines(soup.get_text("\n", strip=True).splitlines())


def _clean_lines(lines: list[str]) -> str:
    out = []
    for line in lines:
        t = " ".join(line.split())
        if not t:
            continue
        if t.startswith("责任编辑") or t in ("相关阅读",):
            break
        out.append(t)
    return "\n".join(out)
