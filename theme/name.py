"""给查询窗主题起短名。不改聚类：只填 name。

优先 LLM（方案：8～16 字，只看标题，禁止编造）。没有密钥时用标题集合里的战场+高频专名压缩，仍不是某一篇全文标题。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict

from theme.theaters import theaters_of

THEATER_ZH = {
    "ukraine": "俄乌",
    "iran": "美伊",
    "houthi": "胡塞",
    "lebanon": "黎南",
    "gaza": "加沙",
    "carrier": "航母",
    "eastasia": "南海",
    "japan": "日本",
    "korea": "朝韩",
}

# 长词优先；出现在多条标题里才有资格进议题名
PHRASES: list[tuple[str, str]] = [
    ("霍尔木兹海峡", "霍尔木兹"),
    ("霍尔木兹", "霍尔木兹"),
    ("拉腊克", "布雷"),
    ("革命卫队", "美伊冲突"),
    ("爱国者", "反导"),
    ("萨德", "反导"),
    ("无人机", "无人机"),
    ("弹道导弹", "导弹"),
    ("布雷", "布雷"),
    ("航母", "航母"),
    ("林肯", "航母"),
    ("罗斯福", "航母"),
    ("油轮", "航运"),
    ("红海", "红海"),
    ("摩卡", "摩卡港"),
    ("政府军", "也门内战"),
    ("真主党", "真主党"),
    ("黎南部", "黎南"),
    ("黎南", "黎南"),
    ("哈马斯", "哈马斯"),
    ("加沙", "加沙"),
    ("武器库", "清缴"),
    ("基辅", "空袭"),
    ("泽连斯基", "俄乌"),
    ("空袭", "空袭"),
    ("炼油厂", "能源袭击"),
        ("航行协议", "通航协议"),
        ("阿曼", "通航协议"),
        ("基地", "驻军"),
]

SYSTEM = (
    "你给军事新闻报道簇起议题名，不是改写某一条标题。"
    "每条 8 到 16 个汉字，像专栏栏目：地点或双方 + 事态。"
    "专名必须来自所给标题。禁止整句照抄。禁止补因果、胜负、意图。"
    "只输出 JSON：{\"names\":[{\"id\":\"t00\",\"name\":\"俄乌相互空袭\"}]}"
)


def sample_titles(docs: list[dict], n: int = 15) -> list[str]:
    rows = sorted(docs, key=lambda a: (a.get("day") or "", a.get("doc_id") or ""))
    seen, uniq = set(), []
    for a in rows:
        t = (a.get("title_zh") or a.get("title") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    if len(uniq) <= n:
        return uniq
    # 头、中、尾均匀抽，避免全是窗末日的那一篇
    idxs = sorted({round(i * (len(uniq) - 1) / (n - 1)) for i in range(n)})
    return [uniq[i] for i in idxs]


def _primary_theater(titles: list[str]) -> str:
    c: Counter[str] = Counter()
    for t in titles:
        for th in theaters_of(t):
            c[th] += 1
    if not c:
        return ""
    return c.most_common(1)[0][0]


def _action_hits(titles: list[str]) -> list[str]:
    n = max(len(titles), 1)
    scored: list[tuple[float, int, str]] = []
    used_span: set[str] = set()
    for phrase, frag in PHRASES:
        df = sum(1 for t in titles if phrase in t)
        if df < 1:
            continue
        if df / n < 0.12 and df < 3:
            continue
        if any(phrase in p or p in phrase for p in used_span):
            continue
        used_span.add(phrase)
        scored.append((df / n, len(phrase), frag))
    scored.sort(reverse=True)
    out, seen = [], set()
    for _r, _l, frag in scored:
        if frag in seen:
            continue
        seen.add(frag)
        out.append(frag)
        if len(out) >= 3:
            break
    return out


def _mentions(titles: list[str], words: tuple[str, ...]) -> int:
    return sum(1 for t in titles if any(w in t for w in words))


def rule_name(titles: list[str], *, avoid: str = "") -> str:
    """压缩标题集合，不返回超过 16 字的整句标题。"""
    titles = [t for t in titles if (t or "").strip()]
    if not titles:
        return ""
    th = _primary_theater(titles)
    acts = _action_hits(titles)
    name = ""
    if th == "ukraine":
        name = "俄乌相互空袭" if _mentions(titles, ("无人机", "空袭", "导弹", "基辅")) else "俄乌战事"
    elif th == "iran":
        # 航母/反导是这条冲突的侧面，不能把整簇叫成航母
        if _mentions(titles, ("霍尔木兹", "布雷", "拉腊克")):
            name = "美伊霍尔木兹冲突"
        else:
            name = "美伊军事冲突"
        if avoid in ("美伊霍尔木兹冲突", "美伊军事冲突"):
            if _mentions(titles, ("航母", "林肯", "罗斯福")):
                name = "美军航母部署"
            elif _mentions(titles, ("爱国者", "萨德")):
                name = "美反导弹药消耗"
            elif _mentions(titles, ("基地", "海湾")):
                name = "海湾美军基地"
            elif _mentions(titles, ("航行", "阿曼")):
                name = "霍尔木兹通航协议"
    elif th == "houthi":
        saudi = _mentions(titles, ("沙特", "油轮", "红海", "航运"))
        civil = _mentions(titles, ("政府军", "摩卡", "马里卜"))
        if saudi >= civil:
            name = "胡塞袭击沙特目标"
        else:
            name = "胡塞与也门政府军冲突"
    elif th == "lebanon":
        name = "以军空袭黎南部"
    elif th == "gaza":
        name = "以军空袭加沙"
    elif th == "carrier":
        name = "美军航母部署"
    elif th == "eastasia":
        name = "南海周边军情"
    elif th == "japan":
        name = "日本防卫动向"
    elif th == "korea":
        name = "朝鲜半岛军情"
    if not name or name == avoid:
        if _mentions(titles, ("爱国者", "萨德")):
            name = "美反导弹药消耗"
        elif _mentions(titles, ("基地", "海湾")):
            name = "海湾美军基地"
        elif _mentions(titles, ("航母", "林肯", "罗斯福")):
            name = "美军航母部署"
        else:
            head = THEATER_ZH.get(th, "")
            parts = [head] if head else []
            for a in acts:
                if a and a not in "".join(parts):
                    parts.append(a)
                blob = "".join(parts)
                if 6 <= len(blob) <= 16 and blob != avoid:
                    name = blob
                    break
            else:
                name = "".join(parts)[:16]
    name = re.sub(r"\s+", "", name)
    if name == avoid or len(name) < 4:
        name = (titles[-1].replace(" ", ""))[:12]
    return name[:16]


def _parse_names(raw: str) -> dict[str, str]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).removesuffix("```").strip()
    data = json.loads(text)
    rows = data.get("names") if isinstance(data, dict) else data
    out: dict[str, str] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        i, n = str(row.get("id") or ""), str(row.get("name") or "").strip()
        n = re.sub(r"\s+", "", n)
        if i and 4 <= len(n) <= 20:
            out[i] = n[:16]
    return out


def llm_names(items: list[dict]) -> tuple[dict[str, str], str]:
    """items: {id, titles}。返回 (id→name, model)。失败抛异常。"""
    from extract.llm import chat_json, resolve_client

    _key, _base, model = resolve_client()
    payload = {
        "items": [
            {"id": it["id"], "titles": sample_titles(it["docs"], 15) if "docs" in it else it.get("titles") or []}
            for it in items
        ]
    }
    raw = chat_json(SYSTEM, json.dumps(payload, ensure_ascii=False), temperature=0.0, timeout=120)
    return _parse_names(raw), model


def apply_names(payload: dict, *, mode: str = "auto") -> dict:
    """mode: off / rule / llm / auto。改 payload['themes'|'derived'|params]，不改 mass。"""
    if mode in ("off", "none", "headline"):
        payload.setdefault("params", {})["name_mode"] = "headline"
        return payload

    docs_by_theme: dict[str, list[dict]] = defaultdict(list)
    docs_by_story: dict[str, list[dict]] = defaultdict(list)
    for a in payload.get("docs") or []:
        if a.get("theme_id"):
            docs_by_theme[a["theme_id"]].append(a)
        if a.get("story_id"):
            docs_by_story[a["story_id"]].append(a)

    items = []
    for t in payload.get("themes") or []:
        t["headline"] = t.get("headline") or t.get("name") or t.get("title0") or ""
        items.append({"id": t["theme_id"], "docs": docs_by_theme.get(t["theme_id"]) or []})
    for t in payload.get("minors") or []:
        t["headline"] = t.get("headline") or t.get("name") or t.get("title0") or ""
        items.append({"id": t["theme_id"], "docs": docs_by_theme.get(t["theme_id"]) or []})
    for d in payload.get("derived") or []:
        d["headline"] = d.get("headline") or d.get("name") or d.get("title0") or ""
        items.append({"id": d["story_id"], "docs": docs_by_story.get(d["story_id"]) or []})

    used = "rule"
    model = ""
    mapping: dict[str, str] = {}
    want_llm = mode in ("llm", "auto")
    if want_llm:
        try:
            mapping, model = llm_names(items)
            used = "llm"
        except Exception:
            if mode == "llm":
                raise
            mapping = {}
            used = "rule"

    if used == "rule" or not mapping:
        for t in payload.get("themes") or []:
            titles = [(a.get("title_zh") or a.get("title") or "") for a in docs_by_theme.get(t["theme_id"]) or []]
            mapping[t["theme_id"]] = rule_name(titles)
        for t in payload.get("minors") or []:
            titles = [(a.get("title_zh") or a.get("title") or "") for a in docs_by_theme.get(t["theme_id"]) or []]
            mapping[t["theme_id"]] = rule_name(titles)
        for d in payload.get("derived") or []:
            titles = [(a.get("title_zh") or a.get("title") or "") for a in docs_by_story.get(d["story_id"]) or []]
            mapping[d["story_id"]] = rule_name(titles, avoid=mapping.get(d["theme_id"]) or "")
        used = "rule"

    for t in payload.get("themes") or []:
        if not mapping.get(t["theme_id"]):
            titles = [(a.get("title_zh") or a.get("title") or "") for a in docs_by_theme.get(t["theme_id"]) or []]
            mapping[t["theme_id"]] = rule_name(titles)
    for t in payload.get("minors") or []:
        if not mapping.get(t["theme_id"]):
            titles = [(a.get("title_zh") or a.get("title") or "") for a in docs_by_theme.get(t["theme_id"]) or []]
            mapping[t["theme_id"]] = rule_name(titles)
    for d in payload.get("derived") or []:
        if not mapping.get(d["story_id"]):
            titles = [(a.get("title_zh") or a.get("title") or "") for a in docs_by_story.get(d["story_id"]) or []]
            mapping[d["story_id"]] = rule_name(titles, avoid=mapping.get(d["theme_id"]) or "")

    for t in payload.get("themes") or []:
        t["name"] = mapping.get(t["theme_id"]) or t.get("name") or ""
    for t in payload.get("minors") or []:
        t["name"] = mapping.get(t["theme_id"]) or t.get("name") or ""
    tname = {t["theme_id"]: t["name"] for t in (payload.get("themes") or []) + (payload.get("minors") or [])}
    for d in payload.get("derived") or []:
        d["name"] = mapping.get(d["story_id"]) or d.get("name") or ""
        d["parent_name"] = tname.get(d["theme_id"]) or d.get("parent_name") or ""

    for col in payload.get("columns") or []:
        for u in col.get("units") or []:
            u["headline"] = u.get("name") or ""
            u["name"] = tname.get(u.get("theme_id") or "") or u.get("name") or ""

    for e in payload.get("migration") or []:
        if e.get("from_id"):
            e["headline_from"] = e.get("name_from") or ""
            e["name_from"] = tname.get(e["from_id"]) or e.get("name_from") or ""
        if e.get("to_id"):
            e["headline_to"] = e.get("name_to") or ""
            e["name_to"] = tname.get(e["to_id"]) or e.get("name_to") or ""

    params = payload.setdefault("params", {})
    params["name_mode"] = used
    if model:
        params["name_model"] = model
    params["name_prompt_sha"] = hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]
    return payload
