# data 目录说明

按「进 → 出」顺序排列。前一步写出，后一步读取。

| 目录 | 来源 | 内容 | 入库 |
| --- | --- | --- | --- |
| `raw/huanqiu_mil/`、`raw/rss_api/` | 采集 | 按日 jsonl，一篇一行，含标题、正文、时间、链接 | 是 |
| `extract/` | `extract.kg_batch` | 事件、五类实体、角色关系、人物属性 | 否（体积大） |
| `dataset/` | `dataset.build` | NER / EE / RE 三分卷，7:1.5:1.5 | 是 |
| `dataset/export/` | `dataset.export` | BIO / CMNEE / DuIE 三种格式 | 是 |
| `annotations/` | 标注工具 | 每任务每标注员一个 jsonl | 否（随标注产生） |
| `zh/` | `align.zh` | 汉语规范名对照、人物底档 | 是（重新生成要 55 分钟） |
| `zhwiki/` | `ingest.zhwiki` | 维基索引压缩包与标题表 | 否（212MB 可重下） |
| `theme/` | `theme.slice` | 议题切片、网页数据 | 部分是 |
| `actor/` | `actor.slice` | 主体提及、共现、切片 | 部分是 |
| `graph/` | `graph.build10` | 属性图 JSON | 否（体积大） |
| `reports/` | 各环节 | Markdown 报告 | 否 |
| `eval/` | 手工 | 金标、缓存 | 部分是 |
| `archive/` | 整理时 | 旧版本中间文件，只作回溯 | 否 |

## 抽取结果的版本链

`data/extract/full_zh_*` 是全量中文稿（4115 篇）的逐级处理结果。整理时只保留当前还在用的三个：

| 文件 | 用途 |
| --- | --- |
| `full_zh_localized2.jsonl` | `align.zh localize` 的输出，`title_fix` 的输入 |
| `full_zh_final2.jsonl` | `align.disamb` 的输入 |
| `full_zh_titlefix.jsonl` | 职务串改判后的最新版 |

其余 13 个中间版本（`clean`、`evfix`、`typed`、`typed2`、`scenefix`、`localized`、`final`、`final3`、`resolved`、`resolved2`、`edges`、`edges2`、`edges3`）已移到 `archive/extract/`，共 566MB。

`kg*` 前缀是事件中心方案的试抽结果：

| 文件 | 内容 |
| --- | --- |
| `kgab_huanqiu_mil_2026-10.jsonl` | 100 篇，三分卷数据集的正本来源 |
| `kg_full_huanqiu_mil_2026-01.jsonl` | 173 篇有效 |
| `kgtest_comp.jsonl` | 89 篇含复合事件 |
| `kgpilot*.jsonl` | 十类方案的三轮试点，可删 |
| `batch12_huanqiu_mil_2026-01.jsonl` | 早期批处理 |

## 归档说明

`archive/extract_openie/` 是旧开放事件方案的抽取结果（`openie*.jsonl`）。旧方案的代码在 `legacy/`，说明见 `legacy/README.md`。

这些文件不在 git 里，只在本机。确认不再需要后可直接删除，释放约 700MB。
