# 三任务标注工具

两套用法，共用同一套界面代码。

## 一、离线单文件（发给标注人用这个）

```bash
python3 -m annotate.bundle
```

生成 `annotate/dist/annotate.html`（约 45KB）。**把这个文件发给标注人，双击用浏览器打开就能标。**

- 数据不出浏览器：标注人自己点「选择文件」选 jsonl，文件不会上传
- 标注记录存在浏览器本地（localStorage），关掉页面不丢
- 点「导出结果」下载 `verdicts_<标注员>_<日期>.jsonl`，发回给你
- 换机器或换浏览器时，记录不会跟过去，所以标完要及时导出

支持一次选多个文件，按 `entities`/`events`/`triples` 字段自动识别属于哪个任务。

## 二、服务器版（自己用，或团队同一局域网）

```bash
python3 -m annotate --port 8767
```

浏览器打开 `http://<机器IP>:8767`。

- 顶栏「卷」下拉列出 `data/dataset/*.jsonl` 全部文件，可指定只标某一份
- 不选卷则按「任务 + train/dev/test」取
- 标注记录写到 `data/annotations/{task}_{annotator}.jsonl`，追加写入，历史保留
- 换任务时如果当前卷不属于新任务，自动退回「全部卷」

## 输入格式

每行一个 JSON，至少要有 `doc_id`、`title`、`text`，以及下列三段之一：

| 字段 | 任务 | 内容 |
| --- | --- | --- |
| `entities` | NER | `[{"text": "...", "type": "机构"}]` |
| `events` | EE | `[{"trigger": "...", "event_type": "...", "arguments": [...]}]` |
| `triples` | RE | `[{"head": "...", "relation": "...", "tail": "..."}]` |

`doc_id` 缺失时会自动补 `d0`、`d1` 这样的编号。

## 输出格式

```json
{"task":"ner","annotator":"A","doc_id":"...","items":[...],"notes":"","at":"2026-11-12T..."}
```

`items` 每条是：

```json
{"idx":0,"action":"keep|modify|drop","source":{...},"type":"修改后的类型"}
```

手工补标的条目 `action` 为 `add`，带 `offset: [start, end]` 和 `text`。

两套用法输出的格式完全一致，可以合并。

## 界面

三栏：左边统计与快捷键，中间原文（实体按类型着色），右边逐条判定。

| 操作 | 快捷键 |
| --- | --- |
| 保留 / 修改 / 删除当前候选 | `1` `2` `3` |
| 切换候选 | `←` `→` 或点高亮 |
| 进入框选补标 | `/` |
| 提交并下一篇 | `Enter` |
| 退出框选 | `Esc` |

## 代码结构

| 文件 | 作用 |
| --- | --- |
| `static/core.js` | 界面渲染与交互，不碰数据来源 |
| `static/app-server.js` | 服务器版控制器：走 `/api/*` |
| `static/app-local.js` | 离线版控制器：localStorage + 导出 |
| `static/task.html` | 服务器版页面 |
| `static/task.css` | 两版共用样式 |
| `bundle.py` | 把 CSS 与 JS 内联成单文件 |
| `tasks.py` | 卷读取、判定记录、统计 |
| `task_server.py` | HTTP 服务 |

改界面只动 `core.js` 和 `task.css`，两版都生效。改完记得重跑 `python3 -m annotate.bundle`。

## 接口

| 路径 | 说明 |
| --- | --- |
| `GET /api/files` | 列出可标注的卷 |
| `GET /api/next?task=&split=&annotator=&file=&skip=` | 取下一篇未标稿件 |
| `GET /api/doc?task=&doc_id=&file=` | 按编号取稿件 |
| `POST /api/judge` | 提交判定 |
| `GET /api/stats` | 双标覆盖率 |
