"""人物要素抽取。十项都来自原文，没有的留空，不补百科。"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from actor.clean import person_seed_of
from actor.config import ACTOR_DIR, REPORT_DIR
from actor.types import COUNTRIES, COUNTRY_ALIAS, rule_type
from ingest.datatypes import BY_ID, DATA_TYPES, MIN_DATA_TYPES, classify

# 长的放前面，避免「部长」吃掉「国防部长」。
POSTS = (
    "外交部长",
    "国防部长",
    "总参谋长",
    "新闻发言人",
    "发言人",
    "参谋长",
    "总统",
    "总理",
    "首相",
    "防长",
    "外长",
    "部长",
    "大臣",
    "司令",
    "舰长",
    "大使",
    "特使",
    "书记",
    "主席",
    "国王",
    "王储",
    "领袖",
    "议员",
    "陛下",
    "高官",
)
RANKS = (
    "上将",
    "中将",
    "少将",
    "准将",
    "大校",
    "上校",
    "中校",
    "少校",
    "大尉",
    "上尉",
    "中尉",
    "少尉",
    "元帅",
    "将军",
)
ORG_MARKS = ("国防部", "外交部", "武装部队", "武装力量")
NAME_RE = re.compile(r"^[\u4e00-\u9fff·•A-Za-z][\u4e00-\u9fff·•A-Za-z.\- ]{0,16}$")
MIN_PERSON_ELEMENTS = 10
# 会随见报日改写的身份项。行为、地点、对象留在当次提及里。
STATE_FIELDS = ("post", "nationality", "org")
POST_CANON = {
    "外长": "外交部长",
    "防长": "国防部长",
    "新闻发言人": "发言人",
}
# 十项都能从事件原文落到取值。出处（证据、来源、链接）不计入。
PERSON_ELEMENTS = (
    ("name", "姓名"),
    ("post", "职务"),
    ("nationality", "国籍"),
    ("org", "所属机构"),
    ("role", "事件角色"),
    ("action", "相关行为"),
    ("kind", "行为类别"),
    ("event_time", "行为时间"),
    ("place", "发生地点"),
    ("counterpart", "行为对象"),
)


def _strip_de(text: str) -> str:
    return text[1:] if text.startswith("的") else text


def _match_post(text: str) -> str:
    for post in POSTS:
        if text.startswith(post):
            return post
    return ""


def _match_rank(text: str) -> str:
    for rank in RANKS:
        if text.startswith(rank):
            return rank
    return ""


def _match_org(text: str) -> str:
    for org in ORG_MARKS:
        if text.startswith(org):
            return org
    return ""


def _match_country(text: str) -> tuple[str, str]:
    for name in sorted(COUNTRIES, key=len, reverse=True):
        if text.startswith(name):
            return name, text[len(name) :]
    for alias, canon in sorted(COUNTRY_ALIAS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if len(alias) >= 2 and not alias.isascii() and text.startswith(alias):
            return canon, text[len(alias) :]
    return "", text


def _take_titles(rest: str) -> tuple[str, str, str]:
    rest = _strip_de(rest)
    post = _match_post(rest)
    if post:
        rest = _strip_de(rest[len(post) :])
    rank = _match_rank(rest)
    if rank:
        rest = _strip_de(rest[len(rank) :])
    if not post:
        post = _match_post(rest)
        if post:
            rest = _strip_de(rest[len(post) :])
    return post, rank, rest


def _split_leading(text: str) -> tuple[str, str, str, str, str]:
    nationality, rest = _match_country(text)
    rest = _strip_de(rest)
    org = ""
    post, rank, rest = _take_titles(rest)
    if not post and not rank:
        org = _match_org(rest)
        if org:
            rest = _strip_de(rest[len(org) :])
            post, rank, rest = _take_titles(rest)
    return nationality, org, post, rank, rest


def _split_inner(text: str) -> tuple[str, str, str, str]:
    for post in POSTS:
        i = text.find(post)
        if i <= 0:
            continue
        right = _strip_de(text[i + len(post) :])
        if not right or not NAME_RE.fullmatch(right):
            continue
        left = text[:i]
        nationality = ""
        if left in COUNTRIES:
            nationality = left
        elif left in COUNTRY_ALIAS:
            nationality = COUNTRY_ALIAS[left]
        return nationality, "", post, right
    return "", "", "", ""


def _blank_person(name: str = "") -> dict:
    return {"name": name, "post": "", "rank": "", "nationality": "", "org": ""}


def _join_unique(items: list[str]) -> str:
    seen: list[str] = []
    for item in items:
        text = (item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return "、".join(seen)


def parse_person_surface(surface: str) -> dict:
    """从表面名拆姓名、职务、军衔、国籍、机构。拆不开的字段留空。"""
    text = (surface or "").strip()
    if not text:
        return _blank_person()
    seed = person_seed_of(text)
    if seed and text.endswith(seed):
        nationality, org, post, rank, _rest = _split_leading(text[: -len(seed)])
        return {"name": seed, "post": post, "rank": rank, "nationality": nationality, "org": org}
    nationality, org, post, rank, rest = _split_leading(text)
    if (post or rank) and rest and NAME_RE.fullmatch(rest):
        return {"name": rest, "post": post, "rank": rank, "nationality": nationality, "org": org}
    nationality, org, post, rest = _split_inner(text)
    if post and rest:
        return {"name": rest, "post": post, "rank": "", "nationality": nationality, "org": org}
    for rank in RANKS:
        if text.endswith(rank) and len(text) > len(rank) and NAME_RE.fullmatch(text[: -len(rank)]):
            return {"name": text[: -len(rank)], "post": "", "rank": rank, "nationality": "", "org": ""}
    for post in POSTS:
        if text.endswith(post) and len(text) > len(post):
            return {"name": text, "post": post, "rank": "", "nationality": "", "org": ""}
    return _blank_person(text)


def is_person_mention(row: dict) -> bool:
    if (row.get("entity_type") or row.get("type_rule")) == "Person":
        return True
    surface = str(row.get("surface") or "")
    if person_seed_of(surface):
        return True
    return rule_type(surface, str(row.get("role") or "")) == "Person"


_raw_index: dict[str, str] | None = None


def raw_type_index(raw_dir: Path | None = None) -> dict[str, str]:
    """doc_id → 数据类型。旧抽取行没有 category 时，用原稿信封回填。"""
    global _raw_index
    if raw_dir is None and _raw_index is not None:
        return _raw_index
    from actor.config import DATA

    root = raw_dir or (DATA / "raw")
    out: dict[str, str] = {}
    if root.is_dir():
        for path in sorted(root.glob("*/*.jsonl")):
            for line in path.open(encoding="utf-8"):
                if not line.strip():
                    continue
                raw = json.loads(line)
                doc_id = str(raw.get("doc_id") or "")
                if not doc_id:
                    continue
                dtype = classify(
                    str(raw.get("source") or ""),
                    raw.get("extra") if isinstance(raw.get("extra"), dict) else {},
                    str(raw.get("channel") or ""),
                )
                if dtype:
                    out[doc_id] = dtype
    if raw_dir is None:
        _raw_index = out
    return out


def data_type_of(row: dict) -> str:
    stamped = str(row.get("data_type") or "").strip()
    if stamped in BY_ID:
        return stamped
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    dtype = classify(str(row.get("source") or ""), extra, str(row.get("channel") or ""))
    if dtype:
        return dtype
    doc_id = str(row.get("doc_id") or "")
    if not doc_id:
        return ""
    return raw_type_index().get(doc_id, "")


def load_event_index() -> dict[tuple[str, int, int], dict]:
    """(doc_id, chunk_i, event_i) → 同一事件里的时间、地点、主体、对象。"""
    from actor.harvest import default_extract_paths, iter_extract

    index: dict[tuple[str, int, int], dict] = {}
    for path in default_extract_paths():
        if not path.is_file():
            continue
        for rec in iter_extract(path):
            doc_id = str(rec.get("doc_id") or "")
            for ci, ch in enumerate(rec.get("chunks") or []):
                for ei, ev in enumerate(ch.get("events") or []):
                    places, subjects, objects = [], [], []
                    for arg in ev.get("arguments") or []:
                        if not isinstance(arg, dict):
                            continue
                        role = str(arg.get("role") or "")
                        text = str(arg.get("text") or "").strip()
                        if not text:
                            continue
                        if role == "地点":
                            places.append(text)
                        elif role == "主体":
                            subjects.append(text)
                        elif role == "对象":
                            objects.append(text)
                    index[(doc_id, ci, ei)] = {
                        "raw_time": str(ev.get("raw_time") or "").strip(),
                        "kind": str(ev.get("kind") or "").strip(),
                        "place": _join_unique(places),
                        "subjects": subjects,
                        "objects": objects,
                    }
    return index


def _same_person(argument: str, surface: str, role: str) -> bool:
    text = (argument or "").strip()
    if not text or not surface:
        return False
    if text == surface:
        return True
    parsed = parse_person_surface(text)
    if parsed["name"] == surface:
        return True
    from actor.clean import canonicalize_surface

    return canonicalize_surface(text, role) == surface


def _recover_identity(row: dict, index: dict | None) -> dict:
    """旧提及只存了规范姓名。从同一事件的原文论元把职务、国籍、机构找回来。"""
    surface = str(row.get("surface") or "")
    argument = str(row.get("argument") or "").strip()
    if argument:
        parsed = parse_person_surface(argument)
        parsed["name"] = surface or parsed["name"]
        return parsed
    info = None
    if index is not None and row.get("via", "extract") == "extract" and "chunk_i" in row:
        info = index.get((str(row.get("doc_id") or ""), int(row["chunk_i"]), int(row["event_i"])))
    role = str(row.get("role") or "")
    pool = []
    if info:
        pool = info["subjects"] if role == "主体" else info["objects"] if role == "对象" else []
    best = None
    best_score = -1
    for text in pool:
        if not _same_person(text, surface, role):
            continue
        parsed = parse_person_surface(text)
        score = int(bool(parsed["post"])) + int(bool(parsed["nationality"])) + int(bool(parsed["org"]))
        if score > best_score:
            best = parsed
            best_score = score
    if best is None:
        return parse_person_surface(surface)
    best["name"] = surface or best["name"]
    return best


def _event_fields(row: dict, index: dict | None) -> dict:
    kind = str(row.get("kind") or "").strip()
    event_time = str(row.get("event_time") or row.get("raw_time") or "").strip()
    place = str(row.get("place") or "").strip()
    counterpart = str(row.get("counterpart") or "").strip()
    info = None
    if index is not None and "chunk_i" in row and "event_i" in row:
        info = index.get((str(row.get("doc_id") or ""), int(row["chunk_i"]), int(row["event_i"])))
    if info:
        kind = kind or info["kind"]
        event_time = event_time or info["raw_time"]
        place = place or info["place"]
        if not counterpart:
            role = str(row.get("role") or "")
            pool = info["objects"] if role == "主体" else info["subjects"] if role == "对象" else []
            surface = str(row.get("surface") or "")
            counterpart = _join_unique([x for x in pool if x != surface])
    return {"kind": kind, "event_time": event_time, "place": place, "counterpart": counterpart}


def person_element(row: dict, index: dict | None = None) -> dict | None:
    if not is_person_mention(row):
        return None
    dtype = data_type_of(row)
    if dtype not in BY_ID:
        return None
    surface = str(row.get("surface") or "")
    parsed = _recover_identity(row, index)
    if not parsed["name"]:
        return None
    evidence = str(row.get("evidence") or row.get("title") or "")
    sides = _event_fields(row, index)
    element = {
        "name": parsed["name"],
        "post": parsed["post"],
        "nationality": parsed["nationality"],
        "org": parsed["org"],
        "role": str(row.get("role") or ""),
        "action": str(row.get("trigger") or ""),
        "kind": sides["kind"],
        "event_time": sides["event_time"],
        "place": sides["place"],
        "counterpart": sides["counterpart"],
    }
    element.update(
        {
            "evidence": evidence[:180],
            "data_type": dtype,
            "data_type_label": BY_ID[dtype].label,
            "source": str(row.get("source") or ""),
            "day": str(row.get("day") or "")[:10],
            "url": str(row.get("url") or ""),
            "doc_id": str(row.get("doc_id") or ""),
        }
    )
    return element


def elements_from_mentions(rows: list[dict], index: dict | None = None) -> list[dict]:
    if index is None and any("chunk_i" in row for row in rows):
        index = load_event_index()
    out = []
    for row in rows:
        item = person_element(row, index)
        if item:
            out.append(item)
    return out


def fixture_mentions() -> list[dict]:
    """每种登记类型一条样例。句子相同，类型标签按该类型的 source / extra 回判。"""
    rows = []
    for item in DATA_TYPES:
        rows.append(
            {
                "surface": "美国总统特朗普",
                "role": "主体",
                "trigger": "表示",
                "kind": "表态",
                "raw_time": "9月1日",
                "place": "白宫",
                "counterpart": "伊朗",
                "evidence": "美国总统特朗普表示将继续施压。",
                "source": item.source,
                "extra": dict(item.extra),
                "day": "2026-09-01",
                "url": f"https://example.local/{item.id}",
                "doc_id": item.id,
                "title": item.label,
            }
        )
    return rows


def capability_check() -> dict:
    """人物要素不少于 10 项，且每种数据类型都能抽出姓名、职务、国籍。"""
    rows = elements_from_mentions(fixture_mentions())
    by_type = {row["data_type"]: row for row in rows}
    missing = [item.id for item in DATA_TYPES if item.id not in by_type]
    bad = []
    element_ids = [key for key, _label in PERSON_ELEMENTS]
    if len(PERSON_ELEMENTS) < MIN_PERSON_ELEMENTS:
        bad.append("element_count")
    for item in DATA_TYPES:
        got = by_type.get(item.id)
        if not got:
            continue
        if any(key not in got for key in element_ids):
            bad.append(item.id)
        if got["name"] != "特朗普" or got["post"] != "总统" or got["nationality"] != "美国":
            bad.append(item.id)
        if got["action"] != "表示" or got["role"] != "主体" or got["kind"] != "表态":
            bad.append(item.id)
        if got["event_time"] != "9月1日" or got["place"] != "白宫" or got["counterpart"] != "伊朗":
            bad.append(item.id)
        if classify(item.source, item.extra) != item.id:
            bad.append(item.id)
    samples = {
        "东帝汶总统奥尔塔": {"name": "奥尔塔", "post": "总统", "rank": ""},
        "七国集团外长": {"name": "七国集团外长", "post": "外长"},
        "美国国防部长奥斯汀": {"name": "奥斯汀", "post": "国防部长", "nationality": "美国"},
        "美国上将史密斯": {"name": "史密斯", "rank": "上将", "nationality": "美国", "post": ""},
    }
    for surface, expect in samples.items():
        got = parse_person_surface(surface)
        if any(got.get(k) != v for k, v in expect.items()):
            bad.append(surface)
    from actor.clean import canonicalize_surface

    if not state_update_ok():
        bad.append("state_update")
    if canonicalize_surface("美国总统特朗普", "主体") != "特朗普":
        bad.append("canon_name")
    kept = _recover_identity(
        {"surface": "特朗普", "argument": "美国总统特朗普", "role": "主体", "via": "extract"},
        None,
    )
    if kept["name"] != "特朗普" or kept["post"] != "总统" or kept["nationality"] != "美国":
        bad.append("keep_title")
    ok = (
        len(DATA_TYPES) >= MIN_DATA_TYPES
        and len(PERSON_ELEMENTS) >= MIN_PERSON_ELEMENTS
        and not missing
        and not bad
    )
    return {
        "ok": ok,
        "min_types": MIN_DATA_TYPES,
        "n_types": len(DATA_TYPES),
        "min_elements": MIN_PERSON_ELEMENTS,
        "n_elements": len(PERSON_ELEMENTS),
        "elements": [label for _key, label in PERSON_ELEMENTS],
        "missing": missing,
        "bad": sorted(set(bad)),
        "types": [
            {
                "id": item.id,
                "label": item.label,
                "summary": item.summary,
                "name": (by_type.get(item.id) or {}).get("name", ""),
                "post": (by_type.get(item.id) or {}).get("post", ""),
                "nationality": (by_type.get(item.id) or {}).get("nationality", ""),
            }
            for item in DATA_TYPES
        ],
    }


def write_capability(report: dict, dest: Path | None = None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = dest or (REPORT_DIR / "person_types.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _day_value(group: list[dict], field: str) -> tuple[str, dict | None]:
    counts: Counter[str] = Counter()
    sample: dict[str, dict] = {}
    for row in group:
        value = str(row.get(field) or "").strip()
        if field == "post":
            value = POST_CANON.get(value, value)
        if not value:
            continue
        counts[value] += 1
        sample.setdefault(value, row)
    if not counts:
        return "", None
    value = counts.most_common(1)[0][0]
    return value, sample[value]


def build_person_states(elements: list[dict]) -> list[dict]:
    """按见报日重放身份。空值不覆盖；后见报的不同职务、国籍、机构写成新修订。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in elements:
        name = str(row.get("name") or "").strip()
        if name:
            grouped[name].append(row)
    states = []
    for name, rows in grouped.items():
        rows.sort(key=lambda r: (str(r.get("day") or ""), str(r.get("doc_id") or "")))
        by_day: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_day[str(row.get("day") or "")].append(row)
        current = {field: "" for field in STATE_FIELDS}
        as_of = {field: "" for field in STATE_FIELDS}
        evidence = {field: "" for field in STATE_FIELDS}
        doc_of = {field: "" for field in STATE_FIELDS}
        revisions: list[dict] = []
        for day in sorted(by_day):
            group = by_day[day]
            for field in STATE_FIELDS:
                value, src = _day_value(group, field)
                if not value or src is None:
                    continue
                if value == current[field]:
                    as_of[field] = day
                    evidence[field] = str(src.get("evidence") or "")[:180]
                    doc_of[field] = str(src.get("doc_id") or "")
                    continue
                current[field] = value
                as_of[field] = day
                evidence[field] = str(src.get("evidence") or "")[:180]
                doc_of[field] = str(src.get("doc_id") or "")
                revisions.append(
                    {
                        "field": field,
                        "value": value,
                        "day": day,
                        "doc_id": doc_of[field],
                        "evidence": evidence[field],
                    }
                )
        states.append(
            {
                "name": name,
                "post": current["post"],
                "post_as_of": as_of["post"],
                "nationality": current["nationality"],
                "nationality_as_of": as_of["nationality"],
                "org": current["org"],
                "org_as_of": as_of["org"],
                "n_mentions": len(rows),
                "first_day": str(rows[0].get("day") or ""),
                "last_day": str(rows[-1].get("day") or ""),
                "revisions": revisions,
            }
        )
    states.sort(key=lambda s: (-s["n_mentions"], s["name"]))
    return states


def state_update_ok() -> bool:
    """后入库的旧闻不能盖过更新的见报；空职务不能把已有职务清掉。"""
    rows = [
        {"name": "甲", "day": "2026-05-01", "post": "外长", "nationality": "某国", "org": "", "doc_id": "b", "evidence": "五月任外长"},
        {"name": "甲", "day": "2026-06-01", "post": "", "nationality": "", "org": "", "doc_id": "c", "evidence": "只写了名字"},
        {"name": "甲", "day": "2026-04-01", "post": "大使", "nationality": "", "org": "外交部", "doc_id": "a", "evidence": "四月任大使"},
        {"name": "甲", "day": "2026-08-01", "post": "总统", "nationality": "某国", "org": "", "doc_id": "d", "evidence": "八月任总统"},
    ]
    state = build_person_states(rows)[0]
    posts = [item["value"] for item in state["revisions"] if item["field"] == "post"]
    return (
        state["post"] == "总统"
        and state["post_as_of"] == "2026-08-01"
        and state["nationality"] == "某国"
        and state["org"] == "外交部"
        and posts == ["大使", "外交部长", "总统"]
    )


def write_states(states: list[dict], dest: Path | None = None) -> Path:
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    path = dest or (ACTOR_DIR / "person_state.jsonl")
    with path.open("w", encoding="utf-8") as f:
        for row in states:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def write_elements(rows: list[dict], dest: Path | None = None) -> Path:
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    path = dest or (ACTOR_DIR / "persons.jsonl")
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path
