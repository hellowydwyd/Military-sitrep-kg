"""把 CMNEE 正文转成 NewsDoc 信封，不使用 8 类标签。

python -m ingest.cmnee --src /data/users/wyd/data/KG/CMNEE --out data
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingest.envelope import SOURCE_CMNEE, build_doc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = Path("/data/users/wyd/data/KG/CMNEE")


def _iter_records(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        yield from data
        return
    if isinstance(data, dict):
        yield data


def convert_file(path: Path) -> list:
    docs = []
    for rec in _iter_records(path):
        cid = str(rec.get("id") or "").strip()
        text = str(rec.get("text") or "").strip()
        if not cid or not text:
            continue
        doc = build_doc(
            url=f"https://cmnee.local/doc/{cid}",
            title=text[:40].replace("\n", " "),
            body=text,
            publish_time=None,
            source=SOURCE_CMNEE,
            extra={"cmnee_id": cid, "cmnee_split": path.stem},
        )
        docs.append(doc)
    return docs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CMNEE 正文 → 新闻信封（丢弃闭式事件标签）")
    p.add_argument("--src", default=str(DEFAULT_SRC))
    p.add_argument("--out", default=str(ROOT / "data"))
    p.add_argument("--splits", default="train,valid,test")
    args = p.parse_args(argv)

    src = Path(args.src)
    out_dir = Path(args.out) / "raw" / "cmnee"
    out_dir.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    for split in [s.strip() for s in args.splits.split(",") if s.strip()]:
        path = src / f"{split}.json"
        if not path.is_file():
            print(f"跳过缺失 {path}")
            continue
        docs = convert_file(path)
        dest = out_dir / f"{split}.jsonl"
        with dest.open("w", encoding="utf-8") as f:
            for doc in docs:
                f.write(doc.to_json() + "\n")
                n_ok += 1
        print(f"{split}: {len(docs)} 篇 → {dest}")
    print(f"共 {n_ok} 篇信封。银标抽取：python -m extract --source cmnee --include-undated --from 1970-01-01 --to 2099-12-31 --max-docs 0 --out data/extract/openie_event_cmnee.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
