"""几种对齐方法。预测 same_as 的无序对。"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

from extract.llm import chat_json
from extract.prompt import parse_llm_json
from align.cluster import pair_key
from align.normalize import normalize

SAME = "same_as"


def method_exact_norm(names: list[str]) -> set[tuple[str, str]]:
    """归一化后全等才合（引号/空格）。"""
    buckets: dict[str, list[str]] = {}
    for n in names:
        buckets.setdefault(normalize(n) or n, []).append(n)
    out: set[tuple[str, str]] = set()
    for group in buckets.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                out.add(pair_key(a, b))
    return out


def method_contain(names: list[str]) -> set[tuple[str, str]]:
    """短串是长串子串就合。已知会把「日本」并进「日本防卫省」。"""
    out: set[tuple[str, str]] = set()
    norms = [(n, normalize(n) or n) for n in names]
    for i, (a, na) in enumerate(norms):
        for b, nb in norms[i + 1 :]:
            if not na or not nb or na == nb:
                if na == nb and a != b:
                    out.add(pair_key(a, b))
                continue
            if len(na) >= 2 and na in nb:
                out.add(pair_key(a, b))
            elif len(nb) >= 2 and nb in na:
                out.add(pair_key(a, b))
    return out


def method_edit(names: list[str], *, min_ratio: float = 0.75) -> set[tuple[str, str]]:
    """归一化后 SequenceMatcher 比例。美军/俄军/乌军会误合。"""
    out: set[tuple[str, str]] = set()
    norms = [(n, normalize(n) or n) for n in names]
    for i, (a, na) in enumerate(norms):
        for b, nb in norms[i + 1 :]:
            if not na or not nb or na == nb:
                if na == nb and a != b:
                    out.add(pair_key(a, b))
                continue
            if min(len(na), len(nb)) <= 1:
                continue
            r = SequenceMatcher(None, na, nb).ratio()
            if r >= min_ratio:
                out.add(pair_key(a, b))
    return out


def method_hybrid_rule(names: list[str]) -> set[tuple[str, str]]:
    """较严规则：全等，或「军队/武装」全称，禁止国家名被机构吃掉。"""
    countryish = {"中国", "美国", "日本", "伊朗", "以色列", "俄罗斯", "乌克兰", "韩国", "土耳其"}
    out = method_exact_norm(names)
    norms = [(n, normalize(n) or n) for n in names]
    for i, (a, na) in enumerate(norms):
        for b, nb in norms[i + 1 :]:
            if na == nb or not na or not nb:
                continue
            short, long, sa, la = (na, nb, a, b) if len(na) < len(nb) else (nb, na, b, a)
            if short in countryish:
                continue
            if short in long and (
                long.endswith(short)
                or long.startswith(short)
                or any(long.endswith(suf) for suf in ("军队", "国防军", "武装", "武装部队", "军方"))
            ):
                # 以军 ⊂ 以色列军队 OK；美 ⊂ 美军 已因国家表跳过「美国」
                if len(short) >= 2:
                    out.add(pair_key(sa, la))
    return out


SYSTEM = """你是军事新闻实体对齐裁判。只判断两个名称是不是同一个对象的不同写法。
same_as：同一支军队/同一人/同一组织的别称（以军=以色列国防军，特朗普=美国总统特朗普）。
related：有从属或代表关系但不是同一个节点（日本≠日本防卫省，美国≠美军，以色列≠以军）。
distinct：无关或不同武装力量（美军≠俄军）。
国家与其军队、政府、人物一律 related，不要 same_as。
拿不准选 related。输出 JSON：{"pairs":[{"a":"...","b":"...","label":"same_as|related|distinct"}]}"""


def parse_pair_labels(raw: str) -> list[dict]:
    data = parse_llm_json(raw)
    items = data.get("pairs") if isinstance(data.get("pairs"), list) else []
    if not items:
        text = raw or ""
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                items = parsed
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        lab = str(it.get("label") or "").strip()
        if a and b and lab in ("same_as", "related", "distinct"):
            out.append({"a": a, "b": b, "label": lab})
    return out


def llm_label_pairs(
    pairs: list[tuple[str, str]],
    *,
    batch: int = 10,
    cache_path: Path | None = None,
    system: str | None = None,
    user_examples: str | None = None,
    prelabel=None,
) -> list[dict]:
    """对候选对打 same_as / related / distinct。可缓存、可跳过国名子串。

    prelabel(a, b) → (label, via) 或 None。传入时不再走 Actor 的 X方/国名规则。
    """
    cache: dict[str, str] = {}
    if cache_path and cache_path.is_file():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    sys_prompt = system or SYSTEM
    examples = user_examples or (
        "例子：以军/以色列国防军 → same_as；日本/日本防卫省 → related；美军/俄军 → distinct。"
    )

    labeled: list[dict] = []
    seen = set()
    need: list[tuple[str, str]] = []
    for a, b in pairs:
        k = pair_key(a, b)
        if k in seen:
            continue
        seen.add(k)
        ck = f"{k[0]}\t{k[1]}"
        if prelabel is not None:
            hit = prelabel(k[0], k[1])
            if hit:
                labeled.append({"a": k[0], "b": k[1], "label": hit[0], "via": hit[1]})
                continue
        else:
            if FANG.get(a) == b or FANG.get(b) == a:
                labeled.append({"a": k[0], "b": k[1], "label": "same_as", "via": "fang"})
                continue
            if auto_related_country(a, b):
                labeled.append({"a": k[0], "b": k[1], "label": "related", "via": "country_substr"})
                continue
            if actor_auto_related(a, b):
                labeled.append({"a": k[0], "b": k[1], "label": "related", "via": "actor_rule"})
                continue
        if ck in cache:
            labeled.append({"a": k[0], "b": k[1], "label": cache[ck], "via": "cache"})
            continue
        need.append(k)

    print(f"  待 LLM {len(need)} 对（已自动/缓存 {len(labeled)}）", flush=True)
    for i in range(0, len(need), batch):
        chunk = need[i : i + batch]
        payload = json.dumps([{"a": a, "b": b} for a, b in chunk], ensure_ascii=False)
        user = (
            "判断下列名称对。a/b 必须原样复制。\n"
            f"{examples}\n"
            f"{payload}"
        )
        raw = chat_json(sys_prompt, user, timeout=120)
        got = parse_pair_labels(raw)
        by = {pair_key(x["a"], x["b"]): x["label"] for x in got}
        for a, b in chunk:
            lab = by.get(pair_key(a, b), "related")
            labeled.append({"a": a, "b": b, "label": lab, "via": "llm"})
            cache[f"{a}\t{b}"] = lab
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  LLM {min(i + batch, len(need))}/{len(need)}", flush=True)
    return labeled


def method_llm(pairs: list[tuple[str, str]], *, batch: int = 10) -> set[tuple[str, str]]:
    """只把 LLM 判为 same_as 的对算合并。"""
    out: set[tuple[str, str]] = set()
    for it in llm_label_pairs(pairs, batch=batch):
        if it["label"] == SAME:
            out.add(pair_key(it["a"], it["b"]))
    return out


COUNTRYISH = {
    "中国",
    "美国",
    "日本",
    "伊朗",
    "以色列",
    "俄罗斯",
    "乌克兰",
    "韩国",
    "土耳其",
    "英国",
    "法国",
    "德国",
    "印度",
    "巴基斯坦",
    "叙利亚",
    "黎巴嫩",
    "沙特",
}

FANG = {
    "日方": "日本",
    "美方": "美国",
    "俄方": "俄罗斯",
    "乌方": "乌克兰",
    "中方": "中国",
    "以方": "以色列",
    "伊方": "伊朗",
    "韩方": "韩国",
}


def auto_related_country(a: str, b: str) -> bool:
    """国名是另一串的真子串 → 从属/包含，不送 LLM。X方↔国名除外。"""
    if (a, b) in FANG.items() or (b, a) in FANG.items():
        return False
    na, nb = normalize(a) or a, normalize(b) or b
    if na == nb:
        return False
    if na in COUNTRYISH and na in nb and len(na) < len(nb):
        return True
    if nb in COUNTRYISH and nb in na and len(nb) < len(na):
        return True
    return False


def actor_auto_related(a: str, b: str) -> bool:
    """驻地部队≠母军队；「方面」≠「方向」。"""
    if a.endswith("方面") and b.endswith("方向") and a[:-2] == b[:-2]:
        return True
    if a.endswith("方向") and b.endswith("方面") and a[:-2] == b[:-2]:
        return True
    long, short = (a, b) if len(a) >= len(b) else (b, a)
    if long.startswith("驻") and len(short) >= 2 and long.endswith(short) and long != short:
        return True
    return False


def candidate_pairs(names: list[str]) -> list[tuple[str, str]]:
    """子串、编辑相近、X方↔国名、以军↔以色列*。"""
    s = method_contain(names) | method_edit(names, min_ratio=0.72)
    nset = set(names)
    for fang, country in FANG.items():
        if fang in nset and country in nset:
            s.add(pair_key(fang, country))
    for n in names:
        if n.startswith("以色列") and "以军" in nset:
            s.add(pair_key("以军", n))
    return sorted(s)
