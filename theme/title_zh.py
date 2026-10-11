"""英文标题译成中文，只给主题编码和立题用。不译正文。"""

from __future__ import annotations

import json
from pathlib import Path

from extract.llm import chat_json
from extract.prompt import parse_llm_json
from theme.config import THEME_DIR

CACHE = THEME_DIR / "title_zh.json"
BATCH = 20
SYSTEM = (
    "你把军事新闻标题译成简体中文。专名用通行译法（以军、胡塞武装、泽连斯基、革命卫队）。"
    "只输出 JSON：{\"items\":[{\"id\":\"...\",\"title_zh\":\"...\"}]}。"
    "不要解释，不要漏 id。"
)


def _load_cache() -> dict[str, str]:
    if not CACHE.is_file():
        return {}
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if v}
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_en(row: dict) -> bool:
    lang = (row.get("lang") or "").lower()
    if lang.startswith("en"):
        return True
    if row.get("source") == "rss_api" and not lang.startswith("zh"):
        return True
    return False


def _translate_batch(batch: list[dict]) -> dict[str, str]:
    payload = [{"id": r["doc_id"], "title": r.get("title") or ""} for r in batch]
    raw = chat_json(SYSTEM, json.dumps({"items": payload}, ensure_ascii=False))
    parsed = parse_llm_json(raw)
    out: dict[str, str] = {}
    for it in parsed.get("items") or []:
        if not isinstance(it, dict):
            continue
        did = str(it.get("id") or "")
        zh = str(it.get("title_zh") or "").strip()
        if did and zh:
            out[did] = zh
    return out


def fill_catalog(path: Path | None = None) -> dict:
    src = path or (THEME_DIR / "catalog.jsonl")
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    cache = _load_cache()
    need = [r for r in rows if _is_en(r) and r.get("doc_id") and r["doc_id"] not in cache]
    translated = 0
    for i in range(0, len(need), BATCH):
        chunk = need[i : i + BATCH]
        got = _translate_batch(chunk)
        cache.update(got)
        translated += len(got)
        _save_cache(cache)
        print(f"  译标题 {min(i + BATCH, len(need))}/{len(need)}", flush=True)
    n_filled = 0
    with src.open("w", encoding="utf-8") as f:
        for row in rows:
            did = row.get("doc_id") or ""
            if did in cache:
                row["title_zh"] = cache[did]
                n_filled += 1
            elif not _is_en(row):
                row["title_zh"] = row.get("title") or ""
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"need": len(need), "translated": translated, "filled": n_filled}
