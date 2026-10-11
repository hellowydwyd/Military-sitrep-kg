"""把本轮抓取结果写成可打开的验收报告。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ingest.envelope import NewsDoc

CST = timezone(timedelta(hours=8))


def _preview(text: str, n: int = 160) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def build_report(docs: list[NewsDoc], *, notes: list[str] | None = None) -> str:
    now = datetime.now(CST).replace(microsecond=0).isoformat()
    by_src = Counter(d.source for d in docs)
    complete = [d for d in docs if not d.missing_fields()]
    rate = (len(complete) / len(docs)) if docs else 0.0
    lines = [
        "# 爬虫冒烟验收报告",
        "",
        f"- 生成时间：`{now}`",
        f"- 本轮入库：**{len(docs)}** 篇（完整 {len(complete)}，字段完整率 **{rate:.0%}**）",
        f"- 分源：{dict(by_src) if by_src else '{}'}",
        "",
        "## 过关线（本轮）",
        "",
        "| 项 | 要求 | 结果 |",
        "|----|------|------|",
        f"| 篇数 | ≥ 5 | {'通过' if len(docs) >= 5 else '未通过'}（{len(docs)}） |",
        f"| 字段完整率 | ≥ 90% | {'通过' if rate >= 0.9 else '未通过'}（{rate:.0%}） |",
        f"| 新鲜度 | 至少 1 篇近 7 日稿 | {_fresh_label(docs)} |",
        "",
    ]
    if notes:
        lines.append("## 说明")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    lines.append("## 样本（每篇：标题 / 时间 / 链接 / 正文前 160 字）")
    lines.append("")
    for i, doc in enumerate(docs, 1):
        miss = doc.missing_fields()
        flag = "完整" if not miss else "缺字段: " + ",".join(miss)
        lines.append(f"### {i}. [{doc.source_name}] {doc.title or '(无标题)'}")
        lines.append("")
        lines.append(f"- 状态：{flag}")
        lines.append(f"- 语言：`{doc.lang}`")
        lines.append(f"- 时间：`{doc.publish_time or 'unknown'}`")
        lines.append(f"- 字数：{doc.body_chars}")
        lines.append(f"- URL：{doc.url}")
        lines.append(f"- 正文：{_preview(doc.body)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _fresh_label(docs: list[NewsDoc]) -> str:
    dated = [d for d in docs if d.publish_time]
    if not dated:
        return "未通过（0 篇）"
    newest = max(d.publish_time for d in dated)
    return f"通过（最新 `{newest}`）"


def _huanqiu_fresh_label(docs: list[NewsDoc]) -> str:
    return _fresh_label([d for d in docs if d.source == "huanqiu_mil"])


def write_report(docs: list[NewsDoc], path: Path, *, notes: list[str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_report(docs, notes=notes)
    path.write_text(text, encoding="utf-8")
    return path
