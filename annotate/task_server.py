"""三任务标注服务：NER / EE / RE 分卷标注的网页界面。

    python3 -m annotate.task_server --port 8767

接口：
  GET  /api/volumes                     各卷篇数与已标进度
  GET  /api/next?task=&split=&annotator=   取下一篇待标稿件
  GET  /api/doc?task=&doc_id=&annotator=   按编号取指定稿件
  POST /api/judge                       提交判定
  GET  /api/stats                       双标覆盖率
  GET  /                                标注界面
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from annotate import tasks as taskmod

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"


def _json_bytes(obj, code: int = 200) -> tuple[int, bytes, str]:
    return code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8"


class Handler(BaseHTTPRequestHandler):
    server_version = "annotate/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query, encoding="utf-8", errors="replace")

    def do_GET(self):
        try:
            self._dispatch_get()
        except Exception as exc:
            traceback.print_exc()
            self._send(*_json_bytes({"ok": False, "error": str(exc)}, 500))

    def do_POST(self):
        try:
            self._dispatch_post()
        except Exception as exc:
            traceback.print_exc()
            self._send(*_json_bytes({"ok": False, "error": str(exc)}, 500))

    def _annotator(self) -> str:
        a = (self._query().get("annotator") or ["A"])[0]
        return (a or "A").strip().upper() or "A"

    def _dispatch_get(self) -> None:
        path = urlparse(self.path).path
        q = self._query()

        if path in ("/", "/index.html"):
            return self._static("task.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/") :])
        if path in ("/task.js", "/task.css"):
            return self._static(path.lstrip("/"))

        if path == "/api/volumes":
            vols = taskmod.list_volumes()
            ann = (q.get("annotator") or ["A"])[0].upper()
            done = {t: len(taskmod.load_verdicts(t, ann)) for t in taskmod.TASKS}
            return self._send(*_json_bytes({"volumes": vols, "done": done, "annotator": ann}))

        if path == "/api/next":
            task = (q.get("task") or ["ner"])[0]
            split = (q.get("split") or ["train"])[0]
            if task not in taskmod.TASKS:
                return self._send(*_json_bytes({"ok": False, "error": "bad task"}, 400))
            skip = set()
            for raw in q.get("skip") or []:
                skip.update(x for x in raw.split(",") if x)
            doc = taskmod.next_doc(task, split, self._annotator(), skip)
            rows = taskmod.load_volume(task, split)
            done = taskmod.load_verdicts(task, self._annotator())
            remaining = sum(1 for r in rows if r.get("doc_id") not in done)
            payload = doc or {"done": True}
            payload["remaining"] = remaining
            payload["total"] = len(rows)
            payload["done_count"] = len(rows) - remaining
            payload["skipped"] = len(skip)
            return self._send(*_json_bytes(payload))

        if path == "/api/doc":
            task = (q.get("task") or ["ner"])[0]
            doc_id = (q.get("doc_id") or [""])[0]
            split = (q.get("split") or ["train"])[0]
            for row in taskmod.load_volume(task, split):
                if row.get("doc_id") == doc_id:
                    return self._send(*_json_bytes(taskmod.view(task, row)))
            return self._send(*_json_bytes({"ok": False, "error": "not found"}, 404))

        if path == "/api/stats":
            return self._send(*_json_bytes(taskmod.stat()))

        self._send(*_json_bytes({"ok": False, "error": "not found"}, 404))

    def _remaining(self, task: str, split: str, annotator: str) -> int:
        rows = taskmod.load_volume(task, split)
        done = taskmod.load_verdicts(task, annotator)
        return sum(1 for r in rows if r.get("doc_id") not in done)

    def _dispatch_post(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/judge":
            return self._send(*_json_bytes({"ok": False, "error": "not found"}, 404))
        payload = self._read_json()
        task = str(payload.get("task") or "ner")
        doc_id = str(payload.get("doc_id") or "")
        annotator = str(payload.get("annotator") or "A").upper()
        items = payload.get("items") or []
        notes = str(payload.get("notes") or "")
        if task not in taskmod.TASKS or not doc_id:
            return self._send(*_json_bytes({"ok": False, "error": "bad request"}, 400))
        result = taskmod.save_verdict(task, annotator, doc_id, items, notes)
        self._send(*_json_bytes(result))

    def _static(self, name: str) -> None:
        name = name.lstrip("/").replace("..", "")
        p = STATIC / name
        if not p.is_file():
            return self._send(*_json_bytes({"ok": False, "error": "missing"}, 404))
        body = p.read_bytes()
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, body, ctype)


def run(host: str = "0.0.0.0", port: int = 8767) -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"三任务标注: http://{host}:{port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="三任务标注服务")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8767)
    a = p.parse_args(argv)
    run(a.host, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
