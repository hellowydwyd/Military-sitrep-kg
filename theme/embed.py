"""本地 bge 向量缓存。需要 sentence_transformers（可用 autoschemakg 环境）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from theme.config import (
    EMBED_DIM,
    MODEL_ID,
    MODEL_PATH,
    THEME_DIR,
    encode_text,
)
from theme.corpus import iter_raw


def _load_model(model_path: Path):
    from sentence_transformers import SentenceTransformer

    if not model_path.is_dir():
        raise FileNotFoundError(f"编码器不存在: {model_path}")
    return SentenceTransformer(str(model_path), device="cpu")


def _is_en_row(row: dict, raw: dict) -> bool:
    lang = (row.get("lang") or raw.get("lang") or "").lower()
    src = row.get("source") or raw.get("source") or ""
    return lang.startswith("en") or src == "rss_api"


def texts_for_catalog(kept: list[dict]) -> list[str]:
    by_id = {r["doc_id"]: r for r in kept}
    raw_by_id: dict[str, dict] = {}
    for raw in iter_raw():
        did = raw.get("doc_id") or ""
        if did in by_id:
            raw_by_id[did] = raw
    texts = []
    for row in kept:
        raw = raw_by_id.get(row["doc_id"]) or {}
        title = row.get("title_zh") or raw.get("title") or row.get("title") or ""
        if _is_en_row(row, raw):
            texts.append(encode_text(title, ""))
        else:
            texts.append(encode_text(raw.get("title") or row["title"], raw.get("body") or ""))
    return texts


def vec_hash(mat: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(mat).tobytes()).hexdigest()


def embed_catalog(
    kept: list[dict],
    *,
    model_path: Path = MODEL_PATH,
    batch_size: int = 32,
) -> dict:
    THEME_DIR.mkdir(parents=True, exist_ok=True)
    ids = [r["doc_id"] for r in kept]
    old_map: dict[str, np.ndarray] = {}
    npz = THEME_DIR / "embed.npz"
    if npz.is_file():
        old_ids, old_mat = load_embed(npz)
        if old_mat.shape[1] == EMBED_DIM:
            old_map = {i: old_mat[j] for j, i in enumerate(old_ids)}
    need_idx = [i for i, did in enumerate(ids) if did not in old_map]
    texts_all = texts_for_catalog(kept)
    if need_idx:
        model = _load_model(model_path)
        new_texts = [texts_all[i] for i in need_idx]
        vecs = model.encode(
            new_texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        new_mat = np.asarray(vecs, dtype=np.float32)
        for k, i in enumerate(need_idx):
            old_map[ids[i]] = new_mat[k]
        replay_texts = new_texts[: min(8, len(new_texts))]
        replay_src = new_mat[: len(replay_texts)]
    else:
        model = None
        replay_texts = []
        replay_src = None
    mat = np.stack([old_map[did] for did in ids]).astype(np.float32)
    if mat.ndim != 2 or mat.shape[1] != EMBED_DIM:
        raise RuntimeError(f"期望 (*, {EMBED_DIM})，得到 {mat.shape}")
    np.savez_compressed(npz, ids=np.array(ids), vectors=mat)
    if model is not None and replay_texts:
        again = model.encode(
            replay_texts,
            batch_size=len(replay_texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        again = np.asarray(again, dtype=np.float32)
        max_abs = float(np.max(np.abs(again - replay_src)))
        ok = bool(np.allclose(again, replay_src, atol=1e-5))
    else:
        max_abs = 0.0
        ok = True
    meta = {
        "model_id": MODEL_ID,
        "model_path": str(model_path),
        "dim": int(mat.shape[1]),
        "n": int(mat.shape[0]),
        "n_reused": int(len(ids) - len(need_idx)),
        "n_encoded": int(len(need_idx)),
        "title_repeat": 1,
        "npz": str(npz),
        "sha256": vec_hash(mat),
        "replay8_ok": ok,
        "replay8_max_abs": max_abs,
    }
    (THEME_DIR / "embed_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return meta


def load_embed(npz: Path | None = None) -> tuple[list[str], np.ndarray]:
    path = npz or (THEME_DIR / "embed.npz")
    data = np.load(path, allow_pickle=True)
    ids = [str(x) for x in data["ids"].tolist()]
    return ids, data["vectors"].astype(np.float32)
