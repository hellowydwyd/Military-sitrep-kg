"""时间模型评测：T1 短语金标 / T2 抽取对照 / T3 写图硬门槛。

python3 -m graph.time_eval
python3 -m graph.time_eval --build-b1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract.compare import load_rows, metrics
from graph.store import build_graph, load_openie, load_graph, save_graph, stats
from graph.time import event_meta
from reason.daily import events_on_day

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GOLD = DATA / "eval" / "time_gold.json"
SEQ_CUES = ("随后", "之后", "接着", "然后")


def _load_gold() -> dict:
    return json.loads(GOLD.read_text(encoding="utf-8"))


def run_t1(gold: dict) -> tuple[bool, list[str]]:
    lines = ["## T1 短语金标", ""]
    n_ok = n_fail = 0
    for case in gold.get("phrases") or []:
        ev = {"raw_time": case.get("raw_time") or ""}
        meta = event_meta(ev, case.get("publish_time"), None, case.get("prev_abs_day"))
        exp = case.get("expect") or {}
        bad = []
        if "event_time" in exp and (meta.get("day") or "") != exp["event_time"]:
            bad.append(f"day got `{meta.get('day')}` want `{exp['event_time']}`")
        if "event_time_end" in exp and (meta.get("end") or "") != exp["event_time_end"]:
            bad.append(f"end got `{meta.get('end')}` want `{exp['event_time_end']}`")
        if "time_precision" in exp and (meta.get("precision") or "") != exp["time_precision"]:
            bad.append(f"precision got `{meta.get('precision')}` want `{exp['time_precision']}`")
        if "tlink" in exp and (meta.get("tlink") or "") != exp["tlink"]:
            bad.append(f"tlink got `{meta.get('tlink')}` want `{exp['tlink']}`")
        if exp.get("forbid_day") and (meta.get("day") or "") == exp["forbid_day"]:
            bad.append(f"forbade `{exp['forbid_day']}`")
        if meta.get("precision") == "pub":
            bad.append("precision=pub")
        mark = "OK" if not bad else "FAIL"
        if bad:
            n_fail += 1
            lines.append(f"- **{mark}** `{case['id']}`: {'; '.join(bad)}")
        else:
            n_ok += 1
            lines.append(f"- {mark} `{case['id']}`")
    lines += ["", f"- 通过 **{n_ok}** / 失败 **{n_fail}**", ""]
    return n_fail == 0, lines


def _seq_chunks(rows: list[dict]) -> tuple[int, int]:
    n_seq = n_ge2 = 0
    for r in rows:
        for c in r.get("chunks") or []:
            text = c.get("text") or ""
            if not any(k in text for k in SEQ_CUES):
                continue
            n_seq += 1
            if len(c.get("events") or []) >= 2:
                n_ge2 += 1
    return n_seq, n_ge2


def run_t2(old_path: Path, new_path: Path) -> tuple[bool | None, list[str]]:
    lines = ["## T2 抽取对照（R1）", ""]
    if not new_path.is_file():
        lines += [f"- 跳过：还没有 `{new_path}`。R1 命令见开发记录 §8.5。", ""]
        return None, lines
    if not old_path.is_file():
        lines += [f"- 跳过：缺少 `{old_path}`", ""]
        return None, lines
    old, new = load_rows(old_path), load_rows(new_path)
    ids = [i for i in new if i in old]
    old_rows = [old[i] for i in ids]
    new_rows = [new[i] for i in ids]
    mo, mn = metrics(old_rows), metrics(new_rows)
    os_, o2 = _seq_chunks(old_rows)
    ns_, n2 = _seq_chunks(new_rows)
    lines += [
        f"- 对照篇数 **{len(ids)}**",
        f"- 事件条数 {mo['events']} → {mn['events']}（不强制下降）",
        f"- 空 raw_time % {mo['empty_raw_pct']} → {mn['empty_raw_pct']}",
        f"- 摘录含 trigger % {mo['evidence_has_trigger_pct']} → {mn['evidence_has_trigger_pct']}",
        f"- kind 覆盖 {mn.get('kinds') or '无'}",
        f"- 含随后/之后的块：旧 {o2}/{os_} 抽到≥2 条事件；新 {n2}/{ns_}",
        "",
    ]
    ok = True
    if mn["evidence_has_trigger_pct"] < 100:
        lines.append("- **FAIL** 新抽 evidence 未全覆盖 trigger")
        ok = False
    if not mn.get("kinds"):
        lines.append("- **FAIL** 新抽缺少 kind")
        ok = False
    else:
        n_ev = mn["events"] or 1
        covered = sum(mn["kinds"].values())
        if covered < n_ev:
            lines.append(f"- **FAIL** kind 未覆盖全部事件（{covered}/{n_ev}）")
            ok = False
    if mn["empty_raw_pct"] >= mo["empty_raw_pct"]:
        lines.append("- **FAIL** 空 raw_time 未下降")
        ok = False
    if ns_ and n2 <= o2 and n2 == 0:
        lines.append("- **FAIL** 含随后的块仍抽不出 ≥2 条事件")
        ok = False
    if ok:
        lines.append("- T2 硬门槛通过")
    lines.append("")
    return ok, lines


def _graph_hard(g: dict) -> tuple[bool, list[str]]:
    s = stats(g)
    prec = s.get("by_precision") or {}
    lines = [
        f"- 事件 {s['n_events']}；TLINK **{s['n_tlink']}**；NEXT_IN_TIME **{s.get('n_next_in_time') or 0}**",
        f"- precision：{dict(prec)}",
        f"- 空 raw 却有日历：**{s['empty_raw_dated']}**",
        f"- `2026-01-01` 且 day：**{s['jan1_day']}**",
    ]
    ok = True
    if prec.get("pub"):
        lines.append(f"- **FAIL** precision=pub 仍有 {prec['pub']}")
        ok = False
    if s["empty_raw_dated"]:
        lines.append("- **FAIL** 空 raw 不得填日历")
        ok = False
    if s["jan1_day"] > 5:
        lines.append("- **FAIL** 01-01 day 过多")
        ok = False
    tlinks = [e for e in g.get("edges") or [] if e.get("type") == "TLINK"]
    if not tlinks:
        lines.append("- **FAIL** 没有 TLINK")
        ok = False
    else:
        bad = [e for e in tlinks if not e.get("cue") or not e.get("chunk_id")]
        if bad:
            lines.append(f"- **FAIL** {len(bad)} 条 TLINK 缺 cue/chunk_id")
            ok = False
    return ok, lines


def _find_ev(graph: dict, doc_id: str, needle: str) -> list[dict]:
    return [
        e
        for e in graph.get("events") or []
        if e.get("doc_id") == doc_id and needle in (e.get("trigger") or "")
    ]


def run_t3(graph: dict, gold: dict, label: str) -> tuple[bool, list[str]]:
    lines = [f"## T3 写图（{label}）", ""]
    ok, hard = _graph_hard(graph)
    lines += hard
    docs = gold.get("docs") or {}
    strike = docs.get("lebanon_strike") or {}
    doc_id = strike.get("doc_id") or ""
    pair = strike.get("after_triggers") or ["发现", "发动打击"]
    earlier_n, later_n = pair[0], pair[1]
    e_ids = {e["event_id"] for e in _find_ev(graph, doc_id, earlier_n)}
    l_ids = {e["event_id"] for e in _find_ev(graph, doc_id, later_n)}
    linked = False
    for edge in graph.get("edges") or []:
        if edge.get("type") != "TLINK" or edge.get("order") != "AFTER":
            continue
        if edge.get("from") in l_ids and edge.get("to") in e_ids:
            linked = True
            break
    if not e_ids or not l_ids:
        lines.append(f"- TLINK 以军「随后」：旧抽可能未拆成两条（发现={len(e_ids)} 打击={len(l_ids)}），记为粒度不足")
    elif linked:
        lines.append("- OK 打击 AFTER 发现")
        day_ok = True
        for needle in pair:
            dated = [
                e
                for e in _find_ev(graph, doc_id, needle)
                if e.get("time_precision") == "day" and len(e.get("event_time") or "") == 10
            ]
            if not dated:
                lines.append(f"- **FAIL** `{needle}` 未继承到当天日历")
                ok = False
                day_ok = False
        if day_ok:
            e_days = {
                e["event_time"][:10]
                for e in _find_ev(graph, doc_id, earlier_n)
                if e.get("event_time")
            }
            l_days = {
                e["event_time"][:10]
                for e in _find_ev(graph, doc_id, later_n)
                if e.get("event_time")
            }
            if e_days and e_days == l_days:
                lines.append(f"- OK 发现/打击同属 {next(iter(e_days))}")
            else:
                lines.append("- **FAIL** 发现与打击日历不一致")
                ok = False
    else:
        lines.append("- **FAIL** 未连 打击 AFTER 发现")
        ok = False
    for ev in _find_ev(graph, doc_id, strike.get("forbidden_pub_trigger") or "发动打击"):
        pub = (ev.get("publish_time") or "")[:10]
        raw = (ev.get("raw_time") or "")
        if "随后" not in raw or ev.get("time_precision") != "day":
            continue
        siblings = [
            e
            for e in graph.get("events") or []
            if e.get("chunk_id") == ev.get("chunk_id")
            and e.get("event_id") != ev.get("event_id")
            and e.get("event_time") == ev.get("event_time")
            and "随后" not in (e.get("raw_time") or "")
        ]
        if ev.get("event_time") == pub and not siblings:
            lines.append("- **FAIL** 「随后」被写成见报日（无同块前件日）")
            ok = False
    air = docs.get("syria_airstrike") or {}
    aid = air.get("doc_id") or ""
    week_evs = [
        e
        for e in _find_ev(graph, aid, air.get("week_trigger") or "空袭")
        if "上周" in (e.get("raw_time") or "") or "上周" in (e.get("evidence") or "")
    ]
    leak = False
    for day in air.get("not_on_days") or []:
        ids = {e["event_id"] for e in events_on_day(graph, day)}
        if any(e["event_id"] in ids for e in week_evs):
            leak = True
            lines.append(f"- **FAIL** 上周空袭出现在 {day} 当日清单")
            ok = False
    if week_evs and not leak:
        lines.append("- OK 上周空袭不在 8/23、8/24 当日清单")
    elif not week_evs:
        lines.append("- 未找到 raw/evidence 含「上周」的空袭（抽粒度问题）")
    lines.append("")
    return ok, lines


def _daily_n(graph: dict, day: str) -> int:
    return len(events_on_day(graph, day))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="时间模型 T1/T2/T3")
    p.add_argument("--gold", default=str(GOLD))
    p.add_argument("--old-extract", default=str(DATA / "extract" / "openie_slice40.jsonl"))
    p.add_argument("--new-extract", default=str(DATA / "extract" / "openie_v2_slice40.jsonl"))
    p.add_argument("--b0", default=str(DATA / "graph" / "sitrep.json"))
    p.add_argument("--b1", default=str(DATA / "graph" / "sitrep.b1_slice40.json"))
    p.add_argument("--b2", default=str(DATA / "graph" / "sitrep.b2_slice40.json"))
    p.add_argument("--build-b1", action="store_true", help="从旧 slice40 建 B1 图")
    p.add_argument("--build-b2", action="store_true", help="从 v2 slice40 建 B2 图")
    p.add_argument("--out", default=str(DATA / "reports" / "time_ab.md"))
    args = p.parse_args(argv)
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))

    if args.build_b1:
        src = Path(args.old_extract)
        g = build_graph(load_openie(src))
        save_graph(g, Path(args.b1))
        print(f"B1 已写 {args.b1}  events={len(g['events'])}")
    if args.build_b2:
        src = Path(args.new_extract)
        if not src.is_file():
            print(f"没有 {src}，跳过 --build-b2")
        else:
            g = build_graph(load_openie(src))
            save_graph(g, Path(args.b2))
            print(f"B2 已写 {args.b2}  events={len(g['events'])}")

    t1_ok, t1_lines = run_t1(gold)
    t2_ok, t2_lines = run_t2(Path(args.old_extract), Path(args.new_extract))

    parts = ["# 时间模型对照", "", "B0=旧抽+旧写图；B1=旧抽+新写图；B2=新抽+新写图。", ""]
    parts += t1_lines + t2_lines
    gate = t1_ok
    if t2_ok is False:
        gate = False

    for path, label in (
        (Path(args.b0), "B0 全量旧图（只观察当日条数）"),
        (Path(args.b1), "B1"),
        (Path(args.b2), "B2"),
    ):
        if not path.is_file():
            parts += [f"## {label}", "", f"- 跳过：没有 `{path}`", ""]
            continue
        g = load_graph(path)
        if label.startswith("B0"):
            parts += [
                f"## {label}",
                "",
                f"- daily 08-24 **{_daily_n(g, '2026-08-24')}**（基线约 61）",
                f"- daily 07-14 **{_daily_n(g, '2026-07-14')}**（基线约 125）",
                "",
            ]
            continue
        ok, lines = run_t3(g, gold, label)
        parts += lines
        parts.append(f"- daily 08-21 {_daily_n(g, '2026-08-21')}；08-24 {_daily_n(g, '2026-08-24')}（观察）")
        parts.append("")
        if not ok:
            gate = False

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(parts)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n已写 {out}")
    if not t1_ok:
        print("T1 未通过")
        return 1
    if t2_ok is False:
        print("T2 未通过")
        return 1
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
