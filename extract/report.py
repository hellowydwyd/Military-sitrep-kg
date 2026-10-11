"""抽取验收报告。"""

from __future__ import annotations

from pathlib import Path


def build_report(rows: list[dict], *, sample_limit: int = 0) -> str:
    n_doc = len(rows)
    n_chunk = sum(r.get("n_chunks") or 0 for r in rows)
    n_tri = sum(len(c.get("triples") or []) for r in rows for c in r.get("chunks") or [])
    n_ev = sum(len(c.get("events") or []) for r in rows for c in r.get("chunks") or [])
    n_drop_t = sum(len(c.get("dropped_triples") or []) for r in rows for c in r.get("chunks") or [])
    n_drop_e = sum(len(c.get("dropped_events") or []) for r in rows for c in r.get("chunks") or [])
    empty = sum(1 for r in rows if not any((c.get("triples") or c.get("events")) for c in r.get("chunks") or []))
    n_err = sum(1 for r in rows if r.get("error"))
    shown = rows if not sample_limit else rows[:sample_limit]
    lines = [
        "# OpenIE 抽取报告",
        "",
        f"- 文档 **{n_doc}** 篇，切块 **{n_chunk}**，失败 **{n_err}**",
        f"- 保留开放事件 **{n_ev}**（派生三元组若存在：{n_tri}）",
        f"- 校验丢弃：事件 {n_drop_e}，三元组 {n_drop_t}",
        f"- 抽空文档：{empty}",
        "",
        f"## 样本（最多 {len(shown)} 篇，每篇最多 4 个事件）",
        "",
    ]
    for i, row in enumerate(shown, 1):
        lines.append(f"### {i}. {row.get('title')}")
        lines.append("")
        lines.append(f"- 时间：`{row.get('publish_time')}`")
        lines.append(f"- URL：{row.get('url')}")
        tris, evs = [], []
        for c in row.get("chunks") or []:
            tris.extend(c.get("triples") or [])
            evs.extend(c.get("events") or [])
        if not tris and not evs:
            lines.append("- （无保留抽取）")
        for t in tris[:4]:
            lines.append(f"- 三元组：`{t['head']}` — **{t['relation']}** → `{t['tail']}`"
                         + (f"  ({t['t_start']})" if t.get("t_start") else ""))
        for e in evs[:2]:
            args = "；".join(f"{a.get('role')}={a.get('text')}" for a in e.get("arguments") or [])
            lines.append(f"- 事件：trigger=`{e.get('trigger')}` {args}")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_report(rows: list[dict], path: Path, *, sample_limit: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(rows, sample_limit=sample_limit), encoding="utf-8")
    return path
