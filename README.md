# Military-sitrep-kg

从公开新闻整理一段时间的报道态势，并从稿件里抽军事态势知识图谱。

来源两路：环球网军事，以及合作方 RSS。RSS 默认只收英语，不按军事分组过滤。英文稿的标题译成中文后参与聚类，正文保留原文。

当前主线是三任务知识图谱：**NER（国家、机构、人物、装备、地点、作品）、EE（七类事件，论元按二级类型封闭）、RE（十种结构关系）**。事件类型和槽位只在 `extract/slots.py` 定义。早期开放抽取和分层图归档在 `legacy/`。

## 整体流程

```
采集 ingest
  环球网栏目 / RSS 接口
  → data/raw/<source>/YYYY-MM-DD.jsonl
  → data/seen.sqlite（按链接去重）

抽取 extract
  python3 -m extract.kg_batch
  → data/extract/kg<tag>_<source>_<月>.jsonl
    每篇：七类事件、六类实体、封闭论元、十种结构关系

事件关系与规范化
  extract.composite / event_links / country_infer / entity_norm
  extract.bitemporal / entity_equal / event_coref
  extract.reextract    按 doc_id 重抽，不改数据集构成
  extract.translate    英文稿译成中文，再走同一套管线
  extract.title_fix    职务串从实体表剔除
  align.types          同名实体的类型归并（旧口径，仍可单独跑）

数据集 dataset
  dataset.preview  抽取结果的校对稿
  dataset.build    三分卷（7:1.5:1.5）
  dataset.export   BIO / CMNEE / DuIE 三种格式
  → data/dataset/{ner,ee,re}_{train,dev,test}.jsonl
  → data/dataset/export/   由 export 写出

标注 annotate
  python3 -m annotate --port 8767
  → data/annotations/{task}_{annotator}.jsonl

抽检 eval
  eval.sample_check   抽样论元与关系，人工判定后算精确率

────────────────────────────────
现行图谱 graph
  graph.build  抽取结果 → data/graph/sitrep.json
  graph.view   读 graph/view.html，写出自包含静态页
────────────────────────────────
旁支（早期方案，仍在用）
  theme/    议题聚类与切片 → theme/web/data.js
  actor/    主体对齐与共现 → theme/web/actor-data.js
  reason/   日报、时间线、证据包问答，读 legacy/graph 的旧分层图
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
| NER | 六类实体：国家、机构、人物、装备、地点、作品 | 片段 + 类型 F1 |
| EE | 七类事件，论元按二级类型封闭，定义在 `extract/slots.py` | Trg-F1、Arg-F1、事件边界 |
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

### 2. 事件关系与国别

抽取结果出来之后，按需要补复合事件、事件间关系和机构辖属。这些步骤各自读写 jsonl，不串成一条隐式流水线。

```bash
python3 -m extract.composite --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl
python3 -m extract.event_links --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl
python3 -m extract.country_infer --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl
```

`align.types` 仍可对旧抽取结果做同名类型归并：`python3 -m align.types --src data/extract/kgin.jsonl`。汉语规范名和消歧模块已经不在仓库里。

按文档集合重抽、译英文稿、清职务串，各自单独跑：

```bash
python3 -m extract.reextract --ids-from data/extract/kgv15_huanqiu_mil_2026-01.jsonl \
  --out data/extract/kgv16_huanqiu_mil_2026-01.jsonl
python3 -m extract.translate --src data/extract/kgen.jsonl \
  --out data/extract/kgen_zh.jsonl
python3 -m extract.title_fix --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl \
  --out data/extract/kgv15_titlefix.jsonl
```

### 3. 数据集

```bash
python3 -m dataset.preview --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl
python3 -m dataset.build --src data/extract/kgab_huanqiu_mil_2026-10.jsonl
python3 -m dataset.export --task all --split all
python3 -m eval.sample_check --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl --n 50
```

`preview` 把抽取结果写成可通读的校对稿。`build` 从 `data/raw` 按 URL 回填正文（抽取结果不含正文），按 7:1.5:1.5 切分，写出九个 jsonl。`export` 转成三种公开格式，写到 `data/dataset/export/`，该目录由命令生成。`sample_check` 抽出论元和关系的抽检清单，人工把 `[ ]` 改成 `[x]` 或 `[-]` 后再 `--score` 算精确率。

### 4. 标注

两种用法，界面相同，输出格式相同。

**离线单文件**，适合发给标注人：

```bash
python3 -m annotate.bundle
```

生成 `annotate/dist/annotate.html`（约 45KB）。把这个文件发出去，双击用浏览器打开即可。标注人自己点「选择文件」载入 jsonl，数据不上传；标完点「导出结果」下载 `verdicts_<标注员>_<日期>.jsonl` 发回。

**服务器版**，适合自己用或同一局域网：

```bash
python3 -m annotate --port 8767
```

浏览器打开 http://127.0.0.1:8767 。顶栏「卷」下拉可指定 `data/dataset/` 下的任意一份 jsonl，也可以不选按任务和划分取。判定记录写到 `data/annotations/{task}_{annotator}.jsonl`，追加写入保留历史。

界面三栏：左侧统计与快捷键，中间原文（实体按类型着色），右侧逐条判定。支持框选补标、跳过、快捷键 `1/2/3` 判定、`←/→` 切换候选、`Enter` 提交。

细节见 [annotate/README](annotate/README.md)。一致性门槛见标注规范第五节。

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
python3 -m graph.build --src data/extract/kgv15_huanqiu_mil_2026-01.jsonl \
  --out data/graph/sitrep.json
python3 -m graph.view --src data/graph/sitrep.json \
  --out annotate/static/graph.html
```

浏览器打开 http://127.0.0.1:8767/static/graph.html （标注服务已在 8767 时）。页面源文件是 `graph/view.html`，`graph.view` 把图谱填进占位符后写出这一份自包含 HTML。页面和 `world.geojson` 放在同一目录。

`reason` 仍读旧的开放抽取分层图，不读上面这份 sitrep 图：

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
| `extract/` | 事件中心抽取（`kg_extract.py` 是核心，槽位只在 `slots.py`）、批量、重抽、翻译、职务串改判 |
| `align/` | 字符串归一、并查集、同名类型归并；供主体对齐和旧图校验使用 |
| `dataset/` | 校对稿、三分卷构建与格式导出 |
| `annotate/` | 三任务标注服务，兼做图谱静态页 |
| `theme/` | 议题聚类、切片、网页导出 |
| `actor/` | 主体对齐、切片、网页导出 |
| `graph/` | 现行事件图谱：`build` 建图，`view.py` 读 `view.html` 写出静态页，`time` 解析时间 |
| `reason/` | 日报与时间线，读 `legacy/graph` 里的旧分层图 |
| `doc/` | 设计文档、评审说明与标注规范 |
| `data/` | 数据，见 [data/README](data/README.md) |
| `legacy/` | 已停用的旧方案，见 [legacy/README](legacy/README.md) |
| `eval/` | `sample_check` 抽检论元与关系；`story_discovery/` 为外部数据集，384MB，不入库 |

## 文档

| 文档 | 内容 |
| --- | --- |
| [本体与关系设计方案](doc/本体与关系设计方案.md) | 实体、事件类型、结构关系的现行定义 |
| [三任务标注规范](doc/三任务标注规范.md) | 标注口径、易混边界、一致性门槛 |
| [论元体系重构说明](doc/论元体系重构说明.md) | 去掉通用角色，论元按二级类型封闭 |
| [论元设计评审](doc/论元设计评审.md) | 对一版论元设计的实测评审 |
| [事件类型体系重构方案](doc/事件类型体系重构方案.md) | 七类事件、两层结构的改法 |
| [实体体系评审](doc/实体体系评审.md) | 六类实体的划分与简称、全称问题 |
| [结构关系评审](doc/结构关系评审.md) | 十种结构关系，重点是辖属链 |
| [事件图谱技术调研](doc/事件图谱技术调研.md) | 事件图谱做法与本项目的对照 |
| [事件图谱构建调研2026](doc/事件图谱构建调研2026.md) | 2026 年的构建方案调研 |
| [RSS 平台类别与行文](doc/RSS平台类别与行文.md) | 信源构成与文风统计 |
| [军事与情报安全新闻数据接口](doc/data_api.md) | 合作方 RSS 接口在本项目中的用法 |

`doc/` 里还有议题方案（[主题分析](doc/主题分析.md)、[主题演化测试方案](doc/主题演化_环球网测试方案.md)）和 [GraphRAG 四系统对比](doc/GraphRAG四系统对比.md)，对应旁支，不在现行抽取主线里。
