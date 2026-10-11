"""python -m theme catalog | embed | assign | slice"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from theme.config import (
    DELTA_DORMANT,
    MODEL_PATH,
    N_CHK,
    N_MIN,
    REPORT_DIR,
    RHO_CORE,
    TEST_TO,
    THEME_DIR,
    THETA_IN,
    THETA_SP,
    TUNE_FROM,
    TUNE_TO,
)
from theme.corpus import catalog_docs, write_catalog


def cmd_catalog() -> int:
    kept, dropped = catalog_docs()
    cfg = write_catalog(kept, dropped)
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print(f"清单 {THEME_DIR / 'catalog.jsonl'}")
    from theme.title_zh import fill_catalog

    zh = fill_catalog()
    print(json.dumps({"title_zh": zh}, ensure_ascii=False))
    print(f"报告 {REPORT_DIR / 'theme_catalog.md'}")
    return 0


def cmd_embed(*, model: str, batch_size: int) -> int:
    cat = THEME_DIR / "catalog.jsonl"
    if not cat.is_file():
        print("先跑: python3 -m theme catalog")
        return 2
    kept = []
    for line in cat.read_text(encoding="utf-8").splitlines():
        if line.strip():
            kept.append(json.loads(line))
    from theme.embed import embed_catalog

    meta = embed_catalog(kept, model_path=Path(model), batch_size=batch_size)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if not meta.get("replay8_ok"):
        print("警告：前 8 篇二次编码不一致")
        return 1
    return 0


def cmd_assign(
    *,
    theta_in: float,
    delta: int,
    grid: bool,
    do_split: bool,
    theta_sp: float,
    n_chk: int,
    n_min: int,
    both_today: bool,
    grid_sp: bool,
    entity_block: bool,
    all_days: bool,
) -> int:
    from theme.assign import grid_split, grid_theta, run_assign, write_assign

    tag = (
        f"tune_{'split' if do_split else 'nosplit'}"
        f"_t{theta_in:.2f}_d{delta}"
        + ("_elink" if entity_block else "")
        + (f"_sp{theta_sp:.2f}" if do_split else "")
    )
    out = run_assign(
        split="all" if all_days else "tune",
        day_from=TUNE_FROM,
        day_to=TEST_TO if all_days else TUNE_TO,
        theta_in=theta_in,
        delta=delta,
        do_split=do_split,
        theta_sp=theta_sp,
        n_chk=n_chk,
        n_min=n_min,
        both_today=both_today,
        entity_block=entity_block,
    )
    write_assign(out, tag)
    print(json.dumps(out["metrics"], ensure_ascii=False, indent=2))
    print(f"报告 {REPORT_DIR / f'theme_assign_{tag}.md'}")
    if grid:
        thetas = [0.62, 0.68, 0.72, 0.76, 0.82]
        rows = grid_theta(thetas, delta=delta)
        (THEME_DIR / "assign_tune_grid.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("θ 网格（调参段、无分裂）")
        for r in rows:
            print(
                f"  θ={r['theta_in']:.2f}  stories={r['n_stories']}  "
                f"sg={r['singleton_rate']}  iqr={r['iqr_median']}  "
                f"active_med={r['active_median']}  max={r['size_max']}"
            )
    if grid_sp:
        sps = [0.82, 0.86, 0.88, 0.90, 0.92, 0.94]
        rows = grid_split(sps, n_mins=(2, 8), both_today=both_today, theta_in=theta_in, delta=delta)
        (THEME_DIR / "assign_tune_grid_sp.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("θ_sp 网格（调参段、条件分裂）")
        for r in rows:
            print(
                f"  sp={r['theta_sp']:.2f} nmin={r['n_min']}  "
                f"stories={r['n_stories']} child={r['n_children']}  "
                f"sg={r['singleton_rate']} max={r['size_max']} iqr={r['iqr_median']}"
            )
    return 0


def cmd_slice(*, day_from: str, day_to: str, tag: str | None, rho: float, cluster: str, name_mode: str) -> int:
    from theme.slice import build_slice, default_assign_tag, write_slice

    tag = tag or default_assign_tag()
    out = build_slice(ta=day_from, tb=day_to, tag=tag, rho=rho, cluster_mode=cluster, name_mode=name_mode)
    json_path, md = write_slice(out)
    print(json.dumps(out["metrics"], ensure_ascii=False, indent=2))
    print(f"JSON {json_path}")
    print(f"报告 {md}")
    if not out["metrics"].get("conservation_ok"):
        print("警告：河宽守恒失败")
        return 1
    return 0


def cmd_narrative(*, force: bool) -> int:
    from theme.narrative import fill_slice
    from theme.slice import _export_viz

    n = 0
    last = None
    for p in sorted(THEME_DIR.glob("slice_*.json")):
        if "theater" in p.name:
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "window" not in data:
            continue
        added = fill_slice(data, force=force)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        n += added
        last = data
        print(f"{p.name} +{added}")
    if last is None:
        print("没有 slice_*.json")
        return 2
    _export_viz(last)
    print(f"共写入 {n} 条事态归纳")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="主题演化：清单、向量、单遍接线、窗查询")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog", help="建语料清单与冻结切分")
    ep = sub.add_parser("embed", help="写 embed.npz（需 sentence_transformers）")
    ep.add_argument("--model", default=str(MODEL_PATH))
    ep.add_argument("--batch-size", type=int, default=32)
    ap = sub.add_parser("assign", help="单遍接线（默认调参段、可开分裂）")
    ap.add_argument("--theta-in", type=float, default=THETA_IN)
    ap.add_argument("--delta", type=int, default=DELTA_DORMANT)
    ap.add_argument("--grid", action="store_true", help="顺带扫一组 θ_in（无分裂）")
    ap.add_argument("--split", action="store_true", help="当天接线后做条件 2-means")
    ap.add_argument("--theta-sp", type=float, default=THETA_SP)
    ap.add_argument("--n-chk", type=int, default=N_CHK)
    ap.add_argument("--n-min", type=int, default=N_MIN)
    ap.add_argument(
        "--both-today",
        action="store_true",
        help="两半当天都要有稿才分裂（更严；默认只要求本线当天有新稿）",
    )
    ap.add_argument("--grid-sp", action="store_true", help="顺带扫一组 θ_sp（有分裂）")
    ap.add_argument("--entity-block", action="store_true", help="标题战场不相交则禁止并入")
    ap.add_argument("--all-days", action="store_true", help="接线到测试段末日，纳入 RSS")
    sp = sub.add_parser("slice", help="查询窗 ThemeSlice（只读调参段接线）")
    sp.add_argument("--from", dest="day_from", default="2026-08-01")
    sp.add_argument("--to", dest="day_to", default=TUNE_TO)
    sp.add_argument("--tag", default=None, help="接线 tag")
    sp.add_argument("--rho", type=float, default=RHO_CORE)
    sp.add_argument(
        "--cluster",
        default="judge",
        choices=["judge", "theater", "query", "tree"],
        help="judge=向量候选后 LLM 判定并/留；theater=先战场再向量且丢掉无标签；query=全局向量+挡板；tree=旧根线族谱",
    )
    sp.add_argument(
        "--name",
        dest="name_mode",
        default="auto",
        choices=["auto", "llm", "rule", "off"],
        help="议题短名：auto=有密钥走 LLM 否则规则压缩；off=仍用近稿标题",
    )
    np_ = sub.add_parser("narrative", help="给已有切片的核心议题归纳事态脉络（不改聚类）")
    np_.add_argument("--force", action="store_true", help="忽略缓存重跑")
    args = p.parse_args(argv)
    if args.cmd == "catalog":
        return cmd_catalog()
    if args.cmd == "embed":
        return cmd_embed(model=args.model, batch_size=args.batch_size)
    if args.cmd == "assign":
        return cmd_assign(
            theta_in=args.theta_in,
            delta=args.delta,
            grid=args.grid,
            do_split=args.split,
            theta_sp=args.theta_sp,
            n_chk=args.n_chk,
            n_min=args.n_min,
            both_today=args.both_today,
            grid_sp=args.grid_sp,
            entity_block=args.entity_block,
            all_days=args.all_days,
        )
    if args.cmd == "slice":
        return cmd_slice(
            day_from=args.day_from,
            day_to=args.day_to,
            tag=args.tag,
            rho=args.rho,
            cluster=args.cluster,
            name_mode=args.name_mode,
        )
    if args.cmd == "narrative":
        return cmd_narrative(force=args.force)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
