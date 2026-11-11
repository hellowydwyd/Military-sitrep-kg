"""python -m annotate [--host 0.0.0.0] [--port 8767]

三任务标注服务：NER / EE / RE 分卷标注。
"""

from __future__ import annotations

import argparse

from annotate.task_server import run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="三任务标注服务")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8767)
    a = p.parse_args(argv)
    run(a.host, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
