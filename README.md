# Military-sitrep-kg

从公开军事新闻整理一段时间的报道态势。来源有两路：环球网军事，以及合作方提供的英语 RSS（白名单站点）。英文稿的标题译成中文后参与聚类，正文保留原文。

仓库包含一套离线处理流程，和已经导出的可视化页面。页面用来查看某个时间窗里的核心议题、每日稿量、议题脉络，以及国家、军队、人物、装备的共现。流程用来采集新稿、抽取事件、重算议题和主体，并按日期或主体查询事件清单。

## 整体流程

处理顺序是固定的。后面的步骤读取前面写出的文件。

```
采集 ingest
  环球网栏目 / RSS 接口
  → data/raw/<source>/YYYY-MM-DD.jsonl
  → data/seen.sqlite（按链接去重）

抽取 extract
  读取 data/raw/
  → data/extract/openie_event.jsonl
     每篇若干事件：参与者、地点、时间、原文片段

        ┌─ 主题 theme
        │    catalog → embed → assign → slice → narrative
        │    → data/theme/slice_*.json
        │    → theme/web/data.js
        │
        ├─ 主体 actor
        │    harvest → link → slice
        │    → data/actor/mentions.jsonl、entities.json、slice_*.json
        │    → theme/web/actor-data.js
        │
        └─ 事件图 graph / reason
             graph.build
             → data/graph/sitrep.b1.json
             reason.daily / reason.timeline
             → data/reports/daily_*.md、timeline_slice.md
```

每篇入库稿含标题、正文、发布时间、来源、链接。同一链接只保留一次。正文短于阈值的稿件记为跳过，不进入抽取。

**主题**按标题和正文的向量相似度，把前后若干天仍在报道的稿件接成一条线。线内若拆成两件不同的事，会再分开。一个查询窗（某月，或 4 月到 9 月）里稿量较大的线成为核心议题，其余为次要议题。`slice` 把窗口结果收成网页数据。

**主体**从抽取结果里收集人名、部队、国家和装备提及，合并别名后，按同一查询窗统计出现次数和共现，导出主体页。

**事件图**把抽取行写成 Document、Chunk、Event 和边。`reason.daily` 按事件日列出当天事项；`reason.timeline` 按主体或地点列出一段日期内的前后稿。清单句子附有稿件编号，可对回原文。图文件是 JSON。报告层、多源合并和证据包问答对应 `sitrep.s1.json` 至 `sitrep.s6.json`，说明见 [开发记录](doc/开发记录.md)。

时间切分与相似度阈值在 `theme/config.py`：调参段 2026-03-29 至 2026-08-31，测试段 2026-09-01 至 2026-10-02；并入阈值 0.72，休眠 7 天，分裂阈值 0.90。

## 克隆之后

两张可视化页面随仓库一起提供，克隆后即可打开，步骤见下面的「查看已有结果」。

重跑采集、抽取、主题、主体和事件图时，还要自备这些：

| 项 | 说明 |
|----|------|
| 原稿 | 环球网与 RSS 的按日 jsonl 在 `data/raw/huanqiu_mil/`、`data/raw/rss_api/`，克隆后可直接拿来做抽取和小范围主题试验。去重库 `data/seen.sqlite`、评测集 `data/raw/cmnee/` 不在仓库里 |
| 向量模型 | `theme/config.py` 的 `MODEL_PATH` 现为 `/data/users/wyd/models/embedding/bge-small-zh-v1.5`。换机器后改成自己的 `bge-small-zh-v1.5` 目录，并安装 `sentence-transformers` 和 `numpy` |
| 大模型 | 抽取、译题、议题命名、主体分类需要 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` |
| RSS | 合作方接口写在 `ingest/crawl/fulltext.py`，地址为 `http://8.130.80.151/rss-api/api/public/news`。跑 `--source rss` 时这台机器要能访问该地址 |

`pip install -r requirements.txt` 只装采集用的包。

## 使用说明

在仓库根目录执行命令。

### 环境

```bash
pip install -r requirements.txt
```

`requirements.txt` 只包含采集依赖。主题编码另需 `sentence-transformers` 和 `numpy`。模型目录是 `theme/config.py` 里的 `MODEL_PATH`，默认指向本机路径，换机器时要改。

抽取、英文标题翻译、议题命名、主体分类走 OpenAI 兼容接口，用环境变量：

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=...
export LLM_MODEL=...
```

### 查看已有结果

```bash
cd theme/web
python3 -m http.server 8765 --bind 127.0.0.1
```

| 页面 | 地址 | 内容 |
|------|------|------|
| 主题 | http://127.0.0.1:8765/ | 时间窗、河图（每日稿量）、核心议题气泡、议题脉络与见报标题 |
| 主体 | http://127.0.0.1:8765/actors.html | 同一时间窗的实体共现和相关标题 |

页面读取同目录的 `data.js`、`actor-data.js`。远程环境把 8765 转到本机。更新切片后重新导出这两个文件，再刷新浏览器。

`python3 -m annotate` 也绑定 8765，与静态页不要同时开。

### 1. 采集

```bash
# 拉最新若干篇。默认 --source all，同时跑环球网和 RSS
python3 -m ingest.crawl once --limit 12 --delay 0.7

# 只跑合作方 RSS，英语白名单
python3 -m ingest.crawl once --source rss --limit 40 --delay 0.7

# 按见报日回溯。--until 仅 RSS 使用
python3 -m ingest.crawl backfill --source rss \
  --since 2026-10-01 --until 2026-10-02 --limit 500 --delay 0.7

# 环球网回溯
python3 -m ingest.crawl backfill --source huanqiu --since 2026-07-01 --limit 2500

# 少量验收，并写 data/reports/crawl_smoke.md
python3 -m ingest.crawl smoke --source rss --limit 8 --delay 0.7

# 按已落盘 jsonl 重出报告
python3 -m ingest.crawl report
```

| 参数 | 作用 |
|------|------|
| `--source` | `huanqiu`、`rss` 或 `all` |
| `--limit` | 最多抓取的正文篇数 |
| `--delay` | 同一主机两次请求的间隔（秒） |
| `--since` / `--until` | 见报日起止，含当日，格式 `YYYY-MM-DD` |
| `--force` | 忽略去重库，已入库的链接也重新抓 |
| `--lang` | RSS 语言，默认 `en`；`all` 为白名单内全部语言 |
| `--variant` | RSS 分组：`military`（默认）或 `intel` |
| `--per-day` | RSS 每个见报日最多入库篇数，`0` 表示不截断 |

落盘路径：

| 路径 | 内容 |
|------|------|
| `data/raw/huanqiu_mil/YYYY-MM-DD.jsonl` | 环球网，按日追加 |
| `data/raw/rss_api/YYYY-MM-DD.jsonl` | RSS，按日追加 |
| `data/seen.sqlite` | 已见链接 |
| `data/reports/latest_run.jsonl` | 本轮抓到的稿件 |

RSS 条目若无全文，会再请求 HTML 补正文。正文过短的条目不会入库。

### 2. 抽取

读取 `data/raw/` 中指定来源、指定日期内的稿件，写出事件 JSONL。

```bash
# 小范围试跑
python3 -m extract --source huanqiu_mil \
  --from 2026-08-20 --to 2026-08-24 --max-docs 40

# RSS
python3 -m extract --source rss_api \
  --from 2026-09-01 --to 2026-10-02 --max-docs 0 \
  --out data/extract/openie_event_rss.jsonl

# 全量续跑：跳过输出文件里已经成功的 doc_id
python3 -m extract --source huanqiu_mil \
  --from 2026-07-01 --to 2026-08-24 --max-docs 0 --resume \
  --out data/extract/openie_event.jsonl
```

| 参数 | 作用 |
|------|------|
| `--from` / `--to` | 见报日起止 |
| `--source` | `huanqiu_mil` 或 `rss_api`；空字符串表示不限来源 |
| `--max-docs` | 最多篇数，`0` 为不截断 |
| `--out` | 输出 JSONL，默认 `data/extract/openie_event.jsonl` |
| `--resume` | 追加写入，跳过已成功的文档 |
| `--ids-from` | 只抽某个 JSONL 里列出的 `doc_id` |
| `--include-undated` | 纳入没有 `publish_time` 的文档 |

默认禁止写入 `data/extract/openie.jsonl`，该文件是早先的全量基线。

### 3. 主题

四步都要按顺序执行。`embed` 依赖 `catalog.jsonl`，`assign` 依赖向量，`slice` 依赖接线结果。

```bash
python3 -m theme catalog
python3 -m theme embed --batch-size 32
python3 -m theme assign --split
python3 -m theme slice --from 2026-04-01 --to 2026-09-30
python3 -m theme narrative
```

| 命令 | 读取 | 写出 |
|------|------|------|
| `catalog` | `data/raw/` | `data/theme/catalog.jsonl`、`title_zh.json`、`data/reports/theme_catalog.md` |
| `embed` | 清单 | `data/theme/embed.npz`、`embed_meta.json` |
| `assign --split` | 向量 | `data/theme/assign_tune_split_t0.72_d7_sp0.90.jsonl` 及同名 stories、splits、指标 |
| `slice` | 接线结果 | `data/theme/slice_<起>_<止>.json`，并重写 `theme/web/data.js` |
| `narrative` | 已有切片 | 为核心议题补事态脉络，缓存在 `data/theme/narrative_cache.json` |

`slice` 还可指定窗口和聚类方式：

```bash
python3 -m theme slice --from 2026-09-01 --to 2026-09-30 --cluster judge --name auto
```

`--cluster` 可选 `judge`（默认，向量候选后再判定）、`theater`、`query`、`tree`。`--name auto` 在有模型密钥时用模型起短名，否则用规则压缩标题。`narrative --force` 忽略缓存重算脉络。

改完切片后重启或刷新静态页。

### 4. 主体

```bash
python3 -m actor harvest
python3 -m actor link
python3 -m actor slice --from 2026-04-01 --to 2026-09-30

# 上面三步一次做完，并按内置的月份窗口各切一张
python3 -m actor build
```

| 命令 | 作用 |
|------|------|
| `harvest` | 从抽取 JSONL 收集提及；默认同时读环球网与 RSS。`--no-scan` 时不扫没有抽取结果的原稿 |
| `link` | 别名合并。`--llm` 时对剩余高频名字再做一次分类 |
| `slice` | 一个查询窗的排行和共现，写出 `data/actor/slice_*.json` 与对应报告，并更新 `actor-data.js` |
| `build` | `harvest` + `link` + 各月切片 |

### 5. 事件图与查询

```bash
python3 -m graph.build \
  --in data/extract/openie_event.jsonl \
  --out data/graph/sitrep.b1.json

python3 -m align.draft --all
python3 -m align.apply --graph data/graph/sitrep.b1.json

python3 -m reason.daily --date 2026-08-20 --graph data/graph/sitrep.b1.json
python3 -m reason.daily --date 2026-08-20 --graph data/graph/sitrep.b1.json --llm

python3 -m reason.timeline --graph data/graph/sitrep.b1.json \
  --actors 以军,以色列国防军 --from 2026-08-18 --to 2026-08-24
```

`graph.build` 的默认输入是旧基线 `openie.jsonl`，默认输出是 `sitrep.b1.json`。使用本次抽取时显式传入 `--in`。`align.apply` 在每次重新写图之后再跑一遍，把主体和地点写回规范名。

`reason.daily` 按**事件日**过滤，输出 `data/reports/daily_YYYY-MM-DD.md`。加上 `--llm` 时，在同一证据包上再写一版简报到 `daily_YYYY-MM-DD.cot.md`。`reason.timeline` 的 `--actors`、`--locations` 为逗号分隔的名字，`--from`、`--to` 为日期，结果在 `data/reports/timeline_slice.md`。

### 6. 试点标注

从抽取结果中取 20 块，在网页里对照修改。两位标注员的定稿分目录保存。

```bash
python3 -m annotate --host 127.0.0.1 --port 8765
```

浏览器打开 http://127.0.0.1:8765 ，一致率页面为 http://127.0.0.1:8765/iaa.html 。预标在 `data/extract/openie_event_pilot.jsonl`，定稿在 `data/eval/annotations/{A,B}/`。说明见 [开放事件标注指南](doc/开放事件标注指南.md)。

## 目录

| 路径 | 说明 |
|------|------|
| `ingest/` | 采集与去重 |
| `extract/` | 事件抽取 |
| `theme/` | 议题聚类、切片、导出 |
| `actor/` | 主体对齐、切片、导出 |
| `graph/`、`reason/` | 写图、清单、时间线 |
| `align/` | 主体与地点的规范名 |
| `annotate/` | 试点标注服务 |
| `theme/web/` | 主题页、主体页 |
| `doc/` | [规划](doc/规划.md)、[开发记录](doc/开发记录.md) |
| `data/theme/`、`data/actor/` | 已提交的切片与网页所用数据 |

仓库包含 `data/raw/huanqiu_mil/`（约 4.4MB，187 个按日文件）和 `data/raw/rss_api/`（约 1MB，14 个按日文件），克隆后可以用它们试抽取。`.gitignore` 仍排除 `data/raw/cmnee/`、`data/extract/`、`data/graph/`、`data/reports/`、`data/theme/embed.npz` 和 `data/seen.sqlite`。换机器重做全量图时，需要在本机重新抽取并写图。
