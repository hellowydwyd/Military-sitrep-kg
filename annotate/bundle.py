"""把标注工具打包成单个 HTML 文件，双击即可用，不需要服务器。

做法与思路：
- 把 task.css、core.js、app-local.js 内联进一份 HTML 模板
- 数据不出浏览器：用户自己选 jsonl，标注记录存 localStorage，可导出
- 输出 annotate/dist/annotate.html

    python3 -m annotate.bundle
    python3 -m annotate.bundle --out /tmp/a.html
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
DIST = Path(__file__).resolve().parent / "dist"


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>军事新闻三任务标注（离线版）</title>
<style>
{css}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <span class="logo">标</span>
    <div class="brand-text">
      <h1>军事新闻三任务标注</h1>
      <p id="subtitle">离线版</p>
    </div>
  </div>

  <nav class="seg" id="task-seg" role="tablist">
    <button class="seg-btn on" data-task="ner">实体 NER</button>
    <button class="seg-btn" data-task="ee">事件 EE</button>
    <button class="seg-btn" data-task="re">关系 RE</button>
  </nav>

  <div class="right">
    <label class="file-pick">
      <input type="file" id="file-input" accept=".jsonl,.json,text/plain" multiple hidden />
      <button id="pick-file" class="ghost" type="button">选择文件</button>
    </label>
    <label class="who">
      标注员
      <input id="annotator" value="A" maxlength="2" />
    </label>
    <button id="export" class="ghost" type="button">导出结果</button>
  </div>
</header>

<main class="layout">
  <aside class="rail">
    <div class="stat-card">
      <div class="stat-num" id="remain">0</div>
      <div class="stat-lbl">剩余篇数</div>
    </div>
    <div class="stat-row">
      <div class="stat-mini"><span id="total-docs">0</span><em>总篇数</em></div>
      <div class="stat-mini"><span id="done-docs">0</span><em>已标</em></div>
    </div>

    <div class="rail-sec">
      <div class="rail-title">进度</div>
      <div class="bar-track"><div class="bar-fill" id="bar"></div></div>
      <div class="rail-note" id="bar-note">0%</div>
    </div>

    <div class="rail-sec">
      <div class="rail-title">本篇候选</div>
      <div class="cnt-row"><span class="dot keep"></span><em id="cnt-keep">0</em><em class="muted">保留</em></div>
      <div class="cnt-row"><span class="dot modify"></span><em id="cnt-modify">0</em><em class="muted">修改</em></div>
      <div class="cnt-row"><span class="dot drop"></span><em id="cnt-drop">0</em><em class="muted">删除</em></div>
      <div class="cnt-row"><span class="dot manual"></span><em id="cnt-add">0</em><em class="muted">补标</em></div>
    </div>

    <div class="rail-sec">
      <div class="rail-title">文件</div>
      <div class="rail-note" id="file-info">未载入文件</div>
      <button id="clear-all" class="ghost" type="button" style="margin-top:6px">清空本机记录</button>
    </div>

    <div class="rail-sec grow">
      <div class="rail-title">操作提示</div>
      <ul class="hints">
        <li><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> 保留 / 修改 / 删除</li>
        <li><kbd>←</kbd><kbd>→</kbd> 切换候选，点高亮同效</li>
        <li><kbd>/</kbd> 进入框选，拖选即补标</li>
        <li><kbd>Enter</kbd> 提交并下一篇</li>
        <li><kbd>Esc</kbd> 退出框选</li>
      </ul>
    </div>
  </aside>

  <section class="docpane">
    <div class="doc-head">
      <div class="doc-titlewrap">
        <h2 id="doctitle">未载入文件</h2>
        <div class="doc-meta" id="docmeta"></div>
      </div>
    <div class="doc-tools">
      <span id="hint" class="hint"></span>
      <span id="cur-done" class="done-badge" hidden>已标</span>
      <div class="nav">
        <button id="prev" class="ghost nav-btn" title="上一题（PageUp）">← 上一题</button>
        <span id="nav-pos" class="nav-pos">0 / 0</span>
        <button id="next" class="ghost nav-btn" title="下一题（PageDown）">下一题 →</button>
      </div>
      <button id="orig-toggle" class="ghost" hidden>对照原文</button>
      <button id="select-mode" class="ghost">框选补标</button>
    </div>
    </div>
    <div class="doc-body">
      <div class="text" id="text"></div>
      <aside class="orig" id="orig" hidden></aside>
    </div>
  </section>

  <aside class="judge">
    <div class="judge-head">
      <span class="judge-title">候选判定</span>
      <span class="judge-sub" id="schema-brief"></span>
    </div>
    <div class="cands" id="cands"></div>
    <div class="actions">
      <textarea id="notes" rows="2" placeholder="备注：争议点、为什么判其他、漏标说明"></textarea>
      <div class="act-row">
        <button id="clear" class="ghost">清空</button>
        <button id="skip" class="ghost">跳过</button>
        <button id="submit" class="primary">提交并下一篇</button>
      </div>
    </div>
  </aside>
</main>

<div class="tip" id="tip"></div>

<script>
{core}
</script>
<script>
{local}
/* 离线版特有的启动逻辑：点按钮打开文件选择框 */
(function () {{
  const btn = document.getElementById("pick-file");
  const inp = document.getElementById("file-input");
  if (btn && inp) btn.addEventListener("click", () => inp.click());
}})();
</script>
</body>
</html>
"""


def build(out: Path) -> Path:
    css = (STATIC / "task.css").read_text(encoding="utf-8")
    core = (STATIC / "core.js").read_text(encoding="utf-8")
    local = (STATIC / "app-local.js").read_text(encoding="utf-8")
    html = TEMPLATE.format(css=css, core=core, local=local)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="打包单文件标注工具")
    p.add_argument("--out", default=str(DIST / "annotate.html"))
    a = p.parse_args(argv)
    path = build(Path(a.out))
    size = path.stat().st_size
    print(f"已生成 {path}（{size / 1024:.0f} KB）")
    print("双击打开即可标注，不需要服务器。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
