"""三任务导出：把标注卷转成公开数据集的兼容格式。

- NER 对齐 MSRA / OntoNotes 的 BIO 序列格式
- EE 对齐 CMNEE 的 trigger 加 arguments 结构
- RE 对齐 DuIE 的 spo_list 结构

    python3 -m dataset.export --task ner --split train
    python3 -m dataset.export --task all
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DS = DATA / "dataset"
EXPORT = DATA / "dataset" / "export"

TASKS = ("ner", "ee", "re")
SPLITS = ("train", "dev", "test")


def load(task: str, split: str) -> list[dict]:
    p = DS / f"{task}_{split}.jsonl"
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def to_bio(row: dict) -> dict:
    """NER 转 BIO。按原文逐字符扫描，实体片段必须能定位。"""
    text = row.get("text") or ""
    spans = []
    for ent in row.get("entities") or []:
        s = ent.get("text") or ""
        t = ent.get("type") or ""
        if not s or not t:
            continue
        start = text.find(s)
        if start < 0:
            continue
        spans.append((start, start + len(s), t))
    # 同一起点取最长，避免嵌套覆盖
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    tags = ["O"] * len(text)
    used = [False] * len(text)
    kept = []
    for a, b, t in spans:
        if any(used[a:b]):
            continue
        tags[a] = f"B-{t}"
        for i in range(a + 1, b):
            tags[i] = f"I-{t}"
        for i in range(a, b):
            used[i] = True
        kept.append((a, b, t))
    return {
        "doc_id": row.get("doc_id"),
        "tokens": list(text),
        "ner_tags": tags,
        "entities": [{"text": text[a:b], "type": t, "offset": [a, b]} for a, b, t in kept],
    }


def to_cmnee_like(row: dict) -> dict:
    """EE 转 CMNEE 风格：events 数组，每条含 trigger、type、arguments。"""
    events = []
    for ev in row.get("events") or []:
        args = []
        for a in ev.get("arguments") or []:
            item = {"role": a.get("role") or "", "text": a.get("text") or "", "offset": None}
            if a.get("stance"):
                item["stance"] = a["stance"]
            args.append(item)
        events.append({
            "trigger": ev.get("trigger") or "",
            "event_type": ev.get("event_type") or "",
            "setting": ev.get("setting") or "",
            "kind": ev.get("kind") or "",
            "arguments": args,
        })
    return {
        "doc_id": row.get("doc_id"),
        "title": row.get("title") or "",
        "text": row.get("text") or "",
        "events": events,
        "composites": row.get("composites") or [],
    }


def to_duie_like(row: dict) -> dict:
    """RE 转 DuIE 风格：spo_list，object 统一为 @value 结构。"""
    spo = []
    for t in row.get("triples") or []:
        spo.append({
            "subject": t.get("head") or "",
            "predicate": t.get("relation") or "",
            "object": {"@value": t.get("tail") or ""},
            "evidence": t.get("evidence") or "",
        })
    return {
        "doc_id": row.get("doc_id"),
        "text": row.get("text") or "",
        "spo_list": spo,
    }


def export_task(task: str, splits: tuple[str, ...] = SPLITS) -> dict:
    EXPORT.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    for split in splits:
        rows = load(task, split)
        if not rows:
            continue
        if task == "ner":
            out = [to_bio(r) for r in rows]
            stats["entities"] += sum(len(r["entities"]) for r in out)
        elif task == "ee":
            out = [to_cmnee_like(r) for r in rows]
            stats["events"] += sum(len(r["events"]) for r in out)
            stats["args"] += sum(len(e["arguments"]) for r in out for e in r["events"])
        else:
            out = [to_duie_like(r) for r in rows]
            stats["triples"] += sum(len(r["spo_list"]) for r in out)
        path = EXPORT / f"{task}_{split}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        stats[f"{split}_docs"] = len(out)
    return dict(stats)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="三任务导出")
    p.add_argument("--task", default="all", choices=list(TASKS) + ["all"])
    p.add_argument("--split", default="all", choices=list(SPLITS) + ["all"])
    a = p.parse_args(argv)
    tasks = TASKS if a.task == "all" else (a.task,)
    splits = SPLITS if a.split == "all" else (a.split,)
    result = {t: export_task(t, splits) for t in tasks}
    print(json.dumps({"out_dir": str(EXPORT), "stats": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
