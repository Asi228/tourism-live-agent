"""只读作品演示使用的轻量文件向量库。

企业/本地环境继续使用 Milvus；这里仅服务 Render 免费实例上的固定演示数据。
数据写入 JSON 文件，因此启动脚本的播种子进程退出后，API 进程仍能读取向量。
"""

from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

from app.config import settings


_LOCK = threading.RLock()


def _path() -> Path:
    return Path(settings.FILE_VECTOR_PATH)


def _read() -> dict[int, dict]:
    path = _path()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        rows = json.load(handle)
    return {int(row["chunk_id"]): row for row in rows}


def _write(rows: dict[int, dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(list(rows.values()), handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temp, path)


def probe(_timeout_seconds: float) -> None:
    with _LOCK:
        _read()


def health(collection: str) -> dict:
    with _LOCK:
        exists = _path().exists()
    return {
        "server_version": "portfolio-file-v1",
        "collection": collection,
        "collection_exists": exists,
        "collections": [collection] if exists else [],
    }


def ensure_collection() -> None:
    with _LOCK:
        if not _path().exists():
            _write({})


def ensure_loaded() -> bool:
    ensure_collection()
    return True


def flush() -> bool:
    return True


def upsert(rows) -> int:
    with _LOCK:
        stored = _read()
        for row in rows:
            stored[int(row.chunk_id)] = {
                "chunk_id": int(row.chunk_id),
                "document_id": int(row.document_id),
                "knowledge_base_id": int(row.knowledge_base_id),
                "page_number": row.page_number,
                "vector": [float(value) for value in row.vector],
            }
        _write(stored)
    return len(rows)


def _delete(predicate) -> int:
    with _LOCK:
        stored = _read()
        kept = {key: row for key, row in stored.items() if not predicate(row)}
        deleted = len(stored) - len(kept)
        _write(kept)
        return deleted


def delete_by_document(document_id: int) -> int:
    return _delete(lambda row: int(row["document_id"]) == int(document_id))


def delete_by_kb(knowledge_base_id: int) -> int:
    return _delete(
        lambda row: int(row["knowledge_base_id"]) == int(knowledge_base_id)
    )


def count(kb_ids: list[int] | None = None) -> int:
    with _LOCK:
        rows = _read().values()
        if kb_ids is None:
            return len(list(rows))
        allowed = {int(value) for value in kb_ids}
        return sum(int(row["knowledge_base_id"]) in allowed for row in rows)


def list_kb_ids() -> list[int]:
    with _LOCK:
        return sorted({int(row["knowledge_base_id"]) for row in _read().values()})


def list_document_ids() -> list[int]:
    with _LOCK:
        return sorted({int(row["document_id"]) for row in _read().values()})


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def search(vector: list[float], kb_ids: list[int] | None, top_k: int) -> list[dict]:
    allowed = None if kb_ids is None else {int(value) for value in kb_ids}
    if allowed == set():
        return []
    with _LOCK:
        candidates = [
            row
            for row in _read().values()
            if allowed is None or int(row["knowledge_base_id"]) in allowed
        ]
    ranked = sorted(
        candidates,
        key=lambda row: _cosine(vector, row["vector"]),
        reverse=True,
    )[:top_k]
    return [
        {
            "chunk_id": int(row["chunk_id"]),
            "document_id": int(row["document_id"]),
            "knowledge_base_id": int(row["knowledge_base_id"]),
            "page_number": row.get("page_number"),
            "score": _cosine(vector, row["vector"]),
        }
        for row in ranked
    ]
