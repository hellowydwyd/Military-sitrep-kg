# Military-sitrep-kg

从公开新闻整理一段时间的报道态势，并从稿件里抽军事态势知识图谱。

来源两路：环球网军事，以及合作方 RSS。RSS 默认只收英语，不按军事分组过滤。英文稿的标题译成中文后参与聚类，正文保留原文。

当前主线是三任务知识图谱：**NER（五类实体）、EE（十六类事件）、RE（十种结构关系）**，三个任务分开出数据卷、分开评测。早期做过开放事件抽取和十类维度抽取，都归档在 `legacy/`，只作回溯。

## 整体流程

```
采集 ingest
  环球网栏目 / RSS 接口
  → data/raw/<source>/YYYY-MM-DD.jsonl
  → data/seen.sqlite（按链接去重）

抽取 extract
  python3 -m extract.kg_batch
  → data/extract/kg<tag>_<source>_<月>.jsonl
    每篇：事件、五类实体、角色关系、人物属性

规范化 align
  align.zh      汉语规范名（离线查维基标题）
  align.types   同名的多种类型归并
  align.disamb  实体消歧与事件融合
  align.edges   边的语义细分
  extract.title_fix  职务串从机构改判为人物

数据集 dataset
  dataset.build   三分卷（7:1.5:1.5）
  dataset.export  BIO / CMNEE / DuIE 三种格式
  → data/dataset/{ner,ee,re}_{train,dev,test}.jsonl
  → data/dataset/export/

标注 annotate
  python3 -m annotate --port 8767
  → data/annotations/{task}_{annotator}.jsonl

────────────────────────────────
旁支（早期方案，仍在用）
  theme/    议题聚类与切片 → theme/web/data.js
  actor/    主体对齐与共现 → theme/web/actor-data.js
  graph/    属性图构建与查询 → data/graph/*.json
  reason/   日报、时间线、证据包问答
────────────────────────────────
```

每篇入库稿含标题、正文、发布时间、来源、链接。同一链接只保留一次。正文短于 80 字的记 `skip-short`，不进抽取。

## 克隆之后

| 项 | 说明 |
| --- | --- |
| 原稿 | `data/raw/huanqiu_mil/`、`data/raw/rss_api/` 已入库，可直接试抽取 |
| 向量模型 | `theme/config.py` 的 `MODEL_PATH` 现为本机绝对路径，换机器要改。另需 `sentence-transformers`、`numpy` |
| 大模型 | 抽取、译题、议题命名、消歧需要 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` |
| RSS | 合作方接口 `http://8.130.80.151/rss-api/api/public/news`，跑 `--source rss` 时需能访问 |
| 维基索引 | `data/zhwiki/` 不入库（212MB）。从 `ftp.acc.umu.se` 镜像取当年 `zhwiki-<日期>-pages-articles-multistream-index.txt.bz2` 放入该目录，`ingest/zhwiki.py` 直接读它 |

`pip install -r requirements.txt` 只装采集用的包。

```bash
export LLM_API_KEY=...
export LLM_BASE_URL=https://api.deepseek.com
export LLM_MODEL=deepseek-chat
```

## 三任务

本体设计见 [本体与关系设计方案](doc/本体与关系设计方案.md)，标注口径见 [三任务标注规范](doc/三任务标注规范.md)。

| 任务 | 标注内容 | 评测指标 |
| --- | --- | --- |
| NER | 五类实体：机构、人物、装备、地点、作品 | 片段 + 类型 F1 |
| EE | 十六类事件、六类论元、话语种类 | Trg-F1、Arg-F1、事件边界 |
| RE | 十种结构关系（隶属、辖属、驻地、部署方、去向、任职、会晤、搭载、位于、敌对） | 按关系种类分别报 P/R/F1 |

事件不是实体，单独统计。实体 F1 不含事件。

### 1. 抽取

```bash
# 100 篇试抽
python3 -m extract.kg_batch --source huanqiu_mil \
  --from 2026-10-01 --to 2026-10-09 --tag kgab

# 全量，断点续跑
python3 -m extract.kg_batch --source huanqiu_mil \
  --from 2026-01-01 --to 2026-10-31 --tag kgfull --resume --sleep 0.5
```

| 参数 | 作用 |
| --- | --- |
| `--source` | 子目录名，如 `huanqiu_mil`、`rss_api` |
| `--from` / `--to` | 见报日起止，含当日 |
| `--limit` | 最多篇数，`0` 为不截断 |
| `--tag` | 输出文件名前缀，输出到 `data/extract/` |
| `--resume` | 追加写入，跳过已成功的 `doc_id` |
| `--max-fail` | 连续失败多少篇后中止 |

### 2. 规范化

```bash
# 汉语规范名。build 查维基，localize 写回抽取结果
python3 -m align.zh build --src data/extract/kgfull_huanqiu_mil_2026-01.jsonl
python3 -m align.zh localize --src data/extract/kgin.jsonl --out data/extract/kgout.jsonl

# 同一名称的多种类型归并
python3 -m align.types --src data/extract/kgin.jsonl

# 实体消歧与事件融合
python3 -m align.disamb

# 职务串从机构改判为人物（可对任意抽取结果跑）
python3 -m extract.title_fix \
  --src data/extract/full_zh_localized2.jsonl \
  --out data/extract/full_zh_titlefix.jsonl
```

`align` 各子模块是独立命令，没有统一入口，`python3 -m align` 会列出全部。

### 3. 数据集

```bash
python3 -m dataset.build --src data/extract/kgab_huanqiu_mil_2026-10.jsonl
python3 -m dataset.export --task all --split all
```

`build` 从 `data/raw` 按 URL 回填正文（抽取结果不含正文），按 7:1.5:1.5 切分，写出九个 jsonl。`export` 转成三种公开格式，写到 `data/dataset/export/`。

### 4. 标注

```bash
python3 -m annotate --port 8767
```

浏览器打开 http://127.0.0.1:8767 。

界面三栏：左侧统计与快捷键，中间原文（实体按类型着色），右侧逐条判定。支持框选补标、跳过、快捷键 `1/2/3` 判定、`←/→` 切换候选、`Enter` 提交。

判定记录写到 `data/annotations/{task}_{annotator}.jsonl`，追加写入保留历史。一致性门槛见标注规范第五节。

## 旁支

### 议题与主体

```bash
python3 -m theme catalog && python3 -m theme embed --batch-size 32
python3 -m theme assign --split
python3 -m theme slice --from 2026-09-01 --to 2026-09-30

python3 -m actor build
```

结果导出到 `theme/web/data.js`、`actor-data.js`，用静态服务查看：

```bash
cd theme/web && python3 -m http.server 8765 --bind 127.0.0.1
```

| 页面 | 地址 |
| --- | --- |
| 主题 | http://127.0.0.1:8765/ |
| 主体 | http://127.0.0.1:8765/actors.html |

### 事件图与查询

```bash
# 建图
python3 -m graph.build10 --src data/extract/kgab_huanqiu_mil_2026-10.jsonl \
  --out data/graph/kg10.json

# 起查询服务
python3 -m graph.serve10 --src data/graph/kg10.json --port 8765
```

`serve10` 是单图常驻内存、按节点按需加载，不做按月切片——按月切会把高连接节点的边切散。

```bash
python3 -m reason.daily --date 2026-08-20
python3 -m reason.timeline --actors 以军,以色列国防军 --from 2026-08-18 --to 2026-08-24
```

`reason.daily` 按**事件日**过滤，写 `data/reports/daily_YYYY-MM-DD.md`。加 `--llm` 再写一版简报。

### 采集

```bash
# 拉最新
python3 -m ingest.crawl once --limit 12 --delay 0.7

# 只跑 RSS，回溯
python3 -m ingest.crawl backfill --source rss \
  --since 2026-10-01 --until 2026-11-02 --limit 500 --delay 0.7

# 环球网回溯
python3 -m ingest.crawl backfill --source huanqiu --since 2026-07-01 --limit 2500
```

| 参数 | 作用 |
| --- | --- |
| `--source` | `huanqiu`、`rss` 或 `all` |
| `--lang` | RSS 语言，默认 `en`；`all` 不按语言过滤 |
| `--variant` | RSS 分组，默认 `all`；也可 `military`、`intel`、`tech` |
| `--per-day` | 每个见报日最多入库篇数 |

## 目录

| 路径 | 说明 |
| --- | --- |
| `ingest/` | 采集、去重、维基索引 |
| `extract/` | 事件中心抽取（`kg_extract.py` 是核心）、批量、职务串改判 |
| `align/` | 规范名、类型归并、消歧、边细分 |
| `dataset/` | 三分卷构建与格式导出 |
| `annotate/` | 三任务标注服务 |
| `theme/` | 议题聚类、切片、网页导出 |
| `actor/` | 主体对齐、切片、网页导出 |
| `graph/` | 属性图构建、查询服务 |
| `reason/` | 日报、时间线、证据包 |
| `doc/` | 设计文档与标注规范 |
| `data/` | 数据，见 [data/README](data/README.md) |
| `legacy/` | 已停用的旧方案，见 [legacy/README](legacy/README.md) |
| `eval/` | 评测（`story_discovery/` 为外部数据集，384MB，不入库） |

## 文档

| 文档 | 内容 |
| --- | --- |
| [本体与关系设计方案](doc/本体与关系设计方案.md) | 五类实体、十六类事件、十七种关系的定义与判据 |
| [三任务标注规范](doc/三任务标注规范.md) | 标注口径、易混边界、一致性门槛 |
| [知识图谱构建子系统技术路线](doc/知识图谱构建子系统技术路线.md) | 子系统整体设计 |
| [5.6.3.2.3 知识图谱构建子系统](doc/5.6.3.2.3知识图谱构建子系统.md) | 交付文档 |
| [十类图谱全量抽取实验记录](doc/十类图谱全量抽取实验记录.md) | 早期十类方案的实验数据 |
| [RSS 平台类别与行文](doc/RSS平台类别与行文.md) | 信源构成与文风统计 |
