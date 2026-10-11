"""定向重抽含「随后/之后/接着/然后」的块，合并进旁路 jsonl，不覆盖 openie.jsonl。

python -m extract.patch_seq
python -m extract.patch_seq --resume
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from extract.llm import chat_json
from extract.prompt import SYSTEM, build_user_prompt, parse_llm_json
from extract.validate import validate_extraction

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEQ_CUES = ("随后", "之后", "接着", "然后")
BASELINE = DATA / "extract" / "openie.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _chunk_index(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for r in rows:
        for ch in r.get("chunks") or []:
            cid = ch.get("chunk_id") or ""
            if cid:
                out[cid] = ch
    return out


def _prompt(row: dict, ch: dict) -> str:
    return (
        f"【标题】{row.get('title') or ''}\n"
        f"【发稿时间】{row.get('publish_time') or ''}\n"
        f"【正文】\n{ch.get('text') or ''}"
    )


def _is_cue_chunk(ch: dict) -> bool:
    text = ch.get("text") or ""
    return any(c in text for c in SEQ_CUES)


def extract_chunk(row: dict, ch: dict) -> dict:
    raw = chat_json(SYSTEM, build_user_prompt(_prompt(row, ch)))
    parsed = parse_llm_json(raw)
    checked = validate_extraction(parsed, title=row.get("title") or "", body=ch.get("text") or "")
    out = dict(ch)
    out["triples"] = checked["triples"]
    out["events"] = checked["events"]
    out["dropped_triples"] = checked["dropped_triples"]
    out["dropped_events"] = checked["dropped_events"]
    return out


def merge_patched(base_rows: list[dict], patched: dict[str, dict]) -> list[dict]:
    out = []
    for row in base_rows:
        chunks = []
        changed = False
        for ch in row.get("chunks") or []:
            cid = ch.get("chunk_id") or ""
            if cid in patched:
                chunks.append(patched[cid])
                changed = True
            else:
                chunks.append(ch)
        item = dict(row)
        item["chunks"] = chunks
        if changed:
            item["seq_patched"] = True
        out.append(item)
    return out


def _ge2(ch: dict) -> bool:
    return len(ch.get("events") or []) >= 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="重抽顺序线索块，写入 openie_seq.jsonl")
    p.add_argument("--base", default=str(BASELINE))
    p.add_argument("--reuse", default=str(DATA / "extract" / "openie_v2_slice40.jsonl"))
    p.add_argument("--out", default=str(DATA / "extract" / "openie_seq.jsonl"))
    p.add_argument("--chunk-out", default=str(DATA / "extract" / "openie_seq_chunks.jsonl"))
    p.add_argument("--report", default=str(DATA / "reports" / "extract_seq.md"))
    p.add_argument("--resume", action="store_true")
    args = p.parse_args(argv)

    out_path = Path(args.out)
    if out_path.resolve() == BASELINE.resolve():
        print("拒绝覆盖 openie.jsonl。改用 --out data/extract/openie_seq.jsonl", file=sys.stderr)
        return 2

    base_rows = _load_jsonl(Path(args.base))
    if not base_rows:
        print(f"没有基线 {args.base}", file=sys.stderr)
        return 1
    reuse = _chunk_index(_load_jsonl(Path(args.reuse))) if args.reuse else {}
    chunk_out = Path(args.chunk_out)
    done = _chunk_index(_load_jsonl(chunk_out)) if args.resume else {}

    jobs: list[tuple[dict, dict]] = []
    reused = 0
    for row in base_rows:
        for ch in row.get("chunks") or []:
            if not _is_cue_chunk(ch):
                continue
            cid = ch.get("chunk_id") or ""
            if cid in done:
                continue
            if cid in reuse:
                done[cid] = reuse[cid]
                reused += 1
                continue
            jobs.append((row, ch))

    print(
        f"顺序线索块待抽 {len(jobs)}（复用 v2 {reused}，resume 已有 {len(done) - reused}）",
        flush=True,
    )
    chunk_out.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.resume and chunk_out.is_file() else "w"
    n_err = 0
    with chunk_out.open(mode, encoding="utf-8") as f:
        if mode == "w":
            for cid, ch in done.items():
                rec = {"chunk_id": cid, "source": "reuse", **ch}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for i, (row, ch) in enumerate(jobs, 1):
            cid = ch.get("chunk_id") or ""
            print(f"[{i}/{len(jobs)}] {(row.get('publish_time') or '')[:10]} {row.get('title') or ''}"[:80], flush=True)
            try:
                new_ch = extract_chunk(row, ch)
                rec = {
                    "doc_id": row.get("doc_id"),
                    "title": row.get("title"),
                    "publish_time": row.get("publish_time"),
                    "source": "llm",
                    **new_ch,
                }
                done[cid] = new_ch
            except Exception as exc:
                n_err += 1
                print(f"  [error] {exc}", file=sys.stderr)
                rec = {
                    "doc_id": row.get("doc_id"),
                    "chunk_id": cid,
                    "error": str(exc),
                    "source": "error",
                }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            nev = len((done.get(cid) or {}).get("events") or [])
            print(f"  events={nev}", flush=True)

    patched = {cid: ch for cid, ch in done.items() if ch.get("events") is not None}
    merged = merge_patched(base_rows, patched)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in merged:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    old_cue = []
    for row in base_rows:
        for ch in row.get("chunks") or []:
            if _is_cue_chunk(ch):
                old_cue.append(ch)
    new_cue = [patched[c.get("chunk_id")] for c in old_cue if c.get("chunk_id") in patched]
    old_ge2 = sum(1 for c in old_cue if _ge2(c))
    new_ge2 = sum(1 for c in new_cue if _ge2(c))
    n_docs = sum(1 for r in merged if r.get("seq_patched"))
    lines = [
        "# 顺序线索块定向重抽",
        "",
        f"- 基线 `{args.base}`；旁路 `{out_path}`（未覆盖 openie.jsonl）",
        f"- 线索块 **{len(old_cue)}**；写入 **{len(new_cue)}**；复用 v2 **{reused}**；失败 **{n_err}**",
        f"- 含随后类线索抽到 ≥2 条事件：旧 **{old_ge2}/{len(old_cue)}** → 新 **{new_ge2}/{len(new_cue)}**",
        f"- 被补丁的文档 **{n_docs}** / {len(base_rows)}",
        "",
    ]
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print(f"JSONL {out_path}  报告 {args.report}")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
