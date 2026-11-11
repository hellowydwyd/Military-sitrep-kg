"""人物要素抽取的数据类型登记。验收要求不少于 10 种。"""

from __future__ import annotations

from dataclasses import dataclass

from ingest.envelope import SOURCE_CMNEE, SOURCE_HUANQIU, SOURCE_RSS

MIN_DATA_TYPES = 10

# RSS category → 类型。与 doc/data_api.md 的分类字典一致。
RSS_CATEGORY = {
    "military-trade-media": "rss_trade_media",
    "military-official-media": "rss_official_media",
    "military-service-branch": "rss_service_branch",
    "military-think-tank": "rss_think_tank",
    "military-official": "rss_official",
    "military-osint": "rss_osint",
    "military-regional": "rss_regional",
    "intel-security": "rss_intel",
}


@dataclass(frozen=True)
class DataType:
    id: str
    label: str
    source: str
    extra: dict
    summary: str


DATA_TYPES: tuple[DataType, ...] = (
    DataType("huanqiu_portal", "中文军事门户新闻", SOURCE_HUANQIU, {}, "环球网军事栏目全文"),
    DataType(
        "rss_trade_media",
        "军事专业媒体",
        SOURCE_RSS,
        {"category": "military-trade-media", "variant": "military"},
        "军事专业媒体稿",
    ),
    DataType(
        "rss_official_media",
        "官方背景媒体",
        SOURCE_RSS,
        {"category": "military-official-media", "variant": "military"},
        "官方背景媒体稿",
    ),
    DataType(
        "rss_service_branch",
        "军种垂直信源",
        SOURCE_RSS,
        {"category": "military-service-branch", "variant": "military"},
        "各国军种站点稿",
    ),
    DataType(
        "rss_think_tank",
        "军事智库",
        SOURCE_RSS,
        {"category": "military-think-tank", "variant": "military"},
        "智库评论与报告转载",
    ),
    DataType(
        "rss_official",
        "官方一手通报",
        SOURCE_RSS,
        {"category": "military-official", "variant": "military"},
        "国防部、议会研究机构等一手源",
    ),
    DataType(
        "rss_osint",
        "开源情报报道",
        SOURCE_RSS,
        {"category": "military-osint", "variant": "military"},
        "OSINT 站点稿",
    ),
    DataType(
        "rss_regional",
        "地区专项报道",
        SOURCE_RSS,
        {"category": "military-regional", "variant": "military"},
        "地区专项信源",
    ),
    DataType(
        "rss_intel",
        "情报安全报道",
        SOURCE_RSS,
        {"category": "intel-security", "variant": "intel"},
        "情报与安全领域稿",
    ),
    DataType("cmnee_corpus", "中文军事事件语料", SOURCE_CMNEE, {}, "CMNEE 正文，不用其闭式事件标签"),
)

BY_ID = {item.id: item for item in DATA_TYPES}

if len(DATA_TYPES) < MIN_DATA_TYPES:
    raise RuntimeError(f"数据类型 {len(DATA_TYPES)} 种，少于 {MIN_DATA_TYPES}")
if len(BY_ID) != len(DATA_TYPES):
    raise RuntimeError("数据类型 id 重复")


def classify(source: str, extra: dict | None = None, channel: str = "") -> str:
    """把一篇稿归到登记类型。归不上返回空字符串。"""
    extra = extra or {}
    if source == SOURCE_HUANQIU:
        return "huanqiu_portal"
    if source == SOURCE_CMNEE:
        return "cmnee_corpus"
    if source != SOURCE_RSS:
        return ""
    cat = str(extra.get("category") or "").strip()
    if cat in RSS_CATEGORY:
        return RSS_CATEGORY[cat]
    variant = str(extra.get("variant") or "").strip()
    if variant == "intel" or channel == "情报安全":
        return "rss_intel"
    return ""
