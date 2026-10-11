"""python -m annotate [--host 127.0.0.1] [--port 8765]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 保证仓库根在 path 上
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from annotate.server import run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="开放事件标注前端（P1 试点）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
