"""Time-CoT：只基于证据包写简报，再做引用校验。"""

from __future__ import annotations

import json
from typing import Any

from extract.llm import chat_json
from reason.pack import serialize_pack
from reason.validate import validate_brief

SYSTEM = """你是军事新闻态势简报员。只根据给定证据包写简报，不要使用包外知识。
禁止因果（导致/造成了）、禁止把单源报道写成已确认或已核实。
禁止补原文没有的数量、型号、意图。
每条事实句必须是 Markdown 无序列表项，句末引用格式必须是 [8位id|chunk_id]，例如 [85e55f9f|2e5e3e0593fb_00]。不要写成 [cite: ...]。
输出 Markdown，不要代码围栏。"""


def _user_prompt(serialized: dict[str, Any]) -> str:
    payload = json.dumps(serialized, ensure_ascii=False, indent=2)
    day = serialized.get("day")
    return f"""查询日：{day}
任务：根据证据包回答「{day} 发生了什么」。

写法：
1. 先一行说明：据环球网报道主张整理，single_source，不得视为已核实事实。
2. 按地点或主题分组（## 小标题）。没有地点就按报道标题分组。
3. 每个事件最多一句；可合并同主题，但必须列出所有用到的 [cite]。
4. 引用写成 [cite字段的值]，不要加 cite: 字样。正确：[85e55f9f|2e5e3e0593fb_00]
5. 只根据每条的 evidence / raw_time 写时间。evidence 里有的「24日」「8月7日」可以写；不要写 evidence 没有的日期。raw_time 为「随后/日前/上周」时照抄原文，不要换成查询日。
6. 部署/任职等若证据未写明当天开始，不要写成当天才开始。
7. 只写 items 里的条目。published_without_event 是当日见报但事件日不在本日的稿，不要写进简报正文。
8. 禁止写「导致」。

证据包 JSON：
{payload}
"""


def _retry_prompt(serialized: dict[str, Any], report: dict[str, Any]) -> str:
    dropped = "\n".join(f"- {x}" for x in (report.get("dropped") or [])[:12]) or "（无）"
    return (
        _user_prompt(serialized)
        + "\n\n上次输出未通过校验，请重写全文。只保留 cite 能在证据包中找到的列表项。"
        + f"\n被丢掉的句子：\n{dropped}\n"
    )


def render_meta(day: str, report: dict[str, Any], retried: bool) -> str:
    status = "通过" if report.get("ok") else "未过 0.9 线，已删除不合格句"
    return "\n".join(
        [
            f"# {day} 态势简报（Time-CoT）",
            "",
            f"- 校验：{status}；保留 {report.get('n_claim_kept')} / {report.get('n_claim')} 句；"
            f"引用命中 {report.get('n_cite_ok')} / {report.get('n_cite')}；"
            f"cite_rate={report.get('cite_rate')}；重试={'是' if retried else '否'}",
            "- 仅环球网主张，不得写成已确认。",
            "",
        ]
    )


def generate_brief(pack: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    serialized = serialize_pack(pack)
    if not serialized.get("items"):
        day = str(pack.get("day") or "")
        empty = f"# {day} 态势简报（Time-CoT）\n\n- 证据包为空，证据不足。\n"
        return empty, {"ok": True, "n_claim": 0, "n_claim_kept": 0, "cite_rate": 1.0, "dropped": []}

    raw = chat_json(SYSTEM, _user_prompt(serialized), timeout=180)
    body, report = validate_brief(raw, serialized)
    retried = False
    if not report.get("ok"):
        retried = True
        raw = chat_json(SYSTEM, _retry_prompt(serialized, report), timeout=180)
        body, report = validate_brief(raw, serialized)
    day = str(serialized.get("day") or "")
    text = render_meta(day, report, retried) + body
    if report.get("dropped"):
        text += "\n## 校验删除\n\n"
        for d in report["dropped"][:12]:
            text += f"- {d}\n"
        if report.get("n_claim_kept") == 0:
            text += "\n证据不足：改写后仍无合格引用句，请对照程序清单。\n"
    report["retried"] = retried
    return text, report
