"""单遍接线；可选条件球面 2-means 分裂。过去只增，分裂只把成员挪到子线。"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date
from pathlib import Path

import numpy as np

from theme.config import (
    DELTA_DORMANT,
    N_CHK,
    N_MIN,
    REPORT_DIR,
    THEME_DIR,
    THETA_IN,
    THETA_SP,
    TUNE_FROM,
    TUNE_TO,
)
from theme.embed import load_embed
from theme.theaters import cannot_link, theaters_of


def _day(s: str) -> date:
    return date.fromisoformat(s[:10])


def _delta_days(later: str, earlier: str) -> int:
    return (_day(later) - _day(earlier)).days


def _norm(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v)) or 1.0
    return (v / n).astype(np.float32)


def spherical_2means(vecs: np.ndarray, rng: np.random.RandomState, n_iter: int = 20) -> np.ndarray:
    n = vecs.shape[0]
    i0 = int(rng.randint(0, n))
    j0 = int(np.argmin(vecs @ vecs[i0]))
    c0, c1 = vecs[i0].copy(), vecs[j0].copy()
    labels = np.zeros(n, dtype=np.int32)
    for _ in range(n_iter):
        s0 = vecs @ c0
        s1 = vecs @ c1
        new = (s1 > s0).astype(np.int32)
        if int(new.sum()) in (0, n) or np.array_equal(new, labels):
            labels = new
            break
        labels = new
        if labels.sum() == 0 or labels.sum() == n:
            break
        c0 = _norm(vecs[labels == 0].mean(axis=0))
        c1 = _norm(vecs[labels == 1].mean(axis=0))
    return labels


class OnlineAssigner:
    def __init__(
        self,
        *,
        theta_in: float,
        delta: int,
        theta_sp: float = THETA_SP,
        n_chk: int = N_CHK,
        n_min: int = N_MIN,
        do_split: bool = False,
        both_today: bool = False,
        entity_block: bool = False,
        seed: int = 0,
    ):
        self.theta_in = theta_in
        self.delta = delta
        self.theta_sp = theta_sp
        self.n_chk = n_chk
        self.n_min = n_min
        self.do_split = do_split
        self.both_today = both_today
        self.entity_block = entity_block
        self.seed = seed
        self.centroids: list[np.ndarray] = []
        self.n: list[int] = []
        self.first: list[str] = []
        self.last: list[str] = []
        self.members: list[list[str]] = []
        self.member_days: list[list[str]] = []
        self.members_inclusive: list[list[str]] = []
        self.titles: list[str] = []
        self.parent: list[int] = []
        self.theaters: list[set[str]] = []
        self.doc_title: dict[str, str] = {}
        self.n_splits = 0
        self.split_log: list[dict] = []

    def _recompute(self, i: int, vec_of: dict[str, np.ndarray]) -> None:
        mem = self.members[i]
        if not mem:
            return
        acc = np.zeros_like(self.centroids[i])
        for did in mem:
            acc = acc + vec_of[did]
        self.centroids[i] = _norm(acc)
        self.n[i] = len(mem)
        days = self.member_days[i]
        self.first[i] = min(days)
        self.last[i] = max(days)

    def _new(self, doc_id: str, day: str, vec: np.ndarray, title: str) -> tuple[int, float, bool]:
        i = len(self.centroids)
        self.centroids.append(vec.copy())
        self.n.append(1)
        self.first.append(day)
        self.last.append(day)
        self.members.append([doc_id])
        self.member_days.append([day])
        self.members_inclusive.append([doc_id])
        self.titles.append(title)
        self.parent.append(-1)
        self.theaters.append(set(theaters_of(title)))
        self.doc_title[doc_id] = title
        return i, 1.0, True

    def _join(self, i: int, doc_id: str, day: str, vec: np.ndarray, title: str) -> None:
        n = self.n[i]
        self.centroids[i] = _norm(self.centroids[i] * n + vec)
        self.n[i] = n + 1
        self.last[i] = day
        self.members[i].append(doc_id)
        self.member_days[i].append(day)
        self.members_inclusive[i].append(doc_id)
        self.doc_title[doc_id] = title
        self.theaters[i] |= set(theaters_of(title))
        p = self.parent[i]
        while p >= 0:
            if doc_id not in self.members_inclusive[p]:
                self.members_inclusive[p].append(doc_id)
            p = self.parent[p]

    def assign(self, doc_id: str, day: str, vec: np.ndarray, title: str) -> tuple[int, float, bool]:
        if not self.centroids:
            return self._new(doc_id, day, vec, title)
        active = [
            i
            for i, last in enumerate(self.last)
            if 0 <= _delta_days(day, last) <= self.delta and self.members[i]
        ]
        if not active:
            return self._new(doc_id, day, vec, title)
        C = np.stack([self.centroids[i] for i in active], axis=0)
        sims = C @ vec
        order = np.argsort(-sims)
        th = theaters_of(title)
        for j in order:
            i = active[int(j)]
            sim = float(sims[int(j)])
            if sim < self.theta_in:
                break
            if self.entity_block and cannot_link(th, self.theaters[i]):
                continue
            self._join(i, doc_id, day, vec, title)
            return i, sim, False
        return self._new(doc_id, day, vec, title)

    def try_split_day(self, day: str, touched: set[int], vec_of: dict[str, np.ndarray]) -> None:
        if not self.do_split:
            return
        queue = list(sorted(touched))
        while queue:
            i = queue.pop(0)
            before = self.n_splits
            self._try_split(i, day, vec_of)
            if self.n_splits > before:
                queue.append(i)
                queue.append(len(self.centroids) - 1)

    def _try_split(self, i: int, day: str, vec_of: dict[str, np.ndarray]) -> None:
        mem = self.members[i]
        if len(mem) < self.n_chk:
            return
        vecs = np.stack([vec_of[d] for d in mem], axis=0)
        rng = np.random.RandomState((self.seed + i * 10007 + int(day.replace("-", ""))) % (2**31 - 1))
        labels = spherical_2means(vecs, rng)
        n1 = int(labels.sum())
        n0 = len(mem) - n1
        if min(n0, n1) < self.n_min:
            return
        c0 = _norm(vecs[labels == 0].mean(axis=0))
        c1 = _norm(vecs[labels == 1].mean(axis=0))
        if float(c0 @ c1) >= self.theta_sp:
            return
        days = np.array(self.member_days[i])
        today0 = int(((labels == 0) & (days == day)).sum())
        today1 = int(((labels == 1) & (days == day)).sum())
        if self.both_today and min(today0, today1) < 1:
            return
        # 较小一侧立为子线；根独占成员改为较大一侧
        child_lab = 0 if n0 <= n1 else 1
        child_ids = [mem[k] for k in range(len(mem)) if labels[k] == child_lab]
        child_days = [self.member_days[i][k] for k in range(len(mem)) if labels[k] == child_lab]
        keep_ids = [mem[k] for k in range(len(mem)) if labels[k] != child_lab]
        keep_days = [self.member_days[i][k] for k in range(len(mem)) if labels[k] != child_lab]
        if not child_ids or not keep_ids:
            return
        ci = len(self.centroids)
        self.members[i] = keep_ids
        self.member_days[i] = keep_days
        self._recompute(i, vec_of)
        title0 = self.doc_title.get(child_ids[0], "")
        acc = np.zeros_like(self.centroids[0])
        for did in child_ids:
            acc = acc + vec_of[did]
        self.centroids.append(_norm(acc))
        self.n.append(len(child_ids))
        self.first.append(min(child_days))
        self.last.append(max(child_days))
        self.members.append(child_ids)
        self.member_days.append(child_days)
        self.members_inclusive.append(list(child_ids))
        self.titles.append(title0)
        self.parent.append(i)
        child_th: set[str] = set()
        for did in child_ids:
            child_th |= set(theaters_of(self.doc_title.get(did, "")))
        self.theaters.append(child_th)
        keep_th: set[str] = set()
        for did in keep_ids:
            keep_th |= set(theaters_of(self.doc_title.get(did, "")))
        self.theaters[i] = keep_th
        self.n_splits += 1
        self.split_log.append(
            {
                "parent": f"s{i:04d}",
                "child": f"s{ci:04d}",
                "day": day,
                "n_parent": len(keep_ids),
                "n_child": len(child_ids),
                "cosine": round(float(c0 @ c1), 4),
                "parent_title": self.titles[i],
                "child_title": title0,
            }
        )


def load_catalog(path: Path | None = None) -> list[dict]:
    p = path or (THEME_DIR / "catalog.jsonl")
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _iqr(days: list[str]) -> float:
    ords = sorted((_day(d) - _day(days[0])).days for d in days)
    if len(ords) < 4:
        return 0.0
    q75, q25 = np.percentile(ords, 75), np.percentile(ords, 25)
    return float(q75 - q25)


def metrics(asg: OnlineAssigner, assigns: list[dict]) -> dict:
    k = len(asg.members)
    sizes = [len(m) for m in asg.members]
    n_sg = sum(1 for s in sizes if s == 1)
    by_day_active: dict[str, int] = {}
    days = sorted({a["day"] for a in assigns})
    for d in days:
        by_day_active[d] = sum(
            1
            for last, mem in zip(asg.last, asg.members)
            if mem and 0 <= _delta_days(d, last) <= asg.delta
        )
    iqrs = [_iqr(ds) for ds, sz in zip(asg.member_days, sizes) if sz >= 4]
    active_vals = list(by_day_active.values()) or [0]
    n_child = sum(1 for p in asg.parent if p >= 0)
    return {
        "n_docs": len(assigns),
        "n_stories": k,
        "n_children": n_child,
        "n_splits": asg.n_splits,
        "singleton": n_sg,
        "singleton_rate": round(n_sg / k, 4) if k else 0.0,
        "size_mean": round(float(statistics.mean(sizes)), 3) if sizes else 0.0,
        "size_median": float(statistics.median(sizes)) if sizes else 0.0,
        "size_max": max(sizes) if sizes else 0,
        "nonsingleton_mean": round(
            float(statistics.mean([s for s in sizes if s > 1])), 3
        )
        if any(s > 1 for s in sizes)
        else 0.0,
        "active_median": float(statistics.median(active_vals)),
        "active_mean": round(float(statistics.mean(active_vals)), 2),
        "active_max": max(active_vals),
        "iqr_n": len(iqrs),
        "iqr_median": round(float(statistics.median(iqrs)), 2) if iqrs else None,
        "iqr_mean": round(float(statistics.mean(iqrs)), 2) if iqrs else None,
        "target_singleton": [0.15, 0.45],
        "target_iqr_median_max": 10,
        "in_singleton_band": 0.15 <= (n_sg / k if k else 0) <= 0.45,
        "iqr_ok": (not iqrs) or statistics.median(iqrs) <= 10,
        "exclusive_sum": sum(sizes),
    }


def run_assign(
    *,
    split: str = "tune",
    theta_in: float = THETA_IN,
    delta: int = DELTA_DORMANT,
    day_from: str = TUNE_FROM,
    day_to: str = TUNE_TO,
    do_split: bool = False,
    theta_sp: float = THETA_SP,
    n_chk: int = N_CHK,
    n_min: int = N_MIN,
    both_today: bool = False,
    entity_block: bool = False,
) -> dict:
    catalog = load_catalog()
    ids, mat = load_embed()
    vec_of = {i: mat[j] for j, i in enumerate(ids)}
    rows = [
        r
        for r in catalog
        if (split == "all" or r.get("split") == split)
        and day_from <= r["day"] <= day_to
    ]
    rows.sort(key=lambda r: (r["day"], r["doc_id"]))
    asg = OnlineAssigner(
        theta_in=theta_in,
        delta=delta,
        theta_sp=theta_sp,
        n_chk=n_chk,
        n_min=n_min,
        do_split=do_split,
        both_today=both_today,
        entity_block=entity_block,
    )
    assigns: list[dict] = []
    i = 0
    while i < len(rows):
        day = rows[i]["day"]
        j = i
        touched: set[int] = set()
        while j < len(rows) and rows[j]["day"] == day:
            r = rows[j]
            vec = vec_of[r["doc_id"]]
            link_title = r.get("title_zh") or r.get("title") or ""
            sid, sim, is_new = asg.assign(r["doc_id"], r["day"], vec, link_title)
            touched.add(sid)
            assigns.append(
                {
                    "doc_id": r["doc_id"],
                    "day": r["day"],
                    "title": r.get("title") or "",
                    "title_zh": r.get("title_zh") or "",
                    "source": r.get("source") or "",
                    "source_name": r.get("source_name") or "",
                    "lang": r.get("lang") or "",
                    "joined_story_id": f"s{sid:04d}",
                    "sim": round(sim, 4),
                    "new": is_new,
                }
            )
            j += 1
        asg.try_split_day(day, touched, vec_of)
        i = j
    home: dict[str, str] = {}
    for si, mem in enumerate(asg.members):
        for did in mem:
            home[did] = f"s{si:04d}"
    freeze_ok = True
    seen: set[str] = set()
    for a in assigns:
        if a["doc_id"] in seen:
            freeze_ok = False
        seen.add(a["doc_id"])
        a["story_id"] = home.get(a["doc_id"], a["joined_story_id"])
    replay = hashlib.sha256(
        ("\n".join(f"{a['doc_id']}\t{a['story_id']}" for a in assigns)).encode()
    ).hexdigest()
    m = metrics(asg, assigns)
    m.update(
        {
            "split": split,
            "theta_in": theta_in,
            "delta": delta,
            "theta_sp": theta_sp,
            "n_chk": n_chk,
            "n_min": n_min,
            "do_split": do_split,
            "both_today": both_today,
            "entity_block": entity_block,
            "day_from": day_from,
            "day_to": day_to,
            "freeze_ok": freeze_ok,
            "assign_sha256": replay,
            "partition_ok": m["exclusive_sum"] == m["n_docs"],
        }
    )
    stories = []
    for i, members in enumerate(asg.members):
        stories.append(
            {
                "story_id": f"s{i:04d}",
                "parent_id": asg.parent[i],
                "parent_story": f"s{asg.parent[i]:04d}" if asg.parent[i] >= 0 else "",
                "n": len(members),
                "n_inclusive": len(asg.members_inclusive[i]),
                "first_day": asg.first[i],
                "last_day": asg.last[i],
                "title0": asg.titles[i],
                "theaters": sorted(asg.theaters[i]) if i < len(asg.theaters) else [],
                "member_doc_ids": members,
            }
        )
    return {
        "metrics": m,
        "assigns": assigns,
        "stories": stories,
        "split_log": asg.split_log,
    }


def write_assign(out: dict, tag: str) -> None:
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (THEME_DIR / f"assign_{tag}.jsonl").write_text(
        "".join(json.dumps(a, ensure_ascii=False) + "\n" for a in out["assigns"]),
        encoding="utf-8",
    )
    (THEME_DIR / f"stories_{tag}.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in out["stories"]),
        encoding="utf-8",
    )
    (THEME_DIR / f"assign_{tag}_metrics.json").write_text(
        json.dumps(out["metrics"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if out.get("split_log"):
        (THEME_DIR / f"splits_{tag}.jsonl").write_text(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in out["split_log"]),
            encoding="utf-8",
        )
    m = out["metrics"]
    top = sorted(out["stories"], key=lambda s: -s["n"])[:12]
    mode = "条件分裂" if m.get("do_split") else "无分裂"
    lines = [
        f"# 单遍接线（{tag}）",
        "",
        f"- 窗：`{m['day_from']}`–`{m['day_to']}`  split=`{m['split']}`",
        f"- θ_in=`{m['theta_in']}`  δ=`{m['delta']}`  模式：**{mode}**",
        f"- 分裂参数 θ_sp=`{m.get('theta_sp')}` n_chk=`{m.get('n_chk')}` n_min=`{m.get('n_min')}` both_today=`{m.get('both_today')}` 实体挡板=`{m.get('entity_block')}`",
        f"- 文档 **{m['n_docs']}** → 故事 **{m['n_stories']}**（子线 {m.get('n_children', 0)}，分裂次数 {m.get('n_splits', 0)}）",
        f"- 单篇率 **{m['singleton_rate']}**（{m['singleton']}/{m['n_stories']}）",
        f"- 非单篇线均规模 **{m['nonsingleton_mean']}**；中位 {m['size_median']}；最大独占 **{m['size_max']}**",
        f"- 日活跃线数 中位 **{m['active_median']}** / 最大 {m['active_max']}",
        f"- |C|≥4 的日期 IQR 中位 **{m['iqr_median']}** 天：{'通过' if m['iqr_ok'] else '偏松'}",
        f"- freeze_ok=`{m['freeze_ok']}` partition_ok=`{m.get('partition_ok')}` sha=`{m['assign_sha256'][:12]}…`",
        "",
        "## 最大的 12 条线（独占成员 / 首题）",
        "",
        "| n | 父 | 起止 | 首题 |",
        "|---|----|------|------|",
    ]
    for s in top:
        title = (s["title0"] or "").replace("|", "\\|")
        par = s.get("parent_story") or "—"
        lines.append(f"| {s['n']} | {par} | {s['first_day']}～{s['last_day']} | {title} |")
    log = out.get("split_log") or []
    if log:
        lines += ["", "## 分裂记录（最多 15 条）", "", "| 日 | 父 → 子 | n | cos | 子首题 |", "|----|---------|---|-----|--------|"]
        for s in log[:15]:
            ct = (s.get("child_title") or "").replace("|", "\\|")
            lines.append(
                f"| {s['day']} | {s['parent']}→{s['child']} | {s['n_parent']}/{s['n_child']} | {s['cosine']} | {ct} |"
            )
        if len(log) > 15:
            lines.append(f"| … | 其余 {len(log) - 15} 次 | | | |")
    (REPORT_DIR / f"theme_assign_{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def grid_theta(thetas: list[float], *, delta: int = DELTA_DORMANT) -> list[dict]:
    rows = []
    for th in thetas:
        out = run_assign(theta_in=th, delta=delta, do_split=False)
        rows.append(out["metrics"])
    return rows


def grid_split(
    sps: list[float],
    *,
    n_mins: tuple[int, ...] = (2,),
    both_today: bool = False,
    theta_in: float = THETA_IN,
    delta: int = DELTA_DORMANT,
) -> list[dict]:
    rows = []
    for sp in sps:
        for n_min in n_mins:
            out = run_assign(
                theta_in=theta_in,
                delta=delta,
                do_split=True,
                theta_sp=sp,
                n_min=n_min,
                both_today=both_today,
            )
            rows.append(out["metrics"])
    return rows
