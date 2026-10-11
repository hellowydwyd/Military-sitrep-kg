"""S1：抽取结果落成 Report，不写世界 Event / STATE。

输入仍是 extract JSONL 的 `events`（或已改名的 `reports`）。
非法子串进 quarantine，不入图。
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from extract.validate import (
    canonicalize_role,
    evidence_sentence,
    is_pronoun,
    locate_span,
)
from graph.time import event_meta, inherit_anchor, usable_occur_anchor

LAYER = "report"


def _rid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _arg_sig(args: list) -> str:
    return ";".join(f"{a.get('role')}={a.get('text')}" for a in args)


def _hay(title: str, text: str) -> str:
    return (title or "") + "\n" + (text or "")


def validate_report_spans(ev: dict, haystack: str) -> list[str]:
    """入图硬门槛：trigger / raw_time / 论元必须是原文子串。"""
    reasons: list[str] = []
    trigger = str(ev.get("trigger") or "").strip()
    if not trigger or is_pronoun(trigger):
        reasons.append("bad_trigger")
    elif not locate_span(trigger, haystack):
        reasons.append("trigger_not_in_text")
    raw_time = str(ev.get("raw_time") or "").strip()
    if raw_time and raw_time.lower() not in ("空", "无", "未知", "none", "null"):
        if not locate_span(raw_time, haystack):
            reasons.append("raw_time_not_in_text")
    n_ok = 0
    for arg in ev.get("arguments") or []:
        if not isinstance(arg, dict):
            continue
        role = canonicalize_role(str(arg.get("role") or ""))
        text = str(arg.get("text") or "").strip()
        if not role or not text:
            continue
        if is_pronoun(text) or not locate_span(text, haystack):
            reasons.append(f"arg_not_in_text:{text[:20]}")
            continue
        n_ok += 1
    if n_ok == 0:
        reasons.append("no_valid_args")
    return reasons


def _normalize_args(ev: dict, haystack: str) -> list[dict]:
    out = []
    for arg in ev.get("arguments") or []:
        if not isinstance(arg, dict):
            continue
        role = canonicalize_role(str(arg.get("role") or ""))
        text = str(arg.get("text") or "").strip()
        if not role or not text or is_pronoun(text):
            continue
        loc = locate_span(text, haystack)
        if not loc:
            continue
        out.append({"role": role, "text": text, "offset": [loc[0], loc[1]]})
    return out


def _weak_complete(rep: dict) -> bool:
    if not (rep.get("trigger") or "").strip():
        return False
    if not (rep.get("evidence") or "").strip():
        return False
    has_ent = any(a.get("role") in ("主体", "对象", "地点") for a in rep.get("arguments") or [])
    return has_ent


def iter_raw_reports(ch: dict) -> list[dict]:
    rows = ch.get("reports")
    if rows is None:
        rows = ch.get("events") or []
    return [r for r in rows if isinstance(r, dict)]


def load_extract(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_report_graph(rows: Iterable[dict]) -> dict[str, Any]:
    documents, chunks, reports, claims, quarantine = [], [], [], [], []
    edges: list[dict] = []
    seen_doc, seen_chunk = set(), set()

    for row in rows:
        if row.get("error"):
            continue
        doc_id = row["doc_id"]
        title = row.get("title") or ""
        if doc_id not in seen_doc:
            seen_doc.add(doc_id)
            documents.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "publish_time": row.get("publish_time"),
                    "url": row.get("url"),
                    "source": row.get("source"),
                }
            )
        for ch in row.get("chunks") or []:
            cid = ch["chunk_id"]
            text = ch.get("text") or ""
            hay = _hay(title, text)
            if cid not in seen_chunk:
                seen_chunk.add(cid)
                chunks.append(
                    {
                        "chunk_id": cid,
                        "doc_id": doc_id,
                        "ord": ch.get("ord", 0),
                        "text": text,
                    }
                )
                edges.append({"type": "CONTAINS", "from": doc_id, "to": cid})

            prev_abs = None
            for ev in iter_raw_reports(ch):
                reasons = validate_report_spans(ev, hay)
                trigger = str(ev.get("trigger") or "").strip()
                args = _normalize_args(ev, hay)
                rid = _rid(cid, trigger, _arg_sig(args or ev.get("arguments") or []))
                evidence = str(ev.get("evidence") or "").strip() or evidence_sentence(hay, trigger)
                rec = {
                    "report_id": rid,
                    "chunk_id": cid,
                    "doc_id": doc_id,
                    "trigger": trigger,
                    "kind": ev.get("kind") or "",
                    "raw_time": ev.get("raw_time") or "",
                    "evidence": evidence,
                    "arguments": args,
                    "title": title,
                    "url": row.get("url"),
                    "publish_time": row.get("publish_time"),
                    "layer": LAYER,
                }
                if reasons:
                    rec["drop"] = reasons
                    quarantine.append(rec)
                    continue
                anchor = inherit_anchor(
                    prev_abs,
                    text,
                    evidence,
                    str(ev.get("raw_time") or ""),
                    row.get("publish_time"),
                )
                meta = event_meta(ev, row.get("publish_time"), None, anchor)
                rec["t_event_hint"] = meta.get("day") or ""
                rec["t_event_end_hint"] = meta.get("end") or ""
                rec["time_precision"] = meta.get("precision") or "unknown"
                rec["time_background"] = bool(meta.get("background"))
                rec["tlink_cue"] = meta.get("cue") or ""
                rec["event_id"] = None  # S2 合并前不指向世界事件
                rec["weak_complete"] = _weak_complete(rec)
                reports.append(rec)
                edges.append({"type": "SOURCED_FROM", "from": rid, "to": cid})
                if usable_occur_anchor(rec.get("kind") or "", rec["t_event_hint"], rec["time_precision"], row.get("publish_time")):
                    prev_abs = rec["t_event_hint"]

            for tri in ch.get("triples") or []:
                head = str(tri.get("head") or "").strip()
                rel = str(tri.get("relation") or "").strip()
                tail = str(tri.get("tail") or "").strip()
                if not head or not rel or not tail:
                    continue
                if not locate_span(head, hay) or not locate_span(tail, hay):
                    quarantine.append(
                        {
                            "report_id": _rid(cid, "claim", head, rel, tail),
                            "chunk_id": cid,
                            "doc_id": doc_id,
                            "drop": ["claim_not_in_text"],
                            "head": head,
                            "relation": rel,
                            "tail": tail,
                            "layer": "claim",
                        }
                    )
                    continue
                cid_rel = _rid(cid, head, rel, tail)
                claims.append(
                    {
                        "claim_id": cid_rel,
                        "chunk_id": cid,
                        "doc_id": doc_id,
                        "head": head,
                        "relation": rel,
                        "tail": tail,
                        "claim_only": True,
                        "evidence": tri.get("evidence") or "",
                    }
                )
                edges.append({"type": "CLAIM", "from": cid_rel, "to": cid, "claim_only": True})

    return {
        "layer": "s1_report",
        "schema": "doc/事件图谱完整方案.md",
        "documents": documents,
        "chunks": chunks,
        "reports": reports,
        "events": [],
        "states": [],
        "claims": claims,
        "quarantine": quarantine,
        "edges": edges,
    }


def save_graph(graph: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_reports_jsonl(graph: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in graph.get("reports") or []]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def stats(graph: dict) -> dict:
    reps = graph.get("reports") or []
    quar = graph.get("quarantine") or []
    by_kind: dict[str, int] = defaultdict(int)
    by_prec: dict[str, int] = defaultdict(int)
    pub_impersonate = 0
    for r in reps:
        by_kind[r.get("kind") or ""] += 1
        by_prec[r.get("time_precision") or ""] += 1
        hint = (r.get("t_event_hint") or "")[:10]
        pub = (r.get("publish_time") or "")[:10]
        raw = str(r.get("raw_time") or "").strip()
        # 无 raw_time 却写成见报日 → 冒充（S1 不应出现）
        if not raw and hint and pub and hint == pub and r.get("time_precision") == "day":
            pub_impersonate += 1
    n_weak = sum(1 for r in reps if r.get("weak_complete"))
    return {
        "n_docs": len(graph.get("documents") or []),
        "n_chunks": len(graph.get("chunks") or []),
        "n_reports": len(reps),
        "n_events": len(graph.get("events") or []),
        "n_states": len(graph.get("states") or []),
        "n_claims": len(graph.get("claims") or []),
        "n_quarantine": len(quar),
        "n_weak_complete": n_weak,
        "weak_complete_rate": round(n_weak / len(reps), 4) if reps else None,
        "pub_impersonate": pub_impersonate,
        "by_kind": dict(by_kind),
        "by_precision": dict(sorted(by_prec.items())),
        "n_edges": len(graph.get("edges") or []),
    }


PROTECTED = frozenset({"sitrep.json", "sitrep.b1.json", "sitrep.b1_seq.json"})


def main(argv: list[str] | None = None) -> int:
    import argparse

    root = Path(__file__).resolve().parents[1]
    data = root / "data"
    p = argparse.ArgumentParser(description="S1：抽取 → Report 层（不写 Event/STATE，不覆盖 B1）")
    p.add_argument("--in", dest="src", default=str(data / "extract" / "openie_event_pilot.jsonl"))
    p.add_argument("--out", default=str(data / "graph" / "sitrep.s1.json"))
    p.add_argument("--reports-out", default=str(data / "eval" / "reports_s1.jsonl"))
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    out = Path(args.out)
    if out.name in PROTECTED and not args.force:
        print(f"拒绝覆盖 {out}（B0/B1 基线）。请用 --out data/graph/sitrep.s1.json")
        return 2
    src = Path(args.src)
    if not src.is_file():
        print(f"找不到输入 {src}")
        return 1

    rows = load_extract(src)
    g = build_report_graph(rows)
    path = save_graph(g, out)
    rpath = export_reports_jsonl(g, Path(args.reports_out))
    s = stats(g)

    print(f"写入 {path}")
    print(f"Report JSONL {rpath}")
    print(
        f"Document {s['n_docs']} | Chunk {s['n_chunks']} | "
        f"Report {s['n_reports']} | Event {s['n_events']} | STATE {s['n_states']} | "
        f"quarantine {s['n_quarantine']} | Claim {s['n_claims']}"
    )
    print(f"弱完备 {s['n_weak_complete']}/{s['n_reports']}  见报日冒充 {s['pub_impersonate']}")
    print("kind:", s["by_kind"])

    report = data / "reports" / "s1_report_layer.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# S1 Report 层验收",
        "",
        f"- 输入：`{src}`",
        f"- 图：`{path}`",
        f"- Report JSONL：`{rpath}`",
        f"- Document **{s['n_docs']}** | Chunk **{s['n_chunks']}** | Report **{s['n_reports']}**",
        f"- Event **{s['n_events']}**（必须为 0）| STATE **{s['n_states']}**（必须为 0）",
        f"- quarantine **{s['n_quarantine']}** | Claim **{s['n_claims']}**",
        f"- 弱完备 **{s['n_weak_complete']} / {s['n_reports']}**（{s['weak_complete_rate']}）",
        f"- 见报日冒充 **{s['pub_impersonate']}**（须为 0）",
        "",
        "## kind",
        "",
    ]
    for k, n in s["by_kind"].items():
        lines.append(f"- `{k or '空'}`: {n}")
    lines += ["", "## time_precision", ""]
    for k, n in s["by_precision"].items():
        lines.append(f"- `{k or '空'}`: {n}")
    lines += ["", "## 说明", "", "本层只有 Report。世界 Event 合并见 S2；STATE 见 S5。", ""]
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告 {report}")
    if s["n_events"] != 0 or s["n_states"] != 0:
        print("失败：S1 图不得含 Event/STATE")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

