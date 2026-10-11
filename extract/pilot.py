"""从旧 openie.jsonl 分层抽 20 块，供人工试点。不调用 LLM。

python -m extract.pilot
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEQ_CUES = ("随后", "之后", "接着", "然后")
SAY = ("称", "表示", "谴责", "警告", "否认", "透露")


def _load_chunks(path: Path) -> list[dict]:
    out = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for ch in row.get("chunks") or []:
            evs = ch.get("events") or []
            text = ch.get("text") or ""
            out.append(
                {
                    "doc_id": row.get("doc_id"),
                    "title": row.get("title"),
                    "publish_time": row.get("publish_time"),
                    "url": row.get("url"),
                    "chunk_id": ch.get("chunk_id"),
                    "n_events": len(evs),
                    "has_seq": any(c in text for c in SEQ_CUES),
                    "has_say": any(
                        (e.get("trigger") or "") in SAY or any(s in (e.get("trigger") or "") for s in SAY)
                        for e in evs
                    ),
                    "empty_raw": any(not str(e.get("raw_time") or "").strip() for e in evs) if evs else True,
                    "multi": len(evs) >= 3,
                    "n_chars": len(text),
                }
            )
    return out


def _pick(pool: list[dict], n: int, used: set[str]) -> list[dict]:
    chosen = []
    for item in pool:
        cid = item["chunk_id"]
        if cid in used:
            continue
        used.add(cid)
        chosen.append(item)
        if len(chosen) >= n:
            break
    return chosen


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="分层抽 20 块试点")
    p.add_argument("--src", default=str(DATA / "extract" / "openie.jsonl"))
    p.add_argument("--out", default=str(ROOT / "data" / "eval" / "pilot20.json"))
    p.add_argument("--n", type=int, default=20)
    args = p.parse_args(argv)

    chunks = _load_chunks(Path(args.src))
    if not chunks:
        print(f"没有块：{args.src}")
        return 1
    used: set[str] = set()
    buckets = [
        ("seq", [c for c in chunks if c["has_seq"]], 5),
        ("say", [c for c in chunks if c["has_say"] and not c["has_seq"]], 5),
        ("multi", [c for c in chunks if c["multi"] and not c["has_seq"]], 4),
        ("empty_raw", [c for c in chunks if c["empty_raw"] and not c["has_seq"]], 3),
        ("other", chunks, 3),
    ]
    picked = []
    for name, pool, k in buckets:
        got = _pick(pool, k, used)
        for g in got:
            g["stratum"] = name
        picked.extend(got)
    if len(picked) < args.n:
        picked.extend(_pick(chunks, args.n - len(picked), used))
    picked = picked[: args.n]
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"n": len(picked), "chunks": picked}, ensure_ascii=False, indent=2), encoding="utf-8")
    ids_path = dest.with_name("pilot20_ids.jsonl")
    seen_doc = set()
    with ids_path.open("w", encoding="utf-8") as f:
        for item in picked:
            did = item.get("doc_id")
            if did and did not in seen_doc:
                seen_doc.add(did)
                f.write(json.dumps({"doc_id": did}, ensure_ascii=False) + "\n")
    print(f"{len(chunks)} 块中抽 {len(picked)} → {dest}")
    print(f"doc_id 列表（{len(seen_doc)} 篇）→ {ids_path}")
    for name, _, _ in buckets:
        print(f"  {name}: {sum(1 for x in picked if x.get('stratum') == name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
