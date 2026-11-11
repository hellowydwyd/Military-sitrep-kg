"""主题演化冻结配置。切分日期看数后不得改。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
THEME_DIR = DATA / "theme"
RAW_DIR = DATA / "raw"
REPORT_DIR = DATA / "reports"

SOURCE = "huanqiu_mil"
SOURCES = ("huanqiu_mil", "rss_api")
MODEL_ID = "BAAI/bge-small-zh-v1.5"
MODEL_PATH = Path("/data/users/wyd/models/embedding/bge-small-zh-v1.5")
EMBED_DIM = 512

# 见报日闭区间。调参/测试切分看数后不得改。
TUNE_FROM = "2026-03-29"
TUNE_TO = "2026-08-31"
TEST_FROM = "2026-09-01"
TEST_TO = "2026-10-02"
# 查询窗可以盖住已入库的见报日。调参/测试切分仍停在 TUNE_* / TEST_*。
WINDOW_FROM = "2026-01-01"
WINDOW_TO = "2026-10-31"
GOLD_FROM = "2026-09-19"
GOLD_TO = "2026-10-02"

TITLE_REPEAT = 1  # s = title*(1+TITLE_REPEAT) + body → 标题出现两次
MIN_BODY_CHARS = 80

# 出发值（仅调参段可改；测试段只读）
THETA_IN = 0.72
DELTA_DORMANT = 7
# 0.82 拆不开吸引子（终态 2-means 余弦约 0.88–0.95）；调参段改为 0.90
THETA_SP = 0.90
N_CHK = 6
N_MIN = 2
LAMBDA_SCORE = 0.7
# 0.45 在 8 月窗只剩 2 条核心；调参段改为 0.15 → 约 5 条（目标 5～8）
RHO_CORE = 0.15
M_MIN = 2
M_MIN_CH = 2
DELTA_SLICE = 7
SLICE_OVERLAP = 0
SMOOTH_DAYS = 7
CORE_MAX = 8
THETA_M = 0.82
THETA_SH = 0.90
THETA_THEME = 0.78  # 有标签块内向量合并
THETA_UNLABELED = 0.86  # 无战场标签块内更严，避免空桶成团
THETA_ATTACH = 0.78  # 散稿贴已有 / 彼此成团。勿降到 0.70，会把海军多线糊成一团
ETA_SLOPE = 0.4
Z_MAX = 3.0
Z_EPS = 1e-6


def split_of(day: str) -> str:
    """tune / test / gap / other。金标窗是 test 的子集，见 in_gold。"""
    if len(day) < 10:
        return "drop"
    if TUNE_FROM <= day <= TUNE_TO:
        return "tune"
    if TEST_FROM <= day <= TEST_TO:
        return "test"
    if day < TUNE_FROM or day > TEST_TO:
        return "other"
    return "gap"


def in_gold(day: str) -> bool:
    return len(day) >= 10 and GOLD_FROM <= day <= GOLD_TO


def encode_text(title: str, body: str) -> str:
    t = (title or "").strip()
    b = (body or "").strip()
    return "\n".join([t] * (1 + TITLE_REPEAT) + ([b] if b else []))
