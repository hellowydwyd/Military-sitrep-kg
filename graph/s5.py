"""S5：持续类 Event → STATE（valid-time）。不覆盖 S4。单方指控 / 返航不开关区间。"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from graph.s1 import save_graph

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PROTECTED = frozenset(
    {
        "sitrep.json",
        "sitrep.b1.json",
        "sitrep.b1_seq.json",
        "sitrep.s1.json",
        "sitrep.s2.json",
        "sitrep.s3.json",
        "sitrep.s4.json",
        "sitrep.s6.json",
    }
)

OPEN_PRED = ("抵达", "进驻", "部署", "调往", "驻扎")
CLOSE_PRED = ("离开", "撤离", "飞离", "撤出")
NO_STATE_KIND = ("计划",)
NO_STATE_PRED = ("袭击", "发动", "空袭", "打击", "警告", "谴责", "批准", "返航")
SUBJECT_ALIASES = (
    ("美军飞机", "两架美国军机", "这些飞机", "美国军机"),
    ("“林肯”号航母", "林肯号", "林肯", "美国海军“亚伯拉罕·林肯”号核动力航空母舰", "“林肯”号"),
)


def _d(s: str) -> str:
    return (s or "")[:10]


def _parse(s: str) -> date | None:
    raw = _d(s)
    if len(raw) == 7:
        raw = raw + "-01"
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _aligned_ids(ev: dict, key: str) -> list[dict]:
    return list(ev.get(key) or [])


def _actor_ids(ev: dict) -> list[str]:
    return [a["id"] for a in _aligned_ids(ev, "actors_aligned") if a.get("id")]


def _loc_ids(ev: dict) -> list[str]:
    return [a["id"] for a in _aligned_ids(ev, "locations_aligned") if a.get("id")]


def _canon(ev: dict, key: str) -> list[str]:
    return [a.get("canonical") or a.get("mention") or "" for a in _aligned_ids(ev, key)]


def _same_subject(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    for grp in SUBJECT_ALIASES:
        if any(x in a for x in grp) and any(x in b for x in grp):
            return True
    return False


def _event_day(ev: dict) -> str:
    if ev.get("time_precision") == "month" and len(ev.get("event_time") or "") == 7:
        return ev["event_time"] + "-01"
    return _d(ev.get("event_time") or "")


def _blob(ev: dict) -> str:
    return " ".join(
        [
            ev.get("predicate") or "",
            ev.get("trigger_surface") or "",
            " ".join(_canon(ev, "actors_aligned")),
            " ".join(_canon(ev, "locations_aligned")),
        ]
    )


def _event_evidence(graph: dict, ev: dict) -> str:
    bits = [_blob(ev)]
    for r in graph.get("reports") or []:
        if r.get("event_id") == ev.get("event_id"):
            bits.append(r.get("evidence") or "")
            bits.append(r.get("trigger") or "")
    return " ".join(bits)


def derive_states(graph: dict) -> list[dict]:
    events = list(graph.get("events") or [])
    opens: list[dict] = []
    closes: list[tuple[dict, dict]] = []  # (event, match_key)

    for ev in events:
        pred = ev.get("predicate") or ""
        kind = ev.get("kind") or ""
        if kind in NO_STATE_KIND or pred in NO_STATE_PRED:
            continue
        day = _event_day(ev)
        locs = _loc_ids(ev)
        actors = _actor_ids(ev)
        canons = _canon(ev, "actors_aligned")

        if kind == "发生" and pred in OPEN_PRED and day and actors and locs:
            for aid in actors:
                for lid in locs:
                    opens.append(
                        {
                            "state_type": "部署于",
                            "subject_id": aid,
                            "subject": next((c for c in canons if c), aid),
                            "object_id": lid,
                            "object": next((x.get("canonical") for x in ev.get("locations_aligned") or [] if x.get("id") == lid), lid),
                            "t_valid_from": day,
                            "t_valid_to": None,
                            "inducing_event_id": ev["event_id"],
                            "ending_event_id": None,
                            "confidence": "corroborated" if ev.get("status") == "corroborated" else "singleton",
                        }
                    )

        leave_cue = pred in CLOSE_PRED or any(k in _event_evidence(graph, ev) for k in ("离开", "飞离", "已离开"))
        n_rep = len(ev.get("report_ids") or [])
        if leave_cue and locs and (kind == "发生" or ev.get("status") == "corroborated" or n_rep >= 2):
            closes.append((ev, {"locs": locs, "actors": canons + actors, "day": day or _d(ev.get("event_time"))}))

    # 按 from 排序后应用关闭
    opens.sort(key=lambda s: s["t_valid_from"] or "")
    used_close = set()
    for ev, ck in closes:
        day = ck["day"]
        if not day:
            continue
        for s in opens:
            if s["t_valid_to"]:
                continue
            if s["object_id"] not in ck["locs"]:
                continue
            subj_ok = any(_same_subject(s["subject"], x) or s["subject_id"] == x for x in ck["actors"])
            if not subj_ok and not ck["actors"]:
                subj_ok = True
            # 关闭事件主体常是发言人：证据须点名该部署对象（或别名），不能只凭同地点
            if not subj_ok and s["state_type"] == "部署于":
                blob = _event_evidence(graph, ev)
                if (s["subject"] and s["subject"] in blob) or _same_subject(s["subject"], blob):
                    subj_ok = True
            if not subj_ok:
                continue
            if s["t_valid_from"] and day < s["t_valid_from"]:
                continue
            s["t_valid_to"] = day
            s["ending_event_id"] = ev["event_id"]
            used_close.add(ev["event_id"])
            break

    # 同一 (subject, object, type) 不得两条开区间：保留较早 from
    opens.sort(key=lambda s: (s["subject_id"], s["object_id"], s["state_type"], s["t_valid_from"] or ""))
    kept: list[dict] = []
    open_key: set[tuple] = set()
    for s in opens:
        key = (s["subject_id"], s["object_id"], s["state_type"])
        if s["t_valid_to"] is None:
            if key in open_key:
                continue
            open_key.add(key)
        kept.append(s)

    for i, s in enumerate(kept, 1):
        s["state_id"] = f"state:{i:04d}"
    return kept


def state_holds(s: dict, as_of: str) -> bool:
    t = _parse(as_of)
    if not t:
        return False
    a = _parse(s.get("t_valid_from") or "")
    b = _parse(s.get("t_valid_to") or "") if s.get("t_valid_to") else None
    if not a:
        return False
    if t < a:
        return False
    if b is not None and t >= b:
        return False
    return True


def states_as_of(graph: dict, as_of: str, *, entity: str = "", place: str = "") -> list[dict]:
    out = []
    for s in graph.get("states") or []:
        if not state_holds(s, as_of):
            continue
        if entity and entity not in (s.get("subject"), s.get("subject_id")) and entity not in (s.get("subject") or "") and not _same_subject(s.get("subject") or "", entity):
            continue
        if place and place not in (s.get("object"), s.get("object_id")) and place not in (s.get("object") or ""):
            continue
        out.append(s)
    return out


def s5_checks(graph: dict) -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    # 至多一条开区间
    open_keys = [((s["subject_id"], s["object_id"], s["state_type"])) for s in graph.get("states") or [] if not s.get("t_valid_to")]
    add("开区间唯一", len(open_keys) == len(set(open_keys)), f"open={open_keys}")

    bez_10 = states_as_of(graph, "2026-08-10", place="贝兹梅尔")
    bez_23 = states_as_of(graph, "2026-08-23", place="贝兹梅尔")
    add("as_of 08-10 军机在贝兹梅尔", len(bez_10) >= 1, f"n={len(bez_10)} {[(s.get('t_valid_from'), s.get('t_valid_to')) for s in bez_10]}")
    add("as_of 08-23 军机不在贝兹梅尔", len(bez_23) == 0, f"n={len(bez_23)}")

    lin_20 = states_as_of(graph, "2026-08-20", entity="林肯")
    lin_open = [s for s in lin_20 if not s.get("t_valid_to") or (s.get("t_valid_to") or "") > "2026-08-20"]
    add("林肯号 08-20 部署未因返航关闭", len(lin_open) >= 1, f"{[(s.get('object'), s.get('t_valid_from'), s.get('t_valid_to')) for s in lin_20]}")

    warn = [s for s in graph.get("states") or [] if "警告" in json.dumps(s, ensure_ascii=False)]
    add("警告不生成 STATE", len(warn) == 0, f"n={len(warn)}")
    n_ok = sum(1 for c in checks if c["ok"])
    return {"n": len(checks), "n_ok": n_ok, "pass": n_ok == len(checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="S5：STATE valid-time + as_of")
    p.add_argument("--in", dest="src", default=str(DATA / "graph" / "sitrep.s4.json"))
    p.add_argument("--out", default=str(DATA / "graph" / "sitrep.s5.json"))
    p.add_argument("--as-of", dest="as_of", default="")
    p.add_argument("--entity", default="")
    p.add_argument("--at", dest="place", default="")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    if args.as_of:
        path = DATA / "graph" / "sitrep.s5.json"
        if Path(args.src).name.endswith("s5.json") and Path(args.src).is_file():
            path = Path(args.src)
        if not path.is_file():
            print("请先 python -m graph.s5")
            return 1
        g = json.loads(path.read_text(encoding="utf-8"))
        rows = states_as_of(g, args.as_of, entity=args.entity, place=args.place)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    if out.name in PROTECTED and not args.force:
        print(f"拒绝覆盖 {out}")
        return 2
    src = Path(args.src)
    if not src.is_file():
        print(f"找不到 {src}，请先 python -m graph.s4")
        return 1
    g = json.loads(src.read_text(encoding="utf-8"))
    states = derive_states(g)
    g["states"] = states
    g["layer"] = "s5_state"
    # 边
    edges = [e for e in g.get("edges") or [] if e.get("type") != "STATE"]
    for s in states:
        edges.append(
            {
                "type": "STATE",
                "from": s["subject_id"],
                "to": s["object_id"],
                "state_id": s["state_id"],
                "state_type": s["state_type"],
                "t_valid_from": s["t_valid_from"],
                "t_valid_to": s["t_valid_to"],
                "inducing_event_id": s.get("inducing_event_id"),
                "ending_event_id": s.get("ending_event_id"),
            }
        )
    g["edges"] = edges
    path = save_graph(g, out)
    ev = s5_checks(g)
    print(f"写入 {path}")
    print(f"STATE {len(states)}（开区间 {sum(1 for s in states if not s.get('t_valid_to'))}）")
    print(f"种子检查 {ev['n_ok']}/{ev['n']} pass={ev['pass']}")
    for c in ev["checks"]:
        print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")

    report = DATA / "reports" / "s5_state.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# S5 STATE / as_of 验收",
        "",
        f"- 输入：`{src}`",
        f"- 输出：`{path}`",
        f"- STATE **{len(states)}** 条",
        "",
    ]
    for s in states:
        lines.append(
            f"- `{s['state_id']}` {s['subject']} -{s['state_type']}- {s['object']}  "
            f"[{s['t_valid_from']}, {s['t_valid_to'] or '+∞'})"
        )
    lines += ["", f"检查 **{ev['n_ok']}/{ev['n']}**", ""]
    for c in ev["checks"]:
        lines.append(f"- {'通过' if c['ok'] else '失败'} {c['name']} — {c['detail']}")
    lines += ["", "查询：`python -m graph.s5 --as-of 2026-08-10 --at 贝兹梅尔空军基地`", ""]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告 {report}")
    return 0 if ev["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
