"""英文稿译成中文，供中文抽取管线复用。

为什么译而不是给英文单独写规则：抽取的代码层校验（事件长度、谓词、
施动者、记者识别）全是中文规则，另写一套英文规则会与中文漂移。
译成中文后中英文走同一条管线，类型体系只有一套。

关键约束：
- 译文按段对应原文，每段保留源序号，便于回溯
- 段落切分不跨句群，保证「片段是原文子串」能在中文侧成立
- 专名用通行译法，与 align/zh.py 的维基规范名保持同一口径

    python3 -m extract.translate --src data/extract/kgen.jsonl \
        --out data/extract/kgen_zh.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

from extract.llm import chat_json
from extract.prompt import parse_llm_json

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 单次送译的字符上限。太长会超时，太短浪费调用
MAX_CHARS = 2400

SYSTEM = """你把英文军事新闻译成简体中文。译文要给中文事件抽取用，所以要按中文新闻的句式重组。

一、断句（最重要）
英文一个长句常含多个动作，必须拆成几个中文短句，一句只讲一件事。
主语相同可承前省略，但每个动作单独成句。

例：
原：He stressed that the fate of Saddam awaits Trump, and asserted that
    the Americans launched the war based on miscalculation, refusing to
    accept historical reality.
译：他强调，萨达姆的命运正等着特朗普。他称美国人和以色列人基于错误算计
    发动了战争。他拒绝接受历史现实。
（拆成三句，每句一个动作，每句都在 40 字以内）

二、句子长度
- 每句 12 至 45 个汉字，最多不超过 55 字
- 带一串从句、并列成分的长句，先断成短句，再译
- 数字、单位、日期不拆

三、专名
1. 用中文新闻的通行译法：Trump=特朗普、Netanyahu=内塔尼亚胡、
   IRGC=伊朗伊斯兰革命卫队、Houthi=胡塞武装、IDF=以色列国防军。
   机构首次出现给全称，之后可用简称。
2. 人名首次出现写全名，之后可只写姓。

四、其他
1. 数字、日期、地名按中文习惯写：October 8 → 10月8日。
2. 军事术语用中文标准说法：airstrike=空袭、drone=无人机、battalion=营。
3. 不添加原文没有的信息，不省略原文有的信息。
4. 引语保留引号，但引语里的长句同样要断句。

只输出 JSON：{"items":[{"id":"段号","zh":"译文"}]}。不要解释，不要漏 id。"""


def split_paragraphs(text: str, limit: int = MAX_CHARS) -> list[str]:
    """按段落切，再按句切，最后按长度打包。"""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if not paras:
        return []
    out: list[str] = []
    buf: list[str] = []
    n = 0
    for p in paras:
        if len(p) > limit:
            # 超长段落按句拆
            sents = [s.strip() for s in p.replace("! ", "!|").replace("? ", "?|").replace(". ", ".|").split("|") if s.strip()]
            for s in sents:
                if buf and n + len(s) + 1 > limit:
                    out.append("\n".join(buf))
                    buf, n = [], 0
                buf.append(s)
                n += len(s) + 1
            continue
        if buf and n + len(p) + 1 > limit:
            out.append("\n".join(buf))
            buf, n = [], 0
        buf.append(p)
        n += len(p) + 1
    if buf:
        out.append("\n".join(buf))
    return out


def translate_blocks(blocks: list[str], retries: int = 3) -> list[str]:
    """逐块翻译，返回与输入等长的中文列表。失败的块返回空串。"""
    if not blocks:
        return []
    payload = {"items": [{"id": str(i), "text": b} for i, b in enumerate(blocks)]}
    user = json.dumps(payload, ensure_ascii=False)
    out = [""] * len(blocks)
    for attempt in range(retries):
        try:
            raw = chat_json(SYSTEM, user)
            parsed = parse_llm_json(raw)
            got = parsed.get("items") if isinstance(parsed, dict) else parsed
            for it in got or []:
                if not isinstance(it, dict):
                    continue
                try:
                    i = int(str(it.get("id")))
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(out):
                    out[i] = str(it.get("zh") or "").strip()
            if all(out):
                return out
            # 补齐漏掉的块
            missing = [i for i, v in enumerate(out) if not v]
            if not missing:
                return out
            payload = {"items": [{"id": str(i), "text": blocks[i]} for i in missing]}
            user = json.dumps(payload, ensure_ascii=False)
        except Exception:
            if attempt == retries - 1:
                break
            time.sleep(2 * (attempt + 1))
    return out


def _load_raw_index() -> dict[str, dict]:
    """建立 doc_id -> 原始稿 的索引。抽取结果不含正文，翻译要回原始稿取。"""
    idx: dict[str, dict] = {}
    for p in (DATA / "raw").glob("*/*.jsonl"):
        try:
            for line in p.open(encoding="utf-8"):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                did = r.get("doc_id")
                if did and did not in idx:
                    idx[did] = r
        except OSError:
            continue
    return idx


def translate_doc(row: dict, raw: dict | None = None) -> tuple[dict, dict]:
    """译一篇。正文从原始稿取；raw 为空时退回行内 body。"""
    st = {"ok": 0, "failed": 0, "chars": 0, "blocks": 0}
    src = raw or row
    body = src.get("body") or ""
    title = src.get("title") or ""
    if not body and not title:
        st["failed"] += 1
        return row, st

    blocks = split_paragraphs(body)
    st["blocks"] = len(blocks)
    zh_blocks = translate_blocks(blocks)
    zh_title_list = translate_blocks([title]) if title else [""]

    missing = sum(1 for b, z in zip(blocks, zh_blocks) if b and not z)
    if missing:
        st["failed"] += 1
    else:
        st["ok"] += 1

    zh_body = "\n".join(z for z in zh_blocks if z)
    zh_title = (zh_title_list[0] if zh_title_list else "").strip()

    out = dict(row)
    out["title_orig"] = title
    out["body_orig"] = body
    out["title"] = zh_title or title
    out["body"] = zh_body or body
    out["body_chars"] = len(zh_body)
    out["lang"] = "zh"
    out["translated"] = True
    st["chars"] = len(zh_body)
    return out, st


def run(src: Path, out: Path, limit: int = 0, resume: bool = False) -> dict:
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    raw_idx = _load_raw_index()
    print(f"原始稿索引 {len(raw_idx)} 篇", flush=True)
    done = set()
    if resume and out.is_file():
        for line in out.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("doc_id") and r.get("translated"):
                done.add(r["doc_id"])
    todo = [r for r in rows if r.get("doc_id") not in done]
    if limit:
        todo = todo[:limit]

    out.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if resume and out.is_file() else "w"
    agg = {"docs": 0, "ok": 0, "failed": 0, "chars": 0, "no_raw": 0}
    t0 = time.time()
    with out.open(mode, encoding="utf-8") as f:
        for i, r in enumerate(todo, 1):
            raw = raw_idx.get(r.get("doc_id"))
            if not raw:
                agg["no_raw"] += 1
            new, st = translate_doc(r, raw)
            agg["docs"] += 1
            agg["ok"] += st["ok"]
            agg["failed"] += st["failed"]
            agg["chars"] += st["chars"]
            f.write(json.dumps(new, ensure_ascii=False) + "\n")
            f.flush()
            if i % 10 == 0:
                rate = i / max(1e-9, time.time() - t0)
                left = (len(todo) - i) / max(1e-9, rate)
                print(
                    f"  {i}/{len(todo)}  {rate*60:.1f} 篇/分  约剩 {left/60:.0f} 分",
                    flush=True,
                )
    agg["elapsed_s"] = round(time.time() - t0, 1)
    agg["out"] = str(out)
    return agg


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="英文稿译成中文")
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--resume", action="store_true")
    a = p.parse_args(argv)
    agg = run(Path(a.src), Path(a.out), a.limit, a.resume)
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
