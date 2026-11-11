"""汉语规范化：把抽取到的表面形式收到中文规范名，并用维基条目补人物底档。

    # 1. 从抽取结果收表面形式，查维基标题，写规范名对照
    python3 -m align.zh build --src data/extract/pilot10v3_sample.jsonl

    # 2. 按对照表取人物底档
    python3 -m align.zh cards --src data/extract/pilot10v3_sample.jsonl

    # 3. 查看当前对照与底档
    python3 -m align.zh stat
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional

from actor.aliases import en_canon_of
from ingest.zhwiki import _first_sentence, download_index, fetch_page, find_titles, is_article_title

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "zh"
CANON = DATA / "canon.json"
CARDS = DATA / "person_cards.jsonl"
REVIEW = DATA / "canon_review.jsonl"

PERSON_TYPES = {"人物"}
ORG_TYPES = {"机构", "平台"}
GEAR_TYPES = {"装备"}
PLACE_TYPES = {"地点"}
NAME_TYPES = PERSON_TYPES | ORG_TYPES | GEAR_TYPES | PLACE_TYPES

ZH_PUNCT = "《》“”‘’\"'（）()【】[]·•.,，、;；:：!！?？ "


def _load(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_canon() -> dict[str, dict]:
    return _load(CANON, {})


def save_canon(map_: dict[str, dict]) -> None:
    CANON.parent.mkdir(parents=True, exist_ok=True)
    CANON.write_text(
        json.dumps(dict(sorted(map_.items())), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


def _clean(surface: str) -> str:
    return (surface or "").strip().strip(ZH_PUNCT)


def _zh_like(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def _read_rows(src: Path) -> list[dict]:
    if not src.is_file():
        return []
    return [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]


def collect_surfaces(rows: list[dict]) -> dict[str, dict[str, int]]:
    """表面形式 -> 类型计数。只收需要规范名的四类。"""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        for ent in row.get("entities") or []:
            if ent.get("type") not in NAME_TYPES:
                continue
            text = _clean(ent.get("text") or "")
            if not text or len(text) > 40:
                continue
            slot = out.setdefault(text, {})
            slot[ent["type"]] = slot.get(ent["type"], 0) + 1
    return out


def resolve(surface: str, canon: dict[str, dict]) -> tuple[str, str]:
    """返回规范名与来源。词表优先，其次已知英文对照，最后原文不变。"""
    s = _clean(surface)
    if s in canon:
        return canon[s]["zh"], canon[s]["source"]
    hit = en_canon_of(s)
    if hit:
        return hit, "seed_aliases"
    return s, "raw"


def _pick_wiki_title(surface: str, cands: list[tuple[int, str]], zh_name: str) -> Optional[tuple[int, str]]:
    """只接受同名条目。zh_name 是译文或原中文名。"""
    target = _clean(zh_name)
    if not target:
        return None
    norm = lambda s: re.sub(r"[\s·・.,，]", "", _clean(s))
    for off, title in cands:
        if norm(title) == norm(target) and is_article_title(title):
            return off, title
    return None


TRANS_SYSTEM = (
    "你把新闻中的专名译成简体中文，用中文维基百科的通行条目名。"
    "人名用常用译名，地名用通行译名，机构用官方中文名。"
    "只输出 JSON：{\"items\":[{\"src\":\"原词\",\"zh\":\"中文名或空\"}]}。译不出就留空，不要编造。"
)


def translate_names(names: list[str], batch: int = 25) -> dict[str, str]:
    """把英文专名译成中文条目名。一次调用一批。"""
    from extract.llm import chat_json
    from extract.prompt import parse_llm_json

    out: dict[str, str] = {}
    for i in range(0, len(names), batch):
        chunk = names[i : i + batch]
        payload = json.dumps({"items": [{"src": n} for n in chunk]}, ensure_ascii=False)
        try:
            raw = chat_json(TRANS_SYSTEM, payload)
            parsed = parse_llm_json(raw)
        except Exception as exc:
            print(f"  translate error {type(exc).__name__}: {exc}")
            continue
        for it in parsed.get("items") or []:
            if not isinstance(it, dict):
                continue
            src = str(it.get("src") or "").strip()
            zh = str(it.get("zh") or "").strip()
            if src and zh and _zh_like(zh):
                out[src] = zh
        print(f"  translated {len(out)}/{len(names)}")
    return out


def build(src: Path, max_lookup: int = 400) -> dict:
    rows = _read_rows(src)
    surfaces = collect_surfaces(rows)
    canon = load_canon()
    todo = [s for s in surfaces if s not in canon]
    latin = [s for s in todo if not _zh_like(s)]
    native = [s for s in todo if _zh_like(s)]
    print(f"surface {len(surfaces)} | 已定 {len(surfaces) - len(todo)} | 中文 {len(native)} | 英文待译 {len(latin)}")

    translated = translate_names(latin[:max_lookup]) if latin else {}
    targets = {s: _clean(s) for s in native}
    targets.update({s: translated[s] for s in translated})
    if not targets:
        return {"added": 0, "total": len(canon)}

    path = download_index()
    wanted = sorted({v for v in targets.values() if v})
    hits = find_titles(wanted, path, limit=3)

    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with REVIEW.open("w", encoding="utf-8") as rev:
        for s, zh in targets.items():
            if not zh:
                rev.write(json.dumps({"surface": s, "zh": None, "why": "no_translation"},
                                     ensure_ascii=False) + "\n")
                continue
            cands = hits.get(zh) or []
            etype = max(surfaces[s], key=surfaces[s].get)
            pick = _pick_wiki_title(s, cands, zh)
            if not pick:
                rev.write(json.dumps({"surface": s, "zh": zh, "type": etype,
                                      "why": "no_exact_title", "cands": [t for _, t in cands][:3]},
                                     ensure_ascii=False) + "\n")
                continue
            off, title = pick
            canon[s] = {
                "zh": title,
                "source": "zhwiki_title" if _zh_like(s) else "zhwiki_title_after_translation",
                "offset": off,
                "type": etype,
                "url": f"https://zh.wikipedia.org/wiki/{title}",
            }
            added += 1
    save_canon(canon)
    print(json.dumps({"canon_total": len(canon), "added": added,
                      "review": str(REVIEW)}, ensure_ascii=False))
    return {"added": added, "total": len(canon)}


FIELD_PATTERNS = {
    "出生日期": [
        r"(\d{4})年(\d{1,2})月(\d{1,2})日[，,]?\s*(?:出生|生於|生于|出生於)",
        r"(?:出生於|出生於|生於|生于)[^。]{0,24}?(\d{4})年(\d{1,2})月(\d{1,2})日",
        r"(\d{4})年(\d{1,2})月(\d{1,2})日出生",
    ],
}

MIL_WORDS = (
    "軍", "军", "國防", "国防", "參謀", "参谋", "武裝", "武装", "部隊", "部队",
    "將軍", "将军", "上將", "上将", "中將", "中将", "少將", "少将", "軍官", "军官",
    "海軍", "海军", "空軍", "空军", "陸軍", "陆军", "部隊", "指挥官", "指揮官",
    "司令", "艦", "舰", "導彈", "导弹", "戰爭", "战争", "衝突", "冲突", "外交",
    "情報", "情报", "安全", "警", "發言人", "发言人",
)

COUNTRIES = (
    "美国", "中國", "中国", "俄羅斯", "俄罗斯", "烏克蘭", "乌克兰", "以色列",
    "伊朗", "伊拉克", "敘利亞", "叙利亚", "也門", "也门", "沙烏地", "沙特",
    "土耳其", "英國", "英国", "法國", "法国", "德國", "德国", "日本", "韓國", "韩国",
    "朝鮮", "朝鲜", "印度", "巴基斯坦", "澳大利亞", "澳大利亚", "加拿大", "菲律賓",
    "菲律宾", "埃及", "約旦", "约旦", "黎巴嫩", "卡塔爾", "卡塔尔", "阿聯酋",
    "阿联酋", "尼日利亞", "尼日利亚", "南非", "巴西", "墨西哥", "哥倫比亞",
    "哥伦比亚", "委內瑞拉", "委内瑞拉", "波蘭", "波兰", "羅馬尼亞", "罗马尼亚",
)


def _lead_fields(lead: str) -> dict[str, str]:
    """导语里取身份、国籍。导语是条目第一句，可回指条目。"""
    out = {"身份/职业": "", "国籍": ""}
    if not lead:
        return out
    m = re.search(r"(?:是|為|为)([^。，,；;]{2,60})", lead)
    if m:
        role = m.group(1).strip()
        role = re.sub(r"^(?:一位|一名|一个|為|为)", "", role).strip()
        if role and len(role) <= 40:
            out["身份/职业"] = role
    hits = [c for c in COUNTRIES if c in lead]
    if hits:
        out["国籍"] = max(hits, key=len)
    return out


def _search_field(plain: str, name: str) -> tuple[str, str]:
    for pat in FIELD_PATTERNS.get(name, []):
        m = re.search(pat, plain)
        if not m:
            continue
        if name == "出生日期":
            y, mo, d = m.group(1), m.group(2), m.group(3)
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}", m.group(0)
        return m.group(1).strip(), m.group(0)
    return "", ""


def _military_ok(lead: str) -> bool:
    return any(w in (lead or "") for w in MIL_WORDS)


def build_cards(src: Path, max_fetch: int = 40) -> dict:
    rows = _read_rows(src)
    canon = load_canon()
    persons: dict[str, int] = {}
    for row in rows:
        for ent in row.get("entities") or []:
            if ent.get("type") != "人物":
                continue
            s = _clean(ent.get("text") or "")
            if s:
                persons[s] = persons.get(s, 0) + 1
    out_rows = []
    fetched = 0
    skipped: list[dict] = []
    CARDS.parent.mkdir(parents=True, exist_ok=True)
    with CARDS.open("w", encoding="utf-8") as f:
        for surface, n in sorted(persons.items(), key=lambda kv: -kv[1]):
            entry = canon.get(surface)
            if not entry or entry.get("source") != "zhwiki_title":
                zh, why = resolve(surface, canon)
                skipped.append({"surface": surface, "zh": zh, "why": why})
                continue
            if fetched >= max_fetch:
                break
            try:
                page = fetch_page(entry["zh"], int(entry["offset"]))
            except Exception as exc:
                print(f"  error {surface} {type(exc).__name__}: {exc}")
                continue
            if page.get("error"):
                print(f"  error {surface} {page['error']}")
                continue
            fetched += 1
            plain = page.get("plain") or ""
            lead = _first_sentence(plain)
            if not _military_ok(lead):
                skipped.append({"surface": surface, "zh": entry["zh"],
                                "why": "not_military_lead", "lead": lead[:80]})
                print(f"  [skip] {surface} -> {entry['zh']} 非军事导语: {lead[:50]}")
                continue
            birth, birth_sent = _search_field(plain, "出生日期")
            lead_fields = _lead_fields(lead)
            attrs = {
                "出生日期": {"value": birth, "source_sentence": birth_sent},
                "身份/职业": {"value": lead_fields["身份/职业"], "source_sentence": lead},
                "国籍": {"value": lead_fields["国籍"], "source_sentence": lead},
            }
            rec = {
                "surface": surface,
                "name_zh": entry["zh"],
                "url": entry.get("url"),
                "mentions": n,
                "lead": lead,
                "attrs": attrs,
                "source": "zhwiki",
            }
            out_rows.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  [card] {surface} -> {entry['zh']} 出生={birth} 国籍={lead_fields['国籍']} 职业={lead_fields['身份/职业'][:20]}")
    return {"cards": len(out_rows), "skipped_persons": len(skipped), "skipped": skipped[:10]}


def localize(src: Path, out: Optional[Path] = None) -> dict:
    """把抽取结果里的专名换成汉语规范名。原文片段与英文写法保留为别名。

    实体：name 用规范名，surface 保留原文片段，aliases 记已见写法。
    关系：head/tail 改为规范名，原文片段写进 head_surface/tail_surface。
    块内改写后，同一规范名的关系去重；跨块合并。
    """
    rows = _read_rows(src)
    if out is None:
        out = src.with_name(src.stem.replace("_sample", "") + "_zh.jsonl")
    canon = load_canon()
    cards: dict[str, dict] = {}
    if CARDS.is_file():
        for line in CARDS.open(encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                cards[rec["name_zh"]] = rec

    stats = {"entities": 0, "localized": 0, "relations": 0, "rel_localized": 0,
             "alias_conflicts": 0, "merged_nodes": 0}
    alias_seen: dict[tuple[str, str], set[str]] = {}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            ents_out: dict[tuple[str, str], dict] = {}
            surface_to_name: dict[str, str] = {}
            for ent in row.get("entities") or []:
                surface = _clean(ent.get("text") or "")
                etype = ent.get("type") or ""
                if not surface:
                    continue
                if etype in NAME_TYPES:
                    name, source = resolve(surface, canon)
                else:
                    name, source = surface, "sentence"
                surface_to_name[surface] = name
                key = (etype, name)
                node = ents_out.get(key)
                if node is None:
                    node = {
                        "type": etype,
                        "name": name,
                        "surface": surface,
                        "aliases": [],
                        "name_source": source,
                        "chunk": ent.get("chunk", 0),
                    }
                    # 事件话语种类随节点带走，不能丢
                    if etype == "事件" and ent.get("kind"):
                        node["kind"] = ent.get("kind")
                    ents_out[key] = node
                else:
                    stats["merged_nodes"] += 1
                    if etype == "事件" and not node.get("kind") and ent.get("kind"):
                        node["kind"] = ent.get("kind")
                if surface != name and surface not in node["aliases"]:
                    node["aliases"].append(surface)
            rels_out = []
            seen = set()
            for rel in row.get("relations") or []:
                head_s = _clean(rel.get("head") or "")
                tail_s = _clean(rel.get("tail") or "")
                head = surface_to_name.get(head_s, head_s)
                tail = surface_to_name.get(tail_s, tail_s)
                key = (rel["name"], head, tail)
                if key in seen:
                    continue
                seen.add(key)
                rels_out.append({
                    **rel,
                    "head": head,
                    "tail": tail,
                    "head_surface": head_s,
                    "tail_surface": tail_s,
                })
                if head != head_s or tail != tail_s:
                    stats["rel_localized"] += 1
            persons_out = []
            for person in row.get("persons") or []:
                ps = _clean(person.get("name") or "")
                pname = surface_to_name.get(ps) or resolve(ps, canon)[0]
                card = cards.get(pname)
                persons_out.append({
                    **person,
                    "name": pname,
                    "name_surface": ps,
                    "card": {"url": card["url"], "lead": card["lead"],
                             "attrs": card["attrs"]} if card else None,
                })
            for node in ents_out.values():
                alias_seen.setdefault((node["type"], node["name"]), set()).update(node["aliases"])
            stats["entities"] += len(ents_out)
            stats["relations"] += len(rels_out)
            f.write(json.dumps({
                "doc_id": row.get("doc_id"),
                "source": row.get("source"),
                "lang": row.get("lang"),
                "title": row.get("title"),
                "publish_time": row.get("publish_time"),
                "url": row.get("url"),
                "entities": list(ents_out.values()),
                "relations": rels_out,
                "persons": persons_out,
            }, ensure_ascii=False) + "\n")
    # 全局别名汇总，供消歧阶段使用
    alias_path = DATA / "node_aliases.json"
    alias_path.write_text(json.dumps(
        {f"{t}|{n}": sorted(v) for (t, n), v in sorted(alias_seen.items())
         if v}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stats["nodes_with_alias"] = sum(1 for v in alias_seen.values() if v)
    stats["out"] = str(out)
    stats["alias_index"] = str(alias_path)
    return stats


def stat() -> dict:
    canon = load_canon()
    by_src: dict[str, int] = {}
    for v in canon.values():
        key = v.get("source", "?")
        by_src[key] = by_src.get(key, 0) + 1
    cards = 0
    if CARDS.is_file():
        cards = sum(1 for line in CARDS.open(encoding="utf-8") if line.strip())
    return {"canon": len(canon), "by_source": by_src, "cards": cards,
            "canon_path": str(CANON), "cards_path": str(CARDS)}


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="汉语规范化与人物底档")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="查维基标题，写规范名对照")
    b.add_argument("--src", default=str(ROOT / "data" / "extract" / "pilot10v3_sample.jsonl"))
    c = sub.add_parser("cards", help="取人物底档")
    c.add_argument("--src", default=str(ROOT / "data" / "extract" / "pilot10v3_sample.jsonl"))
    c.add_argument("--max-fetch", type=int, default=40)
    l = sub.add_parser("localize", help="把抽取结果换成汉语规范名")
    l.add_argument("--src", default=str(ROOT / "data" / "extract" / "pilot10v3_sample.jsonl"))
    l.add_argument("--out", default="")
    sub.add_parser("stat", help="查看对照与底档")
    a = p.parse_args(argv)
    if a.cmd == "build":
        print(json.dumps(build(Path(a.src)), ensure_ascii=False))
    elif a.cmd == "cards":
        print(json.dumps(build_cards(Path(a.src), a.max_fetch), ensure_ascii=False, indent=2))
    elif a.cmd == "localize":
        print(json.dumps(localize(Path(a.src), Path(a.out) if a.out else None),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(stat(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
