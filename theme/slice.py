"""查询窗 ThemeSlice：核心 / 衍生 / 迁移 / 生命周期。只读故事库，不写回。"""

from __future__ import annotations

import hashlib
import json
import calendar
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from theme.config import (
    CORE_MAX,
    DELTA_DORMANT,
    DELTA_SLICE,
    ETA_SLOPE,
    LAMBDA_SCORE,
    M_MIN,
    M_MIN_CH,
    REPORT_DIR,
    RHO_CORE,
    ROOT,
    SLICE_OVERLAP,
    SMOOTH_DAYS,
    THEME_DIR,
    THETA_IN,
    THETA_M,
    THETA_SH,
    THETA_SP,
    THETA_ATTACH,
    THETA_THEME,
    THETA_UNLABELED,
    WINDOW_FROM,
    WINDOW_TO,
    Z_EPS,
    Z_MAX,
)
from theme.embed import load_embed
from theme.judge import apply_groups, judge_groups
from theme.name import apply_names
from theme.theaters import cannot_link, primary_theater, theaters_of


def default_assign_tag() -> str:
    return f"tune_split_t{THETA_IN:.2f}_d{DELTA_DORMANT}_sp{THETA_SP:.2f}"


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _iso(d: date) -> str:
    return d.isoformat()


def _days_inclusive(a: str, b: str) -> list[str]:
    x, y = _d(a), _d(b)
    out = []
    while x <= y:
        out.append(_iso(x))
        x += timedelta(days=1)
    return out


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _norm(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v)) or 1.0
    return (v / n).astype(np.float32)


def _trailing_mean(arr: np.ndarray, win: int) -> np.ndarray:
    out = np.zeros(len(arr), dtype=np.float64)
    c = 0.0
    for i, x in enumerate(arr):
        c += float(x)
        if i >= win:
            c -= float(arr[i - win])
        k = min(i + 1, win)
        out[i] = c / k
    return out


def _stage(n: np.ndarray, *, delta: int, eta: float) -> str:
    if len(n) == 0:
        return "dormant"
    tail = n[-delta:] if len(n) >= delta else n
    if float(tail.sum()) == 0:
        return "dormant"
    if len(n) >= 2 and n[-1] > 0 and n[-2] > 0 and (len(n) == 2 or n[-3] == 0):
        return "emerge"
    tilde = _trailing_mean(n.astype(np.float64), 3)
    slope = float((tilde[-1] - tilde[-3]) / 2.0) if len(tilde) >= 3 else 0.0
    if slope > eta:
        return "grow"
    if slope < -eta:
        return "decay"
    return "persist"


def _z_score(daily: dict[str, int], tb: str) -> float:
    """近 3 日均 vs 再往前 7 日。无历史则 0。"""
    hist_days = _days_inclusive(_iso(_d(tb) - timedelta(days=9)), _iso(_d(tb) - timedelta(days=3)))
    rec_days = _days_inclusive(_iso(_d(tb) - timedelta(days=2)), tb)
    hist = np.array([daily.get(d, 0) for d in hist_days], dtype=np.float64)
    rec = np.array([daily.get(d, 0) for d in rec_days], dtype=np.float64)
    if float(hist.sum()) == 0:
        return 0.0
    sd = float(hist.std())
    if sd < Z_EPS:
        return 0.0
    z = (float(rec.mean()) - float(hist.mean())) / (sd + Z_EPS)
    return float(max(0.0, min(Z_MAX, z)))


def _columns(ta: str, tb: str, *, delta: int, overlap: int) -> list[tuple[str, str]]:
    step = max(1, delta - overlap)
    cols = []
    start = _d(ta)
    end = _d(tb)
    while start <= end:
        stop = min(start + timedelta(days=delta - 1), end)
        cols.append((_iso(start), _iso(stop)))
        start += timedelta(days=step)
        if stop == end:
            break
    return cols


def _display_title(a: dict) -> str:
    return (a.get("title_zh") or a.get("title") or "").strip()


def _rep_title(docs: list[dict]) -> str:
    if not docs:
        return ""
    docs = sorted(docs, key=lambda a: (a.get("day") or "", a.get("doc_id") or ""))
    return _display_title(docs[-1])


def _recent_titles(docs: list[dict], n: int = 3) -> list[dict]:
    docs = sorted(docs, key=lambda a: (a.get("day") or "", a.get("doc_id") or ""), reverse=True)
    out, seen = [], set()
    for a in docs:
        t = _display_title(a)
        if not t or t in seen:
            continue
        seen.add(t)
        out.append({"day": a["day"], "title": t})
        if len(out) >= n:
            break
    return out


def _load_split_log(tag: str) -> list[dict]:
    p = THEME_DIR / f"splits_{tag}.jsonl"
    if not p.is_file():
        return []
    return _load_jsonl(p)


def _story_theaters(docs: list[dict]) -> frozenset[str]:
    th: set[str] = set()
    for a in docs:
        th |= set(theaters_of(a.get("title_zh") or a.get("title") or ""))
    return frozenset(th)


def _agglomerate(items: list[dict], theta: float) -> list[dict]:
    """质心加权合并：余弦 ≥ θ 且战场可并才并。"""
    clusters = [
        {
            "ids": list(x["ids"]),
            "mu": x["mu"],
            "th": set(x["th"]),
            "mass": int(x["mass"]),
            "docs": list(x.get("docs") or []),
        }
        for x in items
    ]
    while len(clusters) >= 2:
        best: tuple[float, int, int] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if cannot_link(clusters[i]["th"], clusters[j]["th"]):
                    continue
                s = float(clusters[i]["mu"] @ clusters[j]["mu"])
                if s >= theta and (best is None or s > best[0]):
                    best = (s, i, j)
        if best is None:
            break
        _s, i, j = best
        a, b = clusters[i], clusters[j]
        n_a, n_b = max(a["mass"], 1), max(b["mass"], 1)
        clusters[i] = {
            "ids": a["ids"] + b["ids"],
            "mu": _norm(a["mu"] * n_a + b["mu"] * n_b),
            "th": set(a["th"]) | set(b["th"]),
            "mass": a["mass"] + b["mass"],
            "docs": list(a.get("docs") or []) + list(b.get("docs") or []),
        }
        del clusters[j]
    return clusters


def _refresh_cand(
    cand: list[dict],
    *,
    ta: str,
    tb: str,
    m_min: int,
    by_id: dict,
) -> list[dict]:
    refreshed = []
    for c in cand:
        mass, daily, docs = _daily_of(c.get("docs") or [], ta, tb)
        if mass < m_min:
            continue
        last = max((a.get("day") or "" for a in docs), default="")
        first = min((a.get("day") or "" for a in docs), default="")
        life_from = min(first, ta) if first else ta
        _, life_daily, _ = _daily_of(docs, life_from, tb)
        life_arr = np.array(
            [life_daily.get(d, 0) for d in _days_inclusive(life_from, tb)], dtype=np.float64
        )
        tree = list(dict.fromkeys(c.get("tree") or []))
        lead = (
            max(tree, key=lambda sid: (sum(1 for a in docs if a.get("story_id") == sid), sid))
            if tree
            else c.get("root")
        )
        c.update(
            {
                "mass": mass,
                "daily": daily,
                "docs": docs,
                "tree": tree,
                "first": first,
                "last": last,
                "z": _z_score(life_daily, tb),
                "life_arr": life_arr,
                "theaters": sorted(_story_theaters(docs)),
                "th": set(_story_theaters(docs)),
                "root": lead,
                "title0": (by_id.get(lead) or {}).get("title0") or c.get("title0") or "",
            }
        )
        refreshed.append(c)
    return refreshed


def _rescue_leftovers(
    cand: list[dict],
    assigns: list[dict],
    vec_of: dict[str, np.ndarray],
    ta: str,
    tb: str,
    theta: float,
    m_min: int,
) -> tuple[list[dict], dict]:
    """单篇也参与：先贴已有团（≥θ），再彼此成团（≥θ）。真散留下。不把碎稿送进 LLM。"""
    owned: set[str] = set()
    mus: list[tuple[dict, np.ndarray | None, set[str]]] = []
    for c in cand:
        for a in c.get("docs") or []:
            did = a.get("doc_id") or ""
            if did:
                owned.add(did)
        mu = _centroid([x["doc_id"] for x in (c.get("docs") or [])], vec_of)
        th = set(c.get("theaters") or c.get("th") or [])
        mus.append((c, mu, th))

    leftover = [
        a
        for a in assigns
        if ta <= a.get("day", "") <= tb
        and (a.get("doc_id") or "")
        and a["doc_id"] not in owned
        and a["doc_id"] in vec_of
    ]
    leftover.sort(key=lambda a: (a.get("day") or "", a.get("doc_id") or ""))
    n_leftover = len(leftover)

    attached = 0
    still: list[dict] = []
    for a in leftover:
        v = vec_of[a["doc_id"]]
        th = set(theaters_of(_display_title(a)))
        best_i, best_s = -1, -1.0
        for i, (_c, mu, cth) in enumerate(mus):
            if mu is None:
                continue
            if cannot_link(th, cth):
                continue
            s = float(v @ mu)
            if s > best_s:
                best_s, best_i = s, i
        if best_i >= 0 and best_s >= theta:
            c, _mu, _cth = mus[best_i]
            c.setdefault("docs", []).append(a)
            sid = a.get("story_id") or ""
            tree = c.setdefault("tree", [])
            if sid and sid not in tree:
                tree.append(sid)
            mu = _centroid([x["doc_id"] for x in c["docs"]], vec_of)
            cth = set(_story_theaters(c["docs"]))
            c["theaters"] = sorted(cth)
            c["th"] = cth
            mus[best_i] = (c, mu, cth)
            attached += 1
        else:
            still.append(a)

    units = []
    for a in still:
        mu = vec_of.get(a["doc_id"])
        if mu is None:
            continue
        b = primary_theater(_display_title(a))
        th = {b} if b else set()
        units.append(
            {
                "ids": [a.get("story_id") or a["doc_id"]],
                "mu": mu,
                "th": th,
                "mass": 1,
                "docs": [a],
            }
        )
    new_groups = []
    for g in _agglomerate(units, theta):
        docs = g.get("docs") or []
        if len(docs) < m_min:
            continue
        new_groups.append(g)
    stats = {
        "n_leftover": n_leftover,
        "n_attached": attached,
        "n_new_groups": len(new_groups),
        "n_new_docs": sum(len(g.get("docs") or []) for g in new_groups),
    }
    stats["n_true_scatter"] = n_leftover - attached - stats["n_new_docs"]
    return new_groups, stats


def _centroid(doc_ids: list[str], vec_of: dict[str, np.ndarray]) -> np.ndarray | None:
    acc = None
    n = 0
    for did in doc_ids:
        v = vec_of.get(did)
        if v is None:
            continue
        acc = v.copy() if acc is None else acc + v
        n += 1
    if acc is None or n == 0:
        return None
    return _norm(acc)


def load_story_lib(tag: str) -> tuple[list[dict], list[dict]]:
    stories = _load_jsonl(THEME_DIR / f"stories_{tag}.jsonl")
    assigns = _load_jsonl(THEME_DIR / f"assign_{tag}.jsonl")
    if not stories or not assigns:
        raise FileNotFoundError(f"缺少接线结果: {tag}（先 python -m theme assign --split）")
    return stories, assigns


def build_index(stories: list[dict], assigns: list[dict]):
    by_id = {s["story_id"]: s for s in stories}
    children: dict[str, list[str]] = defaultdict(list)
    parent_of: dict[str, str] = {}
    for s in stories:
        p = s.get("parent_story") or ""
        parent_of[s["story_id"]] = p
        if p:
            children[p].append(s["story_id"])

    def root_of(sid: str) -> str:
        seen = set()
        while parent_of.get(sid):
            if sid in seen:
                break
            seen.add(sid)
            sid = parent_of[sid]
        return sid

    descendants: dict[str, list[str]] = {}

    def walk(sid: str) -> list[str]:
        if sid in descendants:
            return descendants[sid]
        acc = [sid]
        for ch in children.get(sid, []):
            acc.extend(walk(ch))
        descendants[sid] = acc
        return acc

    for s in stories:
        walk(s["story_id"])

    docs_by_story: dict[str, list[dict]] = defaultdict(list)
    for a in assigns:
        docs_by_story[a["story_id"]].append(a)

    return by_id, children, parent_of, root_of, descendants, docs_by_story


def _mass_daily(
    sids: list[str],
    docs_by_story: dict[str, list[dict]],
    ta: str,
    tb: str,
) -> tuple[int, dict[str, int], list[dict]]:
    daily: dict[str, int] = defaultdict(int)
    docs = []
    for sid in sids:
        for a in docs_by_story.get(sid, []):
            if ta <= a["day"] <= tb:
                daily[a["day"]] += 1
                docs.append(a)
    return sum(daily.values()), dict(daily), docs


def _daily_of(docs: list[dict], ta: str, tb: str) -> tuple[int, dict[str, int], list[dict]]:
    daily: dict[str, int] = defaultdict(int)
    kept = []
    for a in docs:
        if ta <= a["day"] <= tb:
            daily[a["day"]] += 1
            kept.append(a)
    return sum(daily.values()), dict(daily), kept


def _pieces_theater(
    stories: list[dict],
    docs_by_story: dict[str, list[dict]],
    vec_of: dict[str, np.ndarray],
    ta: str,
    tb: str,
    m_min: int,
    theta: float,
    theta_u: float | None = None,
) -> list[dict]:
    """窗内稿按标题主战场切开，只在块内向量合并。无标签块用更严阈值。混线按标题拆，不写回。"""
    theta_u = theta if theta_u is None else theta_u
    buckets: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for s in stories:
        sid = s["story_id"]
        for a in docs_by_story.get(sid, []):
            if not (ta <= a["day"] <= tb):
                continue
            b = primary_theater(_display_title(a))
            buckets[b][sid].append(a)
    pieces = []
    for theater, by_story in buckets.items():
        units = []
        for sid, docs in by_story.items():
            if len(docs) < m_min:
                continue
            mu = _centroid([x["doc_id"] for x in docs], vec_of)
            if mu is None:
                continue
            units.append(
                {
                    "ids": [sid],
                    "mu": mu,
                    "th": {theater} if theater else set(),
                    "mass": len(docs),
                    "docs": docs,
                }
            )
        for g in _agglomerate(units, theta if theater else theta_u):
            pieces.append({"tree": g["ids"], "docs": g["docs"], "partition": theater or "∅"})
    return pieces


def build_slice(
    *,
    ta: str,
    tb: str,
    tag: str | None = None,
    rho: float = RHO_CORE,
    m_min: int = M_MIN,
    m_min_ch: int = M_MIN_CH,
    delta: int = DELTA_DORMANT,
    lam: float = LAMBDA_SCORE,
    delta_slice: int = DELTA_SLICE,
    overlap: int = SLICE_OVERLAP,
    core_max: int = CORE_MAX,
    theta_m: float = THETA_M,
    theta_sh: float = THETA_SH,
    eta: float = ETA_SLOPE,
    smooth: int = SMOOTH_DAYS,
    cluster_mode: str = "judge",
    theta_theme: float = THETA_THEME,
    theta_unlabeled: float = THETA_UNLABELED,
    theta_attach: float = THETA_ATTACH,
    name_mode: str = "auto",
) -> dict:
    tag = tag or default_assign_tag()
    if ta < WINDOW_FROM or tb > WINDOW_TO:
        raise ValueError(f"查询窗须在 {WINDOW_FROM}～{WINDOW_TO}，收到 {ta}～{tb}")
    stories, assigns = load_story_lib(tag)
    cat_by = {
        r["doc_id"]: r
        for r in _load_jsonl(THEME_DIR / "catalog.jsonl")
        if r.get("doc_id")
    } if (THEME_DIR / "catalog.jsonl").is_file() else {}
    by_id, children, parent_of, root_of, descendants, docs_by_story = build_index(stories, assigns)
    ids, mat = load_embed()
    vec_of = {i: mat[j] for j, i in enumerate(ids)}
    cal = _days_inclusive(ta, tb)

    pieces: list[dict] = []
    if cluster_mode == "tree":
        roots = [s["story_id"] for s in stories if not (s.get("parent_story") or "")]
        for rid in roots:
            tree = descendants[rid]
            _mass, _daily, docs = _mass_daily(tree, docs_by_story, ta, tb)
            pieces.append({"tree": tree, "docs": docs, "partition": "tree"})
    elif cluster_mode in ("theater", "judge"):
        pieces = _pieces_theater(
            stories, docs_by_story, vec_of, ta, tb, m_min, theta_theme, theta_u=theta_unlabeled
        )
    else:
        units = []
        for s in stories:
            sid = s["story_id"]
            mass, _daily, docs = _mass_daily([sid], docs_by_story, ta, tb)
            if mass < m_min:
                continue
            mu = _centroid([x["doc_id"] for x in docs], vec_of)
            if mu is None:
                continue
            units.append(
                {
                    "ids": [sid],
                    "mu": mu,
                    "th": set(_story_theaters(docs)),
                    "mass": mass,
                    "docs": docs,
                }
            )
        for g in _agglomerate(units, theta_theme):
            pieces.append({"tree": g["ids"], "docs": g.get("docs") or [], "partition": "query"})

    cand = []
    for piece in pieces:
        tree = piece["tree"]
        mass, daily, docs = _daily_of(piece["docs"], ta, tb)
        last = max((a.get("day") or "" for a in docs), default="")
        first = min((a.get("day") or "" for a in docs), default="")
        if mass < m_min:
            continue
        if cluster_mode == "theater" and (piece.get("partition") or "∅") == "∅":
            continue
        life_from = min(first, ta) if first else ta
        _, life_daily, _ = _daily_of(docs, life_from, tb) if docs else (0, {}, [])
        # 生命周期仍用该片在窗内的日序列（切开后不再把整条 L1 混线算进去）
        life_arr = np.array(
            [life_daily.get(d, 0) for d in _days_inclusive(life_from, tb)], dtype=np.float64
        )
        z = _z_score(life_daily, tb)
        lead = max(
            tree,
            key=lambda sid: (sum(1 for a in docs if a.get("story_id") == sid), sid),
        )
        cand.append(
            {
                "cid": f"c{len(cand):02d}",
                "root": lead,
                "mass": mass,
                "daily": daily,
                "docs": docs,
                "tree": tree,
                "first": first,
                "last": last,
                "z": z,
                "life_arr": life_arr,
                "theaters": sorted(_story_theaters(docs)),
                "th": set(_story_theaters(docs)),
                "partition": piece.get("partition") or "",
                "title0": (by_id.get(lead) or {}).get("title0") or "",
            }
        )
    judge_model = ""
    rescue_stats = {
        "n_leftover": 0,
        "n_attached": 0,
        "n_new_groups": 0,
        "n_new_docs": 0,
        "n_true_scatter": 0,
    }
    if cluster_mode == "judge" and cand:
        print(f"LLM 结构判定 {len(cand)} 个候选…", flush=True)
        groups, judge_model = judge_groups(cand)
        cand = apply_groups(cand, groups)
        print(f"判定后 {len(cand)} 题  model={judge_model}", flush=True)

    if cluster_mode in ("judge", "theater", "query"):
        new_groups, rescue_stats = _rescue_leftovers(
            cand, assigns, vec_of, ta, tb, theta_attach, m_min
        )
        for i, g in enumerate(new_groups):
            cand.append(
                {
                    "cid": f"r{i:02d}",
                    "docs": g.get("docs") or [],
                    "tree": list(dict.fromkeys(g.get("ids") or [])),
                    "th": set(g.get("th") or []),
                    "theaters": sorted(x for x in (g.get("th") or []) if x),
                    "partition": "rescue",
                    "name": "",
                    "title0": "",
                    "root": (g.get("ids") or [""])[0],
                }
            )
        print(
            f"散稿回收 贴靠 {rescue_stats['n_attached']}  "
            f"新团 {rescue_stats['n_new_groups']}（{rescue_stats['n_new_docs']} 篇）  "
            f"真散 {rescue_stats['n_true_scatter']}",
            flush=True,
        )

    cand = _refresh_cand(
        cand, ta=ta, tb=tb, m_min=m_min, by_id=by_id
    )

    max_m = max((c["mass"] for c in cand), default=0) or 1
    for c in cand:
        c["score"] = lam * (c["mass"] / max_m) + (1.0 - lam) * (c["z"] / Z_MAX if Z_MAX else 0.0)
    cand.sort(key=lambda c: (-c["score"], -c["mass"], c.get("root") or ""))
    if cluster_mode == "judge":
        cand.sort(key=lambda c: (-c["mass"], -c["score"], c.get("root") or ""))
        n_core_raw = len(cand)
        cores = cand[:core_max]
        dropped = cand[core_max:]
    else:
        max_s = max((c["score"] for c in cand), default=0.0)
        core_all = [c for c in cand if c["score"] >= rho * max_s] if max_s > 0 else []
        core_all.sort(key=lambda c: (-c["score"], -c["mass"], c["root"]))
        n_core_raw = len(core_all)
        cores = core_all[:core_max]
        dropped = core_all[core_max:]
    for i, c in enumerate(cores):
        c["theme_id"] = f"t{i:02d}"
    for i, c in enumerate(dropped):
        c["theme_id"] = f"m{i:02d}"

    themes = []
    story_theme: dict[str, str] = {}
    lead_of_theme: dict[str, str] = {}
    for rank, c in enumerate(cores):
        tid = c["theme_id"]
        for sid in c["tree"]:
            story_theme[sid] = tid
        by_sid: dict[str, list[dict]] = defaultdict(list)
        for a in c["docs"]:
            by_sid[a.get("story_id") or ""].append(a)
        story_mass: list[tuple[int, str, list[dict]]] = []
        for sid, sdocs in by_sid.items():
            if not sid:
                continue
            story_mass.append((len(sdocs), sid, sdocs))
        story_mass.sort(key=lambda x: (-x[0], x[1]))
        lead_sid = story_mass[0][1] if story_mass else c["root"]
        lead_docs = story_mass[0][2] if story_mass else c["docs"]
        lead_of_theme[tid] = lead_sid
        name = _rep_title(lead_docs) or _rep_title(c["docs"]) or c["title0"]
        raw = [c["daily"].get(d, 0) for d in cal]
        sm = _trailing_mean(np.array(raw, dtype=np.float64), smooth)
        daily = [{"day": d, "n": int(raw[i]), "n_smooth": round(float(sm[i]), 3)} for i, d in enumerate(cal)]
        themes.append(
            {
                "theme_id": tid,
                "parent_theme": 0,
                "root_story": c["root"],
                "lead_story": lead_sid,
                "mass": c["mass"],
                "z": round(c["z"], 4),
                "score": round(c["score"], 4),
                "stage": _stage(c["life_arr"], delta=delta, eta=eta),
                "color_key": rank,
                "daily": daily,
                "story_ids": list(c["tree"]),
                "n_stories": len(c["tree"]),
                "theaters": c.get("theaters") or [],
                "partition": c.get("partition") or "",
                "name": c.get("name") or name,
                "headline": name,
                "recent": _recent_titles(c["docs"]),
                "title0": c["title0"],
            }
        )

    minors = []
    for rank, c in enumerate(dropped):
        tid = c["theme_id"]
        for sid in c["tree"]:
            story_theme[sid] = story_theme.get(sid) or tid
        by_sid = defaultdict(list)
        for a in c["docs"]:
            by_sid[a.get("story_id") or ""].append(a)
        story_mass = [(len(sdocs), sid, sdocs) for sid, sdocs in by_sid.items() if sid]
        story_mass.sort(key=lambda x: (-x[0], x[1]))
        lead_sid = story_mass[0][1] if story_mass else c["root"]
        lead_docs = story_mass[0][2] if story_mass else c["docs"]
        lead_of_theme[tid] = lead_sid
        headline = _rep_title(lead_docs) or _rep_title(c["docs"]) or c["title0"]
        raw = [c["daily"].get(d, 0) for d in cal]
        sm = _trailing_mean(np.array(raw, dtype=np.float64), smooth)
        daily = [{"day": d, "n": int(raw[i]), "n_smooth": round(float(sm[i]), 3)} for i, d in enumerate(cal)]
        minors.append(
            {
                "theme_id": tid,
                "parent_theme": 0,
                "root_story": c["root"],
                "lead_story": lead_sid,
                "mass": c["mass"],
                "z": round(c["z"], 4),
                "score": round(c["score"], 4),
                "stage": _stage(c["life_arr"], delta=delta, eta=eta),
                "color_key": len(themes) + rank,
                "daily": daily,
                "story_ids": list(c["tree"]),
                "n_stories": len(c["tree"]),
                "theaters": c.get("theaters") or [],
                "partition": c.get("partition") or "",
                "name": c.get("name") or headline,
                "headline": headline,
                "recent": _recent_titles(c["docs"]),
                "title0": c["title0"],
                "role": "minor",
            }
        )

    derived = []
    for c in cores:
        tid = c["theme_id"]
        lead = lead_of_theme[tid]
        lead_name = next((t["name"] for t in themes if t["theme_id"] == tid), "")
        by_sid: dict[str, list[dict]] = defaultdict(list)
        for a in c["docs"]:
            by_sid[a.get("story_id") or ""].append(a)
        for sid, sdocs in by_sid.items():
            if not sid or sid == lead:
                continue
            mass = len(sdocs)
            if mass < m_min_ch:
                continue
            derived.append(
                {
                    "story_id": sid,
                    "parent_story": lead,
                    "theme_id": tid,
                    "mass": mass,
                    "name": _rep_title(sdocs) or (by_id.get(sid) or {}).get("title0") or "",
                    "headline": _rep_title(sdocs) or (by_id.get(sid) or {}).get("title0") or "",
                    "parent_name": lead_name,
                    "title0": (by_id.get(sid) or {}).get("title0") or "",
                }
            )
    derived.sort(key=lambda x: (-x["mass"], x["story_id"]))
    derived_ids = {d["story_id"] for d in derived}

    doc_theme: dict[str, str] = {}
    for c in cores + dropped:
        for a in c["docs"]:
            did = a.get("doc_id") or ""
            if did:
                doc_theme[did] = c["theme_id"]

    # 窗内故事（独占稿量 > 0）
    slice_stories = []
    for s in stories:
        mass, daily, docs = _mass_daily([s["story_id"]], docs_by_story, ta, tb)
        if mass <= 0:
            continue
        raw = [daily.get(d, 0) for d in cal]
        tid = story_theme.get(s["story_id"]) or ""
        slice_stories.append(
            {
                "story_id": s["story_id"],
                "parent_story": s.get("parent_story") or "",
                "theme_id": tid,
                "mass": mass,
                "role": (
                    "lead"
                    if story_theme.get(s["story_id"])
                    and s["story_id"] == lead_of_theme.get(story_theme[s["story_id"]])
                    else (
                        "derived"
                        if s["story_id"] in derived_ids
                        else ("core_tree" if tid else "other")
                    )
                ),
                "daily": [{"day": d, "n": int(raw[i])} for i, d in enumerate(cal) if raw[i]],
                "doc_ids": [a["doc_id"] for a in docs],
                "title0": s.get("title0") or "",
            }
        )
    slice_stories.sort(key=lambda x: (-x["mass"], x["story_id"]))

    slice_docs = []
    other_daily = {d: 0 for d in cal}
    n_by_day = {d: 0 for d in cal}
    core_ids = {t["theme_id"] for t in themes}
    for a in assigns:
        if not (ta <= a["day"] <= tb):
            continue
        tid = doc_theme.get(a["doc_id"]) or ""
        n_by_day[a["day"]] += 1
        if tid not in core_ids:
            other_daily[a["day"]] += 1
        c = cat_by.get(a["doc_id"]) or {}
        slice_docs.append(
            {
                "doc_id": a["doc_id"],
                "day": a["day"],
                "title": a.get("title") or c.get("title") or "",
                "title_zh": a.get("title_zh") or c.get("title_zh") or "",
                "source": a.get("source") or c.get("source") or "",
                "source_name": a.get("source_name") or c.get("source_name") or "",
                "lang": a.get("lang") or c.get("lang") or "",
                "url": c.get("url") or a.get("url") or "",
                "story_id": a["story_id"],
                "theme_id": tid,
            }
        )
    slice_docs.sort(key=lambda x: (x["day"], x["doc_id"]))

    # 守恒：主题 raw n + other = 当日篇数
    cons_fail = []
    for i, d in enumerate(cal):
        core_n = sum(t["daily"][i]["n"] for t in themes)
        got = core_n + other_daily[d]
        if got != n_by_day[d]:
            cons_fail.append({"day": d, "core": core_n, "other": other_daily[d], "docs": n_by_day[d]})

    other = {"daily": [{"day": d, "n": other_daily[d]} for d in cal]}

    # 列间匹配（L2 核心，窗内 centroid）
    cols = _columns(ta, tb, delta=delta_slice, overlap=overlap)
    col_units = []
    for c0, c1 in cols:
        units = []
        for t in themes:
            docs = [a for a in next(c["docs"] for c in cores if c["theme_id"] == t["theme_id"]) if c0 <= a["day"] <= c1]
            mu = _centroid([x["doc_id"] for x in docs], vec_of)
            if mu is None or len(docs) < m_min:
                continue
            units.append(
                {
                    "id": t["theme_id"],
                    "mu": mu,
                    "mass": len(docs),
                    "name": _rep_title(docs) or t.get("name") or "",
                }
            )
        col_units.append(units)

    migration = []
    for i in range(len(col_units) - 1):
        left, right = col_units[i], col_units[i + 1]
        day = cols[i + 1][0]
        edges = []
        for u in left:
            for v in right:
                s = float(u["mu"] @ v["mu"])
                if s >= theta_m:
                    edges.append((u["id"], v["id"], s))
        out_deg = defaultdict(int)
        in_deg = defaultdict(int)
        for u, v, _s in edges:
            out_deg[u] += 1
            in_deg[v] += 1
        left_ids = {u["id"] for u in left}
        right_ids = {v["id"] for v in right}
        left_u = {u["id"]: u for u in left}
        right_u = {v["id"]: v for v in right}
        for u in left_ids:
            if out_deg[u] == 0:
                migration.append(
                    {
                        "from_id": u,
                        "to_id": "",
                        "day": day,
                        "op": "decay",
                        "sim": None,
                        "mass_from": left_u[u]["mass"],
                        "mass_to": 0,
                        "name_from": left_u[u]["name"],
                        "name_to": "",
                    }
                )
        for v in right_ids:
            if in_deg[v] == 0:
                migration.append(
                    {
                        "from_id": "",
                        "to_id": v,
                        "day": day,
                        "op": "emerge",
                        "sim": None,
                        "mass_from": 0,
                        "mass_to": right_u[v]["mass"],
                        "name_from": "",
                        "name_to": right_u[v]["name"],
                    }
                )
        for u, v, s in edges:
            if out_deg[u] > 1:
                op = "split"
            elif in_deg[v] > 1:
                op = "merge"
            elif out_deg[u] == 1 and in_deg[v] == 1:
                op = "continue" if s >= theta_sh else "shift"
            else:
                op = "shift"
            migration.append(
                {
                    "from_id": u,
                    "to_id": v,
                    "day": day,
                    "op": op,
                    "sim": round(s, 4),
                    "mass_from": left_u[u]["mass"],
                    "mass_to": right_u[v]["mass"],
                    "name_from": left_u[u]["name"],
                    "name_to": right_u[v]["name"],
                }
            )

    columns_view = []
    for c0, c1 in cols:
        units = []
        for t in themes:
            n = sum(d["n"] for d in t["daily"] if c0 <= d["day"] <= c1)
            if n <= 0:
                continue
            docs = [
                a
                for a in next(c["docs"] for c in cores if c["theme_id"] == t["theme_id"])
                if c0 <= a["day"] <= c1
            ]
            units.append(
                {
                    "theme_id": t["theme_id"],
                    "mass": n,
                    "name": _rep_title(docs) or t.get("name") or "",
                }
            )
        other_n = sum(d["n"] for d in other["daily"] if c0 <= d["day"] <= c1)
        columns_view.append({"from": c0, "to": c1, "units": units, "other": other_n})

    split_events = []
    for s in _load_split_log(tag):
        day = s.get("day") or ""
        if not (ta <= day <= tb):
            continue
        pid, cid = s.get("parent") or "", s.get("child") or ""
        tid = story_theme.get(cid) or story_theme.get(pid) or ""
        if not tid:
            continue
        split_events.append(
            {
                "day": day,
                "parent": pid,
                "child": cid,
                "n_parent": s.get("n_parent"),
                "n_child": s.get("n_child"),
                "cosine": s.get("cosine"),
                "parent_title": (by_id.get(pid) or {}).get("title0") or "",
                "child_title": s.get("child_title") or (by_id.get(cid) or {}).get("title0") or "",
                "theme_id": tid,
            }
        )

    payload = {
        "window": {"from": ta, "to": tb},
        "assign_tag": tag,
        "params": {
            "rho": rho,
            "lambda": lam,
            "m_min": m_min,
            "m_min_ch": m_min_ch,
            "delta": delta,
            "delta_slice": delta_slice,
            "overlap": overlap,
            "core_max": core_max,
            "theta_m": theta_m,
            "theta_sh": theta_sh,
            "eta": eta,
            "smooth_days": smooth,
            "cluster_mode": cluster_mode,
            "theta_theme": theta_theme,
            "theta_unlabeled": theta_unlabeled,
            "theta_attach": theta_attach,
            "judge_model": judge_model,
        },
        "themes": themes,
        "minors": minors,
        "stories": slice_stories,
        "docs": slice_docs,
        "other": other,
        "migration": migration,
        "derived": derived,
        "columns": columns_view,
        "split_events": split_events,
    }
    replay = hashlib.sha256(
        json.dumps(
            {
                "themes": [(t["theme_id"], t["mass"], t["story_ids"]) for t in themes],
                "docs": [(d["doc_id"], d["story_id"], d["theme_id"]) for d in slice_docs],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    n_docs = len(slice_docs)
    other_n = sum(other_daily.values())
    core_n = n_docs - other_n
    media = Counter(
        d.get("source_name") or d.get("source") or "未知" for d in slice_docs
    )
    payload["metrics"] = {
        "n_docs": n_docs,
        "n_days": len(cal),
        "n_days_with_docs": sum(1 for d in cal if n_by_day[d]),
        "n_core_raw": n_core_raw,
        "n_core": len(themes),
        "n_minor": len(minors),
        "n_derived": len(derived),
        "n_stories_in_w": len(slice_stories),
        "core_docs": core_n,
        "minor_docs": sum(t["mass"] for t in minors),
        "other_docs": other_n,
        "other_rate": round(other_n / n_docs, 4) if n_docs else 0.0,
        "conservation_ok": not cons_fail,
        "conservation_fail": cons_fail[:8],
        "n_migration": len(migration),
        "n_columns": len(cols),
        "columns": [{"from": a, "to": b} for a, b in cols],
        "slice_sha256": replay,
        "n_core_dropped_cap": len(dropped),
        "cluster_mode": cluster_mode,
        "n_cand": len(cand),
        "judge_model": judge_model,
        "n_leftover": rescue_stats.get("n_leftover", 0),
        "n_attached": rescue_stats.get("n_attached", 0),
        "n_rescue_groups": rescue_stats.get("n_new_groups", 0),
        "n_rescue_docs": rescue_stats.get("n_new_docs", 0),
        "n_true_scatter": rescue_stats.get("n_true_scatter", 0),
        "n_media": len(media),
        "media": [{"name": k, "n": v} for k, v in media.most_common()],
    }
    apply_names(payload, mode=name_mode)
    payload["metrics"]["name_mode"] = (payload.get("params") or {}).get("name_mode")
    return payload


STAGE_ZH = {"decay": "衰退", "persist": "平台", "grow": "生长", "emerge": "出现", "dormant": "休眠"}
OP_ZH = {
    "continue": "延续",
    "shift": "焦点漂移",
    "split": "分裂",
    "merge": "汇合",
    "decay": "消退",
    "emerge": "新现",
}


def _url_index() -> dict[str, str]:
    p = THEME_DIR / "catalog.jsonl"
    out: dict[str, str] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        did = row.get("doc_id") or ""
        if did:
            out[did] = row.get("url") or ""
    return out


def slice_key(window: dict) -> str:
    a, b = window["from"][:10], window["to"][:10]
    if a == b:
        return a
    am, bm = a[:7], b[:7]
    if am == bm:
        last = calendar.monthrange(int(a[:4]), int(a[5:7]))[1]
        if a.endswith("-01") and b == f"{am}-{last:02d}":
            return am
        return f"{a}~{b}"
    last_b = calendar.monthrange(int(b[:4]), int(b[5:7]))[1]
    if a.endswith("-01") and b == f"{bm}-{last_b:02d}":
        return f"{am}~{bm}"
    return f"{a}~{b}"


def _story_tree(t: dict, articles: list[dict], stories_by: dict) -> dict:
    lead = t.get("lead_story") or ""
    by: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        by[a.get("story_id") or ""].append(a)

    def node(sid: str, role: str) -> dict:
        docs = by.get(sid) or []
        st = stories_by.get(sid) or {}
        title = st.get("title0") or (docs[0]["title"] if docs else sid)
        return {
            "story_id": sid,
            "name": title,
            "mass": len(docs) or int(st.get("mass") or 0),
            "role": role,
            "parent": st.get("parent_story") or "",
        }

    branches = [node(sid, "branch") for sid in by if sid and sid != lead]
    branches.sort(key=lambda x: (-x["mass"], x["story_id"]))
    return {"lead": node(lead, "lead") if lead else None, "branches": branches}


def _slim_theme(t: dict, *, articles: list[dict] | None = None, stories_by: dict | None = None) -> dict:
    arts = articles or []
    return {
        "theme_id": t["theme_id"],
        "name": t.get("name") or t.get("title0") or t["theme_id"],
        "headline": t.get("headline") or "",
        "mass": t["mass"],
        "stage": t["stage"],
        "stage_zh": STAGE_ZH.get(t["stage"], t["stage"]),
        "score": t["score"],
        "z": t["z"],
        "color_key": t["color_key"],
        "recent": t.get("recent") or [],
        "lead_story": t.get("lead_story") or "",
        "theaters": t.get("theaters") or [],
        "partition": t.get("partition") or "",
        "n_stories": t.get("n_stories") or len(t.get("story_ids") or []),
        "daily": t["daily"],
        "articles": arts,
        "tree": _story_tree(t, arts, stories_by or {}),
        "narrative": t.get("narrative") or {"lede": "", "beats": []},
    }


def _slim_slice(out: dict) -> dict:
    urls = _url_index()
    articles_by: dict[str, list[dict]] = defaultdict(list)
    for d in out.get("docs") or []:
        tid = d.get("theme_id") or ""
        if not tid:
            continue
        articles_by[tid].append(
            {
                "day": d.get("day") or "",
                "title": d.get("title_zh") or d.get("title") or "",
                "title_orig": d.get("title") or "",
                "url": d.get("url") or urls.get(d.get("doc_id") or "", ""),
                "story_id": d.get("story_id") or "",
                "source_name": d.get("source_name")
                or ("环球网军事" if d.get("source") != "rss_api" else "RSS"),
                "source": d.get("source") or "",
            }
        )
    for tid in articles_by:
        articles_by[tid].sort(key=lambda x: (x["day"], x["title"]))
    stories_by = {s["story_id"]: s for s in (out.get("stories") or []) if s.get("story_id")}
    return {
        "window": out["window"],
        "assign_tag": out["assign_tag"],
        "metrics": {
            k: out["metrics"][k]
            for k in (
                "n_docs",
                "n_core",
                "n_minor",
                "n_derived",
                "core_docs",
                "minor_docs",
                "other_docs",
                "other_rate",
                "conservation_ok",
                "n_columns",
                "n_migration",
                "n_attached",
                "n_rescue_groups",
                "n_true_scatter",
                "n_media",
                "media",
            )
            if k in out["metrics"]
        },
        "themes": [
            _slim_theme(t, articles=articles_by.get(t["theme_id"]) or [], stories_by=stories_by)
            for t in out["themes"]
        ],
        "minors": [
            _slim_theme(t, articles=articles_by.get(t["theme_id"]) or [], stories_by=stories_by)
            for t in (out.get("minors") or [])
        ],
        "derived": out.get("derived") or [],
        "split_events": out.get("split_events") or [],
        "columns": out.get("columns") or [],
        "migration": out.get("migration") or [],
        "other": out.get("other") or {"daily": []},
    }


def _export_viz(out: dict) -> None:
    """打包调参段各月及跨月切片，前端可切窗。"""
    web = ROOT / "theme" / "web"
    web.mkdir(parents=True, exist_ok=True)
    slim = _slim_slice(out)
    slices: dict[str, dict] = {}
    for p in sorted(THEME_DIR.glob("slice_*.json")):
        if "theater" in p.name:
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "window" not in data:
            continue
        slices[slice_key(data["window"])] = _slim_slice(data)
    key = slice_key(slim["window"])
    slices[key] = slim
    if "2026-04~2026-09" in slices:
        slices.pop("2026-04~2026-08", None)
    payload = (
        "window.SLICES = "
        + json.dumps(slices, ensure_ascii=False)
        + ";\nwindow.SLICE = window.SLICES["
        + json.dumps(key)
        + "];\n"
    )
    (web / "data.js").write_text(payload, encoding="utf-8")


def write_slice(out: dict) -> tuple[Path, Path]:
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ta, tb = out["window"]["from"], out["window"]["to"]
    stem = f"slice_{ta}_{tb}"
    json_path = THEME_DIR / f"{stem}.json"
    try:
        from theme.narrative import fill_slice as fill_narrative

        fill_narrative(out)
    except Exception:
        pass
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    m = out["metrics"]
    lines = [
        f"# ThemeSlice `{ta}`–`{tb}`",
        "",
        f"- 接线 `{out['assign_tag']}`  聚类=`{out['params'].get('cluster_mode')}` θ_theme=`{out['params'].get('theta_theme')}`  起名=`{out['params'].get('name_mode')}`  ρ=`{out['params']['rho']}` λ=`{out['params']['lambda']}`  Δ=`{out['params']['delta_slice']}` overlap=`{out['params']['overlap']}`",
        f"- 窗内文档 **{m['n_docs']}**（有稿日 {m['n_days_with_docs']}/{m['n_days']}）",
        f"- 核心 **{m['n_core']}**（截断前 {m['n_core_raw']}，带宽外次要 {m.get('n_minor', m['n_core_dropped_cap'])}） 衍生 **{m['n_derived']}**  窗内故事 {m['n_stories_in_w']}",
        f"- 核心稿 {m['core_docs']} / 其余合计 {m['other_docs']}（次要 {m.get('minor_docs', 0)} + 碎屑，占比 {m['other_rate']}）",
        f"- 散稿回收 贴靠 {m.get('n_attached', 0)}  新团 {m.get('n_rescue_groups', 0)}（{m.get('n_rescue_docs', 0)} 篇）  真散 {m.get('n_true_scatter', 0)}",
        f"- 守恒 conservation_ok=`{m['conservation_ok']}`  列 {m['n_columns']}  迁移边 {m['n_migration']}",
        f"- sha=`{m['slice_sha256'][:12]}…`",
        "",
        "## 核心议题（短名是簇的概括，不是某一篇标题）",
        "",
        "| mass | 阶段 | 议题 | 近稿 |",
        "|------|------|------|------|",
    ]
    stage_zh = {"decay": "衰退", "persist": "平台", "grow": "生长", "emerge": "出现", "dormant": "休眠"}
    for t in out["themes"]:
        title = (t.get("name") or t.get("title0") or "").replace("|", "\\|")
        head = (t.get("headline") or "").replace("|", "\\|")
        lines.append(
            f"| {t['mass']} | {stage_zh.get(t['stage'], t['stage'])} | {title} | {head} |"
        )
    minors = out.get("minors") or []
    if minors:
        lines += ["", "## 次要议题（独立成题，不进核心带宽；河图里计入「其余合计」）", "", "| mass | 阶段 | 议题 | 近稿 |", "|------|------|------|------|"]
        for t in minors:
            title = (t.get("name") or t.get("title0") or "").replace("|", "\\|")
            head = (t.get("headline") or "").replace("|", "\\|")
            lines.append(
                f"| {t['mass']} | {stage_zh.get(t['stage'], t['stage'])} | {title} | {head} |"
            )
    der = out.get("derived") or []
    if der:
        lines += ["", "## 衍生（同一查询主题内、非主线的旁支）", "", "| mass | 核心 | 旁支 | 近稿 |", "|------|------|------|------|"]
        tname = {t["theme_id"]: t.get("name") or t.get("title0") or t["theme_id"] for t in out["themes"]}
        for d in der[:20]:
            title = (d.get("name") or d.get("title0") or "").replace("|", "\\|")
            core = (tname.get(d["theme_id"]) or "").replace("|", "\\|")
            head = (d.get("headline") or "").replace("|", "\\|")
            lines.append(f"| {d['mass']} | {core} | {title} | {head} |")
        if len(der) > 20:
            lines.append(f"| … | 其余 {len(der) - 20} | |")
    se = out.get("split_events") or []
    if se:
        lines += ["", "## 窗内结构分裂（2-means）", "", "| 日 | 父 → 子 |", "|----|---------|"]
        for s in se[:12]:
            pt = (s.get("parent_title") or s.get("parent") or "").replace("|", "\\|")[:24]
            ct = (s.get("child_title") or s.get("child") or "").replace("|", "\\|")[:24]
            lines.append(f"| {s['day']} | {pt} → {ct} |")
    ops = {}
    op_zh = {
        "continue": "延续",
        "shift": "焦点漂移",
        "split": "分裂",
        "merge": "汇合",
        "decay": "消退",
        "emerge": "新现",
    }
    for e in out.get("migration") or []:
        ops[e["op"]] = ops.get(e["op"], 0) + 1
    if ops:
        lines += [
            "",
            "## 列间迁移",
            "",
            ", ".join(f"{op_zh.get(k, k)}={v}" for k, v in sorted(ops.items())),
        ]
        notable = [e for e in out.get("migration") or [] if e["op"] != "continue"]
        if notable:
            lines += ["", "| 日 | 算子 | 从 | 到 |", "|----|------|----|----|"]
            for e in notable[:16]:
                a = (e.get("name_from") or e.get("from_id") or "∅").replace("|", "\\|")[:28]
                b = (e.get("name_to") or e.get("to_id") or "∅").replace("|", "\\|")[:28]
                lines.append(f"| {e['day']} | {op_zh.get(e['op'], e['op'])} | {a} | {b} |")
    md = REPORT_DIR / f"theme_{stem}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _export_viz(out)
    return json_path, md
