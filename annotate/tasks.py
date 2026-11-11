"""三任务标注：NER / EE / RE 分开的卷管理与判定记录。

与原有单任务的 annotate/store.py 并存，不互相影响。
原有链路标的是事件单层；这里按三任务分卷，每卷只标一种内容。

    python3 -m annotate.tasks list
    python3 -m annotate.tasks show --task ner --split train --idx 0
    python3 -m annotate.tasks judge --task ner --doc <id> --file verdicts.json
    python3 -m annotate.tasks stat
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DS = DATA / "dataset"
STORE = DATA / "annotations"

TASKS = ("ner", "ee", "re")
SPLITS = ("train", "dev", "test")


# ---------------- 卷读取 ----------------


def volume_path(task: str, split: str) -> Path:
    return DS / f"{task}_{split}.jsonl"


def load_volume(task: str, split: str) -> list[dict]:
    p = volume_path(task, split)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def list_volumes() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for t in TASKS:
        out[t] = {}
        for s in SPLITS:
            p = volume_path(t, s)
            if p.is_file():
                out[t][s] = sum(1 for l in p.open(encoding="utf-8") if l.strip())
    return out


# ---------------- 判定记录 ----------------


def _verdict_path(task: str, annotator: str) -> Path:
    return STORE / f"{task}_{annotator}.jsonl"


def load_verdicts(task: str, annotator: str) -> dict[str, dict]:
    p = _verdict_path(task, annotator)
    if not p.is_file():
        return {}
    out: dict[str, dict] = {}
    for line in p.open(encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        out[rec["doc_id"]] = rec
    return out


def save_verdict(task: str, annotator: str, doc_id: str, items: list[dict], notes: str = "") -> dict:
    """追加一条判定记录。同一 doc 多次提交以最后一次为准，历史保留。"""
    STORE.mkdir(parents=True, exist_ok=True)
    rec = {
        "task": task,
        "annotator": annotator,
        "doc_id": doc_id,
        "items": items,
        "notes": notes,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with _verdict_path(task, annotator).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "task": task, "doc_id": doc_id, "annotator": annotator, "items": len(items)}


# ---------------- 待标与进度 ----------------


def next_doc(task: str, split: str, annotator: str, skip: Iterable[str] = ()) -> Optional[dict]:
    """返回该卷中第一篇未判定的稿件，附任务专属的候选内容。"""
    done = load_verdicts(task, annotator)
    skipset = {s for s in skip if s}
    for row in load_volume(task, split):
        did = row.get("doc_id")
        if did in done or did in skipset:
            continue
        return view(task, row)
    return None


def view(task: str, row: dict) -> dict:
    """按任务裁剪候选字段，只给该任务需要判断的内容。"""
    base = {
        "doc_id": row.get("doc_id"),
        "title": row.get("title"),
        "text": row.get("text") or "",
        "task": task,
    }
    if task == "ner":
        base["candidates"] = row.get("entities") or []
        base["schema"] = {"types": ["机构", "人物", "装备", "地点", "作品"]}
    elif task == "ee":
        base["candidates"] = row.get("events") or []
        base["composites"] = row.get("composites") or []
        base["schema"] = {
            "event_types": [
                "空袭", "打击", "拦截", "发射", "部署", "撤离", "演习",
                "控制", "签署", "谈判", "会议", "访问", "逮捕", "援助", "声明", "其他",
            ],
            "settings": [
                "战斗", "演习", "访问", "会谈", "记者会", "视察",
                "会议", "谈判", "审判", "选举", "签署", "部署", "巡逻", "救援", "其他",
            ],
            "kinds": ["发生", "表态", "计划"],
            "roles": ["施事", "受事", "到场", "使用", "涉及", "表态"],
            "stances": [
                "支持", "反对", "谴责", "警告", "否认",
                "承认", "呼吁", "要求", "关切", "中立",
            ],
        }
    else:
        base["candidates"] = row.get("triples") or []
        base["schema"] = {
            "relations": [
                "隶属", "辖属", "驻地", "部署方", "去向",
                "任职", "会晤", "搭载", "位于", "敌对",
            ],
            "endpoints": "两端都必须是实体，不能是事件",
        }
    return base


# ---------------- 统计 ----------------


def stat() -> dict:
    vols = list_volumes()
    out: dict[str, Any] = {"volumes": vols, "annotators": {}}
    for t in TASKS:
        for annotator in ("A", "B", "C"):
            v = load_verdicts(t, annotator)
            if v:
                out["annotators"].setdefault(annotator, {})[t] = len(v)
    # 双标覆盖率
    covered = {}
    for t in TASKS:
        a = set(load_verdicts(t, "A"))
        b = set(load_verdicts(t, "B"))
        total = sum(vols.get(t, {}).values()) if vols.get(t) else 0
        covered[t] = {
            "total": total,
            "A": len(a),
            "B": len(b),
            "both": len(a & b),
            "double_rate": round(len(a & b) / total, 3) if total else 0,
        }
    out["double_annotation"] = covered
    return out


# ---------------- 命令行 ----------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="三任务标注")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出各卷篇数")

    s = sub.add_parser("show", help="取一篇待标稿件")
    s.add_argument("--task", required=True, choices=TASKS)
    s.add_argument("--split", default="train", choices=SPLITS)
    s.add_argument("--annotator", default="A")
    s.add_argument("--idx", type=int, default=-1, help="-1 表示取第一篇未标")

    j = sub.add_parser("judge", help="提交判定")
    j.add_argument("--task", required=True, choices=TASKS)
    j.add_argument("--doc", required=True)
    j.add_argument("--file", required=True, help="判定 JSON，含 items 数组")
    j.add_argument("--annotator", default="A")
    j.add_argument("--notes", default="")

    sub.add_parser("stat", help="标注进度")

    a = p.parse_args(argv)
    if a.cmd == "list":
        print(json.dumps(list_volumes(), ensure_ascii=False, indent=2))
    elif a.cmd == "show":
        if a.idx >= 0:
            rows = load_volume(a.task, a.split)
            if a.idx >= len(rows):
                print(json.dumps({"error": "idx out of range"}, ensure_ascii=False))
                return 1
            print(json.dumps(view(a.task, rows[a.idx]), ensure_ascii=False, indent=2))
        else:
            doc = next_doc(a.task, a.split, a.annotator)
            print(json.dumps(doc or {"done": True}, ensure_ascii=False, indent=2))
    elif a.cmd == "judge":
        payload = json.loads(Path(a.file).read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else payload
        print(json.dumps(
            save_verdict(a.task, a.annotator, a.doc, items or [], a.notes),
            ensure_ascii=False, indent=2,
        ))
    else:
        print(json.dumps(stat(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
