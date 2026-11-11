"""多主体路径与默认窗。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ACTOR_DIR = DATA / "actor"
EXTRACT_PATH = DATA / "extract" / "openie_event.jsonl"
RSS_EXTRACT_PATH = DATA / "extract" / "openie_event_rss.jsonl"
REPORT_DIR = DATA / "reports"
WEB_DIR = ROOT / "theme" / "web"

DEFAULT_FROM = "2026-01-01"
DEFAULT_TO = "2026-10-31"

TYPES = ("Person", "Org", "Country", "MilitaryEntity", "Location", "Document", "Media")
KEEP_ROLES = ("主体", "对象", "装备")
MAX_NAME_CHARS = 36
MIN_NAME_CHARS = 2
CO_MIN = 2
GRAPH_TOP = 48
REL_EDGE_CAP = 80
RANK_TOP = 30
ARTS_CAP = 80
