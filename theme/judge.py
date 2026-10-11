"""查询窗结构判定：向量候选之后，LLM 决定并入 / 单立，不自由发明几何。"""

from __future__ import annotations

import json
import re

from theme.name import sample_titles
from theme.theaters import cannot_link

SYSTEM = (
    "你是军事新闻议题编辑。输入是时间窗内的候选报道团。"
    "你只做结构判定：哪些团是同一议题应合并，哪些应单独成题。"
    "规则："
    "1. 明显不同战场不要合并：俄乌、美伊/霍尔木兹、胡塞/也门、黎南/真主党、加沙/哈马斯、日本、南海/菲律宾、朝鲜半岛、非洲反恐等。"
    "2. 没点名地点的团，若标题事态明显属于某一已有议题则并入；否则单独成题（如香格里拉、军演、防务政策）。"
    "3. 不要把所有中东新闻并成一个团。不要输出一个叫「其他」的议题。"
    "4. 每个输入 id 必须出现在且只出现在一个 group 的 members 里。"
    "5. 每个 group 给 8～16 字议题名，专名来自标题，禁止整句照抄某一篇。"
    "只输出 JSON：{\"groups\":[{\"members\":[\"c00\",\"c03\"],\"name\":\"美伊霍尔木兹冲突\"}]}"
)


def _parse(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).removesuffix("```").strip()
    data = json.loads(text)
    groups = data.get("groups") if isinstance(data, dict) else data
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        mem = [str(x) for x in (g.get("members") or []) if x]
        if not mem:
            continue
        name = re.sub(r"\s+", "", str(g.get("name") or ""))
        out.append({"members": mem, "name": name[:16] if name else ""})
    return out


def judge_groups(cands: list[dict]) -> tuple[list[dict], str]:
    """cands 需有 cid、docs、th。返回 groups + model。失败抛异常。"""
    from extract.llm import chat_json, resolve_client

    _key, _base, model = resolve_client()
    items = []
    for c in cands:
        items.append(
            {
                "id": c["cid"],
                "mass": c["mass"],
                "theaters": c.get("theaters") or sorted(c.get("th") or []),
                "titles": sample_titles(c.get("docs") or [], 10),
            }
        )
    raw = chat_json(SYSTEM, json.dumps({"clusters": items}, ensure_ascii=False), temperature=0.0, timeout=180)
    groups = _parse(raw)
    if not groups:
        raise RuntimeError("LLM 结构判定无 groups")
    return groups, model


def apply_groups(cands: list[dict], groups: list[dict]) -> list[dict]:
    """按 groups 合并候选；战场不相交的合并会被丢掉该边，成员各立。"""
    by_id = {c["cid"]: c for c in cands}
    seen: set[str] = set()
    fused: list[dict] = []
    for g in groups:
        mem = [i for i in g["members"] if i in by_id and i not in seen]
        if not mem:
            continue
        # 硬墙：不相交战场不并
        buckets: list[list[str]] = []
        for i in mem:
            placed = False
            th = set(by_id[i].get("theaters") or by_id[i].get("th") or [])
            for b in buckets:
                th_b: set[str] = set()
                for j in b:
                    th_b |= set(by_id[j].get("theaters") or by_id[j].get("th") or [])
                if not cannot_link(th, th_b):
                    b.append(i)
                    placed = True
                    break
            if not placed:
                buckets.append([i])
        for b in buckets:
            chunk = [by_id[i] for i in b]
            fused.append(_fuse(chunk, name=g.get("name") or ""))
            seen.update(b)
    for c in cands:
        if c["cid"] not in seen:
            fused.append(c)
    return fused


def _fuse(chunk: list[dict], name: str = "") -> dict:
    if len(chunk) == 1:
        one = dict(chunk[0])
        if name:
            one["name"] = name
        return one
    docs = []
    tree = []
    th: set[str] = set()
    parts = []
    for c in chunk:
        docs.extend(c.get("docs") or [])
        tree.extend(c.get("tree") or [])
        th |= set(c.get("theaters") or c.get("th") or [])
        p = c.get("partition") or ""
        if p:
            parts.append(p)
    lead = max(chunk, key=lambda c: (c.get("mass") or 0, c.get("cid") or ""))
    out = dict(lead)
    out["docs"] = docs
    out["tree"] = list(dict.fromkeys(tree))
    out["mass"] = len(docs)
    out["theaters"] = sorted(th)
    out["th"] = th
    out["partition"] = parts[0] if len(set(parts)) == 1 else ",".join(sorted(set(parts)))
    if name:
        out["name"] = name
    return out
