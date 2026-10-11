# 军事与情报安全新闻数据接口

面向外部研究与安全分析场景的只读数据接口,提供军事、情报安全领域新闻的结构化检索。 接口基于统一的 RSS 新闻归档服务。 本文档描述该服务在军事 / 情报安全场景下的专用调用方式、数据构成与边界。通用字段 语义、表结构、限流机制以主文档为准,不在此重复。

## 基本信息


| 项       | 值                                             |
| ------- | --------------------------------------------- |
| 接口地址    | `http://8.130.80.151/rss-api/api/public/news` |
| 协议      | HTTP GET,只读,无需鉴权                              |
| 数据范围    | `variant=military`(军事)、`variant=intel`(情报安全)  |
| 军事条目量   | 1755 篇,来自 153 个信源                             |
| 情报安全条目量 | 66 篇(`intel` 口径),来自 15 个信源                    |
| 时间覆盖    | 2026-09-20 至 2026-09-24                       |
| 限流      | 每 IP 每分钟 60 次请求,详见主文档                         |


## 检索方式

### 按顶层分组检索(推荐)

`variant` 参数用于按数据采集分组一次性获取某一领域的全部数据,是军事 / 情报安全
场景下的标准检索方式。

```bash
# 军事全量数据,单次 limit 上限 1000,需分页取完
curl -s "http://8.130.80.151/rss-api/api/public/news?variant=military&limit=1000" \
  -o military-p1.json
curl -s "http://8.130.80.151/rss-api/api/public/news?variant=military&limit=1000&offset=1000" \
  -o military-p2.json

# 情报安全全量数据
curl -s "http://8.130.80.151/rss-api/api/public/news?variant=intel&limit=100" \
  -o intel.json
```



### 按子分类检索

需要按细分类别(如仅需官方一手源,或仅需 OSINT 源)分别落盘时,使用 `category`
参数逐类检索。`category` 为单值参数,一次请求只能指定一个分类值,多分类检索需
逐一请求:

```bash
for c in military-official military-official-media military-trade-media \
         military-service-branch military-think-tank military-osint \
         military-regional intel-security; do
  curl -s "http://8.130.80.151/rss-api/api/public/news?category=$c&limit=1000" \
    -o "military-$c.json"
done
```

`limit` 参数上限为 1000,超出上限的取值将被截断至 1000,不会返回错误;`total`
字段始终反映筛选条件下的真实总量,可用于判断是否需要分页。

## 分类字典

军事领域下设七个子分类,情报安全为独立分类,均为 `category` 参数的合法取值:


| category                  | 领域   | 文章数  | 信源数 | 说明                                         |
| ------------------------- | ---- | ---- | --- | ------------------------------------------ |
| `military-trade-media`    | 军事   | 1455 | 73  | 军事专业媒体,主要数据来源,如 tass.com、ukrinform.net     |
| `military-official-media` | 军事   | 218  | 4   | 官方背景媒体                                     |
| `military-service-branch` | 军事   | 57   | 60  | 各国军种垂直信源                                   |
| `military-think-tank`     | 军事   | 21   | 8   | 军事智库                                       |
| `military-official`       | 军事   | 3    | 5   | 官方一手信源,如美国 CRS Reports Defense、英国国防部、印度国防部 |
| `military-osint`          | 军事   | 1    | 2   | 开源情报(OSINT)信源,含 Drone Wars UK、Intel News   |
| `military-regional`       | 军事   | 0    | 1   | 地区专项信源,当前无产出                               |
| `intel-security`          | 情报安全 | 74   | 15+ | 情报与安全领域报道,跨 `intel`、`tech` 两个采集分组          |


信源产出集中度较高,按文章数排名前列的信源为:tass.com(178)、ukrinform.net
(114)、english.nv.ua(110)、en.interfax.com.ua(105)、jpost.com(97)、
kyivpost.com(83)、defence24.pl(78)。

## 情报安全数据的口径说明

情报安全数据存在两种统计口径,调用与引用时需明确区分:

- `variant=intel` **口径**:66 篇,15 个信源(含 gCaptain、Military Times、
The War Zone、Defense News、Task & Purpose 等)。
- `category=intel-security` **口径**:74 篇,覆盖 `intel` 与 `tech` 两个采集
分组——其中 8 篇归档于 `tech` 采集管线下。

两种口径不可互相替代,统计与引用时应明确注明所采用的口径。

## 响应结构

响应格式与主接口一致,详见 [rss-archive-api.md § 接口](./rss-archive-api.md)。
军事 / 情报安全场景下的典型条目示例:

```json
{
  "items": [
    {
      "id": 12345,
      "title": "...",
      "url": "https://...",
      "description": "...",
      "contentText": "...",
      "author": "...",
      "categories": ["..."],
      "publishedAt": "2026-09-21T14:00:00.000Z",
      "lang": "en",
      "langVerified": false,
      "variant": "military",
      "category": "military-trade-media",
      "source": { "id": 7, "name": "Defense News", "domain": "defensenews.com" }
    }
  ],
  "total": 1755,
  "limit": 1000,
  "offset": 0
}
```



## 数据范围声明

- **时间范围**:军事数据采集管线自 2026-09-21 上线,当前可用数据覆盖
2026-09-20 至 2026-09-24,共 4 天。此为采集管线启用时间所限,非历史数据缺失。
- **正文覆盖率**:军事数据(1755 篇)中约 20%(344 篇)含完整正文
(`contentText`),其余为摘要片段;情报安全数据(66 篇)正文覆盖率约 52%
(34 篇)。
- **语言标注**:军事数据全部 153 个信源、情报安全数据全部 15 个信源均标注为
`lang=en` 且 `langVerified=false`,即语言字段为默认值,未经人工确认。
- **信源产出不均**:`military-service-branch` 分类下 60 个信源中多数尚无产出
(`articleCount=0`),信源数量不代表实际数据量。
- 其余通用数据边界(作者 / 分类标签 / 正文字段的补齐时间点、`publishedAt`
未经校验、Google News 代理源不可达等)见主文档
[§ 数据边界](./rss-archive-api.md#数据边界请先读这节)。



## 限流与引用

本接口与主接口共用限流与授权规则:每 IP 每分钟 60 次请求,每 IP 每小时 10 万行
返回量,超限返回 `429` 并附 `Retry-After`。数据引用需标注 `url`、`source`、
`author` 字段;涉及原文内容再分发的,需自行确认对应信源的授权条款。详见主文档
[§ 限流](./rss-archive-api.md#限流)与
[§ 引用与版权](./rss-archive-api.md#引用与版权)。