"""十类图谱查询服务：单图常驻内存，按节点取邻域。

不做离线按月切片。全量图 5 万节点、4.6 万边，按月切会把高连接节点的关系切散，
这正是前端关系显示不全的原因。这里一次载入全图，按请求返回需要的部分。

    python3 -m graph.serve10 --port 8766

接口：
  GET /api/summary                 总览与类型分布
  GET /api/events?limit=200        事件列表（可搜索）
  GET /api/node?id=<节点编号>       单节点及其全部边与邻居
  GET /api/search?q=<关键词>        按名称或别名搜索节点
  GET /                              静态页面（theme/web）
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GRAPH = DATA / "graph" / "sitrep10_full.json"
WEB = ROOT / "theme" / "web"

STATE: dict = {}


def load(path: Path = GRAPH) -> dict:
    """载入图并建索引。索引常驻内存，单次请求只做查表与切片。"""
    g = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}
    docs = g.get("docs") or {}
    out_edges: dict[str, list[dict]] = defaultdict(list)
    in_edges: dict[str, list[dict]] = defaultdict(list)
    for e in g["edges"]:
        out_edges[e["from"]].append(e)
        in_edges[e["to"]].append(e)
    deg = Counter()
    for e in g["edges"]:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    by_type: dict[str, list[str]] = defaultdict(list)
    for nid, n in nodes.items():
        by_type[n.get("type") or ""].append(nid)
    # 名称与别名索引，供搜索
    name_index: dict[str, set[str]] = defaultdict(set)
    for nid, n in nodes.items():
        nm = (n.get("name") or "").strip()
        if nm:
            name_index[nm].add(nid)
            for a in n.get("aliases") or []:
                if a:
                    name_index[a].add(nid)
    events = [nid for nid in by_type.get("事件", []) if deg.get(nid)]
    events.sort(key=lambda nid: -deg[nid])
    return {
        "graph": g,
        "nodes": nodes,
        "docs": docs,
        "out": out_edges,
        "in": in_edges,
        "deg": deg,
        "by_type": by_type,
        "name_index": name_index,
        "events": events,
        "summary": {
            "n_nodes": len(nodes),
            "n_edges": len(g["edges"]),
            "n_docs": len(docs),
            "by_type": {t: len(v) for t, v in by_type.items()},
            "isolated": sum(1 for nid in nodes if not deg.get(nid)),
        },
    }


def _edge_view(e: dict, doc: dict, other_id: str, nodes: dict) -> dict:
    other = nodes.get(other_id) or {}
    return {
        "rel": e.get("rel") or "",
        "other": other_id,
        "other_name": other.get("name") or other_id,
        "other_type": other.get("type") or "",
        "direction": "out" if e.get("from") == other_id else "in",
        "evidence": (e.get("evidence") or "")[:400],
        "time": e.get("time") or "",
        "doc": e.get("doc") or "",
        "title": (doc.get("title") or "")[:100],
        "url": doc.get("url") or "",
        "dest_kind": e.get("dest_kind") or "",
        "derived": bool(e.get("derived")),
    }


def node_payload(nid: str, limit: int = 0) -> Optional[dict]:
    st = STATE
    n = st["nodes"].get(nid)
    if not n:
        return None
    nodes = st["nodes"]
    docs = st["docs"]
    rows = []
    for e in st["out"].get(nid, []):
        rows.append(_edge_view(e, docs.get(e.get("doc") or "") or {}, e["to"], nodes))
    for e in st["in"].get(nid, []):
        rows.append(_edge_view(e, docs.get(e.get("doc") or "") or {}, e["from"], nodes))
    rows.sort(key=lambda r: (len(r["evidence"]), r["time"]), reverse=True)
    total = len(rows)
    if limit and total > limit:
        rows = rows[:limit]
    return {
        "node": {
            "id": nid,
            "name": n.get("name") or nid,
            "type": n.get("type") or "",
            "subtype": n.get("subtype") or "",
            "aliases": (n.get("aliases") or [])[:20],
            "doc_count": n.get("doc_count") or 0,
            "degree": st["deg"].get(nid, 0),
        },
        "edges": rows,
        "total": total,
        "returned": len(rows),
    }


def event_payload(nid: str) -> Optional[dict]:
    """事件视图：按十类分栏列出该事件的扇出。"""
    st = STATE
    n = st["nodes"].get(nid)
    if not n or n.get("type") != "事件":
        return None
    nodes = st["nodes"]
    docs = st["docs"]
    facets: dict[str, list[dict]] = defaultdict(list)
    for e in st["in"].get(nid, []):
        facets[e.get("rel") or ""].append(_edge_view(e, docs.get(e.get("doc") or "") or {}, e["from"], nodes))
    for e in st["out"].get(nid, []):
        facets[e.get("rel") or ""].append(_edge_view(e, docs.get(e.get("doc") or "") or {}, e["to"], nodes))
    return {
        "event": {
            "id": nid,
            "name": n.get("name") or nid,
            "kind": n.get("kind") or "",
            "doc_count": n.get("doc_count") or 0,
            "degree": st["deg"].get(nid, 0),
        },
        "facets": {k: v[:30] for k, v in facets.items()},
    }


def search_payload(q: str, limit: int = 30) -> dict:
    st = STATE
    q = (q or "").strip()
    if not q:
        return {"q": q, "items": []}
    hits: dict[str, int] = {}
    # 精确与包含匹配
    for name, ids in st["name_index"].items():
        if q == name:
            for nid in ids:
                hits[nid] = max(hits.get(nid, 0), 3)
        elif q in name:
            for nid in ids:
                hits[nid] = max(hits.get(nid, 0), 2)
    items = []
    for nid, score in hits.items():
        n = st["nodes"][nid]
        items.append({
            "id": nid,
            "name": n.get("name") or nid,
            "type": n.get("type") or "",
            "degree": st["deg"].get(nid, 0),
            "score": score,
        })
    items.sort(key=lambda r: (-r["score"], -r["degree"]))
    return {"q": q, "items": items[:limit], "total": len(items)}


def events_payload(limit: int = 200, q: str = "") -> dict:
    st = STATE
    ids = st["events"]
    if q:
        ids = [i for i in ids if q in (st["nodes"][i].get("name") or "")]
    items = []
    for nid in ids[:limit]:
        n = st["nodes"][nid]
        items.append({
            "id": nid,
            "name": n.get("name") or nid,
            "kind": n.get("kind") or "",
            "degree": st["deg"].get(nid, 0),
            "doc_count": n.get("doc_count") or 0,
        })
    return {"items": items, "total": len(ids)}


class Handler(BaseHTTPRequestHandler):
    server_version = "sitrep10/1.0"

    def log_message(self, fmt, *args):  # 静音访问日志
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        p = (WEB / path.lstrip("/")).resolve()
        if not str(p).startswith(str(WEB.resolve())) or not p.is_file():
            self.send_error(404)
            return
        body = p.read_bytes()
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        # 查询串按 UTF-8 解码，避免中文参数被当成 latin1
        qs = parse_qs(u.query, encoding="utf-8", errors="replace")
        path = u.path

        if path == "/api/summary":
            self._json({"summary": STATE["summary"], "years": "2026-01-01..2026-11-03"})
            return
        if path == "/api/node":
            nid = (qs.get("id") or [""])[0]
            limit = int((qs.get("limit") or ["0"])[0] or 0)
            payload = node_payload(nid, limit)
            if not payload:
                self._json({"error": "node_not_found", "id": nid}, 404)
                return
            self._json(payload)
            return
        if path == "/api/event":
            nid = (qs.get("id") or [""])[0]
            payload = event_payload(nid)
            if not payload:
                self._json({"error": "event_not_found", "id": nid}, 404)
                return
            self._json(payload)
            return
        if path == "/api/events":
            limit = int((qs.get("limit") or ["200"])[0] or 200)
            q = (qs.get("q") or [""])[0]
            self._json(events_payload(limit, q))
            return
        if path == "/api/search":
            q = (qs.get("q") or [""])[0]
            self._json(search_payload(q))
            return
        if path in ("/", ""):
            self._static("g10.html")
            return
        self._static(path)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="十类图谱查询服务")
    p.add_argument("--src", default=str(GRAPH))
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--host", default="0.0.0.0")
    a = p.parse_args(argv)

    print(f"载入 {a.src} …", flush=True)
    STATE.update(load(Path(a.src)))
    s = STATE["summary"]
    print(f"节点 {s['n_nodes']} 边 {s['n_edges']} 文档 {s['n_docs']} 孤立 {s['isolated']}", flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"服务 http://{a.host}:{a.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("停止", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
