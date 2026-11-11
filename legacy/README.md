# legacy：已停用的旧方案

这里的内容不再参与主流程，只作回溯用。当前在用的是三任务方案（NER/EE/RE）。

## annotate：开放事件试点标注（P1）

第一版标注工具，标注对象是「开放事件 + 槽位」，与现在的三任务分卷标注不是一回事。

| 文件 | 说明 |
| --- | --- |
| `annotate/server.py` | 静态页 + 标注 API |
| `annotate/store.py` | 读写 `data/eval/pilot20.json`、`data/extract/openie_event_pilot.jsonl` |
| `annotate/iaa.py` | 双标一致率 |
| `annotate/static/` | `index.html`、`iaa.html`、`app.js`、`style.css` |

启动方式（已失效，仅存档）：

    python3 -m annotate --host 127.0.0.1 --port 8765

现存的三任务标注改用 `python3 -m annotate --port 8767`，代码在 `annotate/task_server.py` 与 `annotate/tasks.py`。

## extract：旧抽取管线

| 文件 | 说明 |
| --- | --- |
| `extract/__main__.py` | 旧的 `python -m extract` 入口，依赖已删除的 `extract.pipeline` |
| `extract/chunk.py` | 按段切块，无人引用 |
| `extract/report.py` | 旧管线的验收报告 |

当前的抽取入口是 `python3 -m extract.kg_batch`，核心在 `extract/kg_extract.py`。

## 相关数据

- `data/eval/pilot20.json`、`data/extract/openie_event_pilot.jsonl`：旧试点预标。
- `data/eval/annotations/`：旧试点定稿。
- `data/extract/openie*.jsonl`：旧开放事件抽取结果，见 `data/archive/extract/`。
