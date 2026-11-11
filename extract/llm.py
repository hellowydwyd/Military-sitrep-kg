"""OpenAI 兼容接口。密钥只读环境变量，不写进仓库。

环境变量：
  LLM_API_KEY    必填（也接受 DEEPSEEK_API_KEY / OPENAI_API_KEY）
  LLM_BASE_URL   默认 https://api.deepseek.com
  LLM_MODEL      默认 deepseek-chat
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def _read_key_from_yaml(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("api_key:"):
            val = s.split(":", 1)[1].strip().split()[0]
            return val or None
    return None


def resolve_client() -> tuple[str, str, str]:
    key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    extra = os.environ.get("LLM_CONFIG_YAML", "").strip()
    if not key and extra:
        key = (_read_key_from_yaml(Path(extra)) or "").strip()
    if not key:
        raise RuntimeError(
            "未设置 LLM_API_KEY（或 DEEPSEEK_API_KEY）。"
            "可 export 后重试，或设 LLM_CONFIG_YAML 指向含 api_key 的 yaml。"
        )
    base = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE).rstrip("/")
    model = os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
    return key, base, model


def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    retries: int = 3,
    timeout: int = 180,
    max_tokens: int = 8000,
) -> str:
    key, base, model = resolve_client()
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last: Exception | None = None
    # 连接与读取分开限时，避免逐字节回传时整体超时被绕过
    deadline = (min(15, timeout), max(60, timeout))
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=deadline)
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                last = RuntimeError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                last = RuntimeError("空响应")
                time.sleep(1.0 * (attempt + 1))
                continue
            finish = str(choice.get("finish_reason") or "")
            if finish == "length":
                # 输出被 token 上限截断，结果不完整，按失败重试
                last = RuntimeError("输出被截断 finish_reason=length")
                print(f"  [warn] 输出截断，重试第 {attempt + 1} 次", file=sys.stderr)
                time.sleep(1.0 * (attempt + 1))
                continue
            return content
        except (requests.RequestException, KeyError, IndexError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last}")
