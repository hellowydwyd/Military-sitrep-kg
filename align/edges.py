"""边的语义细分。

一、部署边：实际部署 / 计划部署 / 误标（实为动用）
二、去向边：部署去向 / 事件地点（击落、坠落、落在、起飞）
三、使用边：按证据动词拆成 发射 / 拦截 / 被击中 / 搭载 / 展示 / 生产 / 涉及

    python3 -m align.edges --src data/extract/pilot10v3_resolved.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 实际部署：已完成的位置调整
DEPLOY_DONE = re.compile(
    r"部署到|部署于|部署在|已部署|增派|派驻|调往|转移至|转移到了|飞抵|抵达|靠泊|停靠|进驻|驻扎|派遣|派往|运抵|交付"
)
# 计划部署：尚未发生
DEPLOY_PLAN = re.compile(r"计划部署|将部署|拟部署|准备部署|打算部署|讨论|磋商|考虑|引进|采购|寻求购买|有意向")
# 使用：动用已有装备实施行动
DEPLOY_AS_USE = re.compile(r"动用|出动|使用了|使用|发射了|实施了?空袭|发动袭击|投入")

# 部署去向：装备被移动到某地
DEST_DEPLOY = re.compile(
    r"部署到|部署于|部署在|增派到|增派至|派往|调往|转移至|移驻|飞抵|运抵|进驻|靠泊|停靠|抵达"
)
# 事件地点：装备在某地发生某事
DEST_EVENT = re.compile(r"击落|拦截|坠落|落在|落入|击中|命中|爆炸|起火|起飞|发射|发现|缴获|摧毁")

# ---- 使用边细分 ----
# 顺序即优先级：先判断被击中（装备受损），再判断拦截（装备被拦），
# 再发射（装备被打出），最后搭载与展示生产。
USE_HIT = re.compile(r"被击中|被摧毁|被击落|坠毁|坠入|受损|爆炸|起火|触雷|撞")
USE_INTERCEPT = re.compile(r"拦截|击落|摧毁|防空部队|防空系统成功")
USE_LAUNCH = re.compile(r"发射了|试射|射向|打出|投下|投掷")
USE_SHOW = re.compile(r"展示|参展|亮相|展出|航展|列队|阅兵")
USE_BUILD = re.compile(r"生产|制造|研制|交付|下线|开工|建造")
USE_CARRY = re.compile(r"搭载|携带|配备|装配|编制|装备有|列装|部署了|动用了|出动|参与|参加|执行|实施")

USE_TYPE = {
    "被击中": "被击中",
    "拦截": "拦截",
    "发射": "发射",
    "展示": "展示",
    "生产": "生产",
    "搭载": "搭载",
}


def classify_use(evidence: str, head_type: str = "", tail_type: str = "") -> str:
    """返回细分类型。head 为装备时优先判为「被击中」。"""
    ev = evidence or ""
    if head_type == "装备" and USE_HIT.search(ev):
        return "被击中"
    if USE_INTERCEPT.search(ev):
        return "拦截"
    if USE_LAUNCH.search(ev):
        return "发射"
    if USE_SHOW.search(ev):
        return "展示"
    if USE_BUILD.search(ev):
        return "生产"
    if USE_CARRY.search(ev):
        return "搭载"
    return "涉及"


def classify_deploy(evidence: str) -> str:
    ev = evidence or ""
    if DEPLOY_DONE.search(ev):
        return "部署"
    if DEPLOY_PLAN.search(ev):
        return "计划部署"
    if DEPLOY_AS_USE.search(ev):
        return "使用"
    return "部署"


def classify_dest(evidence: str) -> str:
    ev = evidence or ""
    if DEST_DEPLOY.search(ev):
        return "部署去向"
    if DEST_EVENT.search(ev):
        return "事件地点"
    return "去向"


# 关系名与端点类型登记。用于校验与路径匹配。
REL_DOMAIN = {
    "实施": ({"机构"}, {"事件"}),
    "参与": ({"人物"}, {"事件"}),
    "搭载": ({"事件"}, {"装备"}),
    "涉及": ({"事件"}, {"装备", "作品"}),
    "拦截": ({"事件"}, {"装备"}),
    "发射": ({"事件"}, {"装备"}),
    "展示": ({"事件"}, {"装备"}),
    "生产": ({"事件"}, {"装备"}),
    "部署": ({"机构"}, {"装备"}),
    "去向": ({"装备"}, {"地点", "机构"}),
    "从属": ({"机构"}, {"机构"}),
    "发表": ({"人物", "机构", "平台"}, {"观点"}),
    "针对": ({"观点"}, {"事件"}),
    "表明": ({"人物", "机构"}, {"立场"}),
    "关于": ({"立场"}, {"事件"}),
    "处于": ({"事件"}, {"场景"}),
    "发布": ({"平台", "机构"}, {"事件"}),
}


def _pair_ok(rel: dict, type_of: dict[str, str]) -> bool:
    want = REL_DOMAIN.get(rel.get("name") or "")
    if not want:
        return True
    h = type_of.get(rel.get("head_id") or "") or type_of.get(rel.get("head") or "")
    t = type_of.get(rel.get("tail_id") or "") or type_of.get(rel.get("tail") or "")
    return h in want[0] and t in want[1]


def run(src: Path, out: Optional[Path] = None) -> dict:
    payload = json.loads(src.read_text(encoding="utf-8"))
    if out is None:
        out = src.with_name(src.stem + "_edges.json")
    stats = Counter()
    rels = Counter()
    use_sub = Counter()
    from extract.pilot10 import SPEECH_REL, is_non_actor
    for row in payload["docs"]:
        type_of = {}
        for ent in row.get("entities") or []:
            for key in ("id", "name", "canonical"):
                val = ent.get(key)
                if val:
                    type_of.setdefault(val, ent.get("type") or "")
        new_rels = []
        for rel in row["relations"]:
            name = rel["name"]
            ev = rel.get("evidence") or ""
            head_type = type_of.get(rel.get("head_id") or "", "") or type_of.get(rel.get("head") or "", "")
            tail_type = type_of.get(rel.get("tail_id") or "", "") or type_of.get(rel.get("tail") or "", "")
            if name == "部署":
                kind = classify_deploy(ev)
                if kind == "使用":
                    stats["deploy_as_use"] += 1
                    sub = classify_use(ev, tail_type, head_type)
                    use_sub[sub] += 1
                    rel = {**rel, "name": sub if sub != "涉及" else "搭载",
                           "retyped_from": "部署", "use_kind": sub}
                elif kind == "计划部署":
                    stats["deploy_planned"] += 1
                    rel = {**rel, "discourse": "计划"}
                else:
                    stats["deploy_done"] += 1
                    rel = {**rel, "discourse": "发生"}
            elif name == "去向":
                kind = classify_dest(ev)
                rel = {**rel, "dest_kind": kind}
                stats[f"dest_{kind}"] += 1
            elif name == "使用":
                sub = classify_use(ev, head_type, tail_type)
                use_sub[sub] += 1
                stats[f"use_{sub}"] += 1
                if head_type == "机构" and tail_type == "装备":
                    # 机构对装备的动作：拦截、发射、生产归机构侧；其余归搭载
                    if sub in ("拦截", "发射", "生产"):
                        new_name = sub
                    else:
                        new_name = "部署" if sub == "搭载" else "搭载"
                    rel = {**rel, "name": new_name, "use_kind": sub}
                else:
                    if sub in ("拦截", "发射", "生产", "搭载"):
                        new_name = sub if sub != "涉及" else "涉及"
                    elif sub == "展示":
                        new_name = "展示"
                    else:
                        new_name = "涉及"
                    rel = {**rel, "name": new_name, "use_kind": sub}
            elif name == "从属":
                # 自环与跨类误挂直接丢弃
                if rel.get("head_id") and rel.get("head_id") == rel.get("tail_id"):
                    stats["sub_self_loop"] += 1
                    continue
            if not _pair_ok(rel, type_of):
                stats["edge_dropped_type"] += 1
                continue
            # 记者、发言人、消息人士不是事件施动者
            if rel["name"] not in SPEECH_REL and is_non_actor(str(rel.get("head") or "")):
                stats["edge_dropped_non_actor"] += 1
                continue
            new_rels.append(rel)
            rels[rel["name"]] += 1

        # 补齐作品与事件的连接：同篇出现即记「涉及」，原句取事件证据
        evs = [e for e in row.get("entities") or [] if e.get("type") == "事件"]
        works = [e for e in row.get("entities") or [] if e.get("type") == "作品"]
        if evs and works:
            ev_text = " ".join([(row.get("title") or ""), *(row.get("body") or "").split()[:40]])
            for ev in evs:
                ev_id = ev.get("id") or ev.get("name")
                for wk in works:
                    wk_id = wk.get("id") or wk.get("name")
                    if not ev_id or not wk_id:
                        continue
                    ev_name = ev.get("canonical") or ev.get("name") or ""
                    wk_name = wk.get("canonical") or wk.get("name") or ""
                    # 作品名在含事件的原句里出现才算涉及
                    evidence = ""
                    for rel in new_rels:
                        e_ev = rel.get("evidence") or ""
                        if wk_name and wk_name in e_ev and (ev_name and ev_name in e_ev):
                            evidence = e_ev
                            break
                    if not evidence:
                        continue
                    new_rels.append({
                        "name": "涉及",
                        "head": ev_name,
                        "tail": wk_name,
                        "head_id": ev_id,
                        "tail_id": wk_id,
                        "evidence": evidence[:200],
                        "derived": "work_in_sentence",
                        "doc_id": row.get("doc_id") or "",
                    })
                    stats["work_linked"] += 1
                    rels["涉及"] += 1
        row["relations"] = new_rels
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"docs": len(payload["docs"]), "edge_kinds": dict(stats),
            "use_subtypes": dict(use_sub), "relations": dict(rels), "out": str(out)}


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="按证据动词细分部署与去向边")
    p.add_argument("--src", default=str(DATA / "extract" / "pilot10v3_resolved.json"))
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    print(json.dumps(run(Path(a.src), Path(a.out) if a.out else None),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
