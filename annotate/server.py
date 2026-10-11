"""stdlib HTTP 服务：静态页 + 标注 API。"""

from __future__ import annotations

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from annotate import iaa as iaa_mod
from annotate import store

STATIC = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
PORT = 8765


def _json_bytes(obj: Any, code: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return code, body, "application/json; charset=utf-8"


class Handler(BaseHTTPRequestHandler):
    server_version = "SitrepAnnotate/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[annotate] {self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except Exception as exc:
            traceback.print_exc()
            code, body, ctype = _json_bytes({"ok": False, "error": str(exc)}, 500)
            self._send(code, body, ctype)

    def do_PUT(self) -> None:
        try:
            self._dispatch_put()
        except Exception as exc:
            traceback.print_exc()
            code, body, ctype = _json_bytes({"ok": False, "error": str(exc)}, 500)
            self._send(code, body, ctype)

    def do_POST(self) -> None:
        try:
            self._dispatch_post()
        except Exception as exc:
            traceback.print_exc()
            code, body, ctype = _json_bytes({"ok": False, "error": str(exc)}, 500)
            self._send(code, body, ctype)

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _annotator(self) -> str:
        q = self._query()
        a = (q.get("annotator") or ["A"])[0]
        return a.strip().upper() or "A"

    def _dispatch_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path == "/iaa.html":
            return self._static("iaa.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/") :])
        if path in ("/app.js", "/style.css"):
            return self._static(path.lstrip("/"))

        if path == "/api/queue":
            code, body, ctype = _json_bytes(store.queue(self._annotator()))
            return self._send(code, body, ctype)

        if path.startswith("/api/chunk/"):
            cid = path[len("/api/chunk/") :]
            cid = cid.split("?")[0]
            try:
                data = store.get_chunk(cid, self._annotator())
            except KeyError:
                code, body, ctype = _json_bytes({"ok": False, "error": "unknown chunk"}, 404)
                return self._send(code, body, ctype)
            code, body, ctype = _json_bytes(data)
            return self._send(code, body, ctype)

        if path == "/api/iaa":
            code, body, ctype = _json_bytes(iaa_mod.compute_iaa())
            return self._send(code, body, ctype)

        code, body, ctype = _json_bytes({"ok": False, "error": "not found"}, 404)
        self._send(code, body, ctype)

    def _dispatch_put(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/chunk/"):
            code, body, ctype = _json_bytes({"ok": False, "error": "not found"}, 404)
            return self._send(code, body, ctype)
        cid = path[len("/api/chunk/") :]
        payload = self._read_json()
        result = store.save_chunk(
            cid,
            self._annotator(),
            events=payload.get("events") or [],
            notes=str(payload.get("notes") or ""),
            status=str(payload.get("status") or "done"),
        )
        http_code = 200 if result.get("ok") else 400
        code, body, ctype = _json_bytes(result, http_code)
        self._send(code, body, ctype)

    def _dispatch_post(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/export":
            code, body, ctype = _json_bytes({"ok": False, "error": "not found"}, 404)
            return self._send(code, body, ctype)
        # body optional
        try:
            self._read_json()
        except Exception:
            pass
        out = store.export_gold(self._annotator())
        code, body, ctype = _json_bytes({"ok": True, "path": str(out)})
        self._send(code, body, ctype)

    def _static(self, name: str) -> None:
        # prevent path escape
        name = name.lstrip("/").replace("..", "")
        path = STATIC / name
        if not path.is_file():
            code, body, ctype = _json_bytes({"ok": False, "error": "missing static"}, 404)
            return self._send(code, body, ctype)
        data = path.read_bytes()
        ctype = "text/plain; charset=utf-8"
        if name.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif name.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif name.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        self._send(200, data, ctype)


def run(host: str = HOST, port: int = PORT) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"标注前端: http://{host}:{port}/")
    print(f"IAA 页:   http://{host}:{port}/iaa.html")
    print("Ctrl+C 退出")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.server_close()
