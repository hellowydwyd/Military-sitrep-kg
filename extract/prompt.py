"""开放事件抽取：槽位固定，trigger 开放。不抽独立三元组、不换算日历日。"""

from __future__ import annotations

import json
import re
from typing import Any

SYSTEM = """你是军事新闻开放事件抽取器。只根据给定文本抽取，不要编造原文没有的事实。
不要使用预定义的事件类型表（如空袭/演习/冲突）。trigger 用原文中的动作词语。
论元角色名只能是：主体、对象、地点、装备。
输出必须是一个 JSON 对象，不要 Markdown 围栏。"""

SYSTEM_EN = """You extract open events from military news. Use only the given text; do not invent facts.
Do not use a closed event-type list. The trigger must be a contiguous verb/phrase copied from the source text.
Argument role names must be the Chinese labels 主体, 对象, 地点, 装备.
kind must be one of 发生, 表态, 计划.
Return one JSON object, no Markdown fence."""

USER_TMPL = """从下面新闻块中抽取开放事件。不要输出 triples。

约束：
1. trigger、论元 text、evidence、raw_time（非空时）必须是正文或标题中的连续子串。不要用「他/其/该部队」等代词当实体；指示词「该建筑」仅当句中没有可截的名词时才允许。
2. 一句一事一时：同一句、同一主体只保留一条事件，不要把同一句拆成打击/开展/拉拢等多条。评论、分析不要拆成多条发生类事件。
   **例外**：原文用「随后 / 之后 / 接着 / 然后」连接的两个动作必须抽成两条，raw_time 分别保留线索词（如一条「当天」、一条「随后」）。
3. raw_time 只填 evidence 句里的原文时间短语（「19日」「8月17日」「上周」「日前」「近日」「当天」「同日」「今天」「昨日」「随后」「之后」「今年1月」等）。必须原样抄写，禁止留空（句中有时间时）、禁止只放进论元、禁止写成 YYYY-MM-DD。没有时间短语才填 ""。禁止写「空」「无」。
4. 每条事件必须带 evidence：包含 trigger 的那一句（可截断到 80 字）。
5. kind 只能是 发生 / 表态 / 计划：
   - 发生：打击、发现、部署、发射、空袭、逮捕、相撞、仍驻扎等事态（含持续状态）。
   - 表态：称、表示、谴责、警告、否认等口头口径。警告/谴责的内容即使是「将打击」，kind 仍是表态。
   - 计划：主谓语是将、拟、计划、准备。
   「据通报称……实施了打击」一句一事只留打击（发生），不要另标「称」。
6. 论元 role 只能是 主体 / 对象 / 地点 / 装备。同角色可多条。没有的槽不写。不要写角色「时间」「目的」「内容」。
7. 原文没有的数量、型号、意图不要写。没有可抽的就给空数组。

输出 JSON schema：
{{
  "events": [
    {{"trigger": "触发词", "raw_time": "原文时间短语或空字符串", "kind": "发生|表态|计划", "evidence": "含触发词的原句", "arguments": [{{"role": "主体|对象|地点|装备", "text": "原文子串"}}]}}
  ]
}}

新闻块：
{block}
"""

USER_TMPL_EN = """Extract open events from the news block below. Do not output triples.

Constraints:
1. trigger, argument text, evidence, and raw_time (when non-empty) MUST be contiguous substrings of the title or body. Do not use pronouns (he/they/it/the latter) as entities.
2. One sentence, one event, one time. Do not split one sentence into multiple 发生 events. Do not turn commentary into extra 发生 events.
   Exception: if the text links two actions with then / afterwards / subsequently / after that, extract two events; keep those cue words in raw_time.
3. raw_time is the time phrase in the evidence sentence (Tuesday, Oct. 7, today, overnight, this week, earlier today). Copy it verbatim. Do not write YYYY-MM-DD. If there is no time phrase, use "".
4. Each event needs evidence: the source sentence that contains the trigger (truncate to ~80 characters if needed).
5. kind is only 发生 / 表态 / 计划:
   - 发生: strikes, launches, deployments, intercepts, arrests, ongoing presence.
   - 表态: said, told, warned, condemned, denied, claimed. If the speech is "will strike", kind is still 表态.
   - 计划: will, plans to, is preparing to as the main predicate.
   For "said that X struck Y", keep the strike as 发生; do not also extract said.
6. role is only 主体 / 对象 / 地点 / 装备. Multiple args per role are allowed. Omit empty slots. Do not use roles 时间 / 目的 / 内容.
7. Do not invent counts, model names, or intent. If nothing is extractable, return an empty array.

JSON schema:
{{
  "events": [
    {{"trigger": "verb from text", "raw_time": "time phrase or empty string", "kind": "发生|表态|计划", "evidence": "source sentence containing the trigger", "arguments": [{{"role": "主体|对象|地点|装备", "text": "contiguous source span"}}]}}
  ]
}}

News block:
{block}
"""


def system_for(lang: str) -> str:
    return SYSTEM_EN if (lang or "").startswith("en") else SYSTEM


def build_user_prompt(prompt_text: str, lang: str = "") -> str:
    tmpl = USER_TMPL_EN if (lang or "").startswith("en") else USER_TMPL
    return tmpl.format(block=prompt_text)


def parse_llm_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {"events": []}
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {"events": []}
    if not isinstance(data, dict):
        return {"events": []}
    data.setdefault("events", [])
    if not isinstance(data["events"], list):
        data["events"] = []
    return data
