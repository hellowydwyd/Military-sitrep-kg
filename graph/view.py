"""事件图谱的实用视图。

页面在 graph/view.html。这里只读图谱、填占位符，写出一份自包含的静态页。

    python3 -m graph.view
    python3 -m graph.view --src data/graph/sitrep.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TEMPLATE = Path(__file__).with_name("view.html")

# 参与「谁和谁互动」的角色。页面里用 __(ACTOR_ROLES)__ 引用。
ACTOR_ROLES = (
    "打击方", "受击方", "拦截方", "袭击方", "控制方", "发射方",
    "部署方", "表态方", "甲方", "乙方", "援助方", "受援方", "施动方", "主体",
)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="图谱实用视图")
    ap.add_argument("--src", default=str(DATA / "graph" / "sitrep.json"))
    ap.add_argument("--out", default=str(DATA / "graph" / "sitrep.html"))
    a = ap.parse_args(argv)

    graph = json.loads(Path(a.src).read_text(encoding="utf-8"))
    for e in graph["edges"]:
        e.pop("evidence", None)

    html = TEMPLATE.read_text(encoding="utf-8")
    html = (html
            .replace("__(TITLE)__", "军事事件图谱")
            .replace("__(DATA)__", json.dumps(graph, ensure_ascii=False))
            .replace("__(ACTOR_ROLES)__", json.dumps(list(ACTOR_ROLES), ensure_ascii=False)))
    leftover = [k for k in ("__(TITLE)__", "__(DATA)__", "__(ACTOR_ROLES)__") if k in html]
    if leftover:
        raise SystemExit(f"页面仍有未替换的占位符：{', '.join(leftover)}")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已生成 {out}（{out.stat().st_size/1024/1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
