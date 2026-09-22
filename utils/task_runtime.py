"""Task-scoped runtime paths and metadata.

Every research run gets an immutable task_id and stores all runtime
artifacts below runtime/tasks/<task_id>. This module deliberately keeps
path construction in one place so a caller cannot accidentally fall back
to the historical global logs/*.log files.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional
from uuid import uuid4

from loguru import logger

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_ENGINE_NAMES = {"query", "media", "insight", "report", "forum"}


def runtime_root() -> Path:
    return Path(os.environ.get("BETTAFISH_RUNTIME_DIR", "runtime/tasks"))


def validate_runtime_id(value: str, field_name: str = "task_id") -> str:
    value = (value or "").strip()
    if not _ID_RE.fullmatch(value):
        raise ValueError(
            f"{field_name} 必须由字母、数字、下划线或短横线组成，长度 1-96"
        )
    return value


def new_task_id() -> str:
    return f"task_{uuid4().hex}"


def new_client_id() -> str:
    return f"client_{uuid4().hex}"


def task_dir(task_id: str) -> Path:
    return runtime_root() / validate_runtime_id(task_id)


def task_logs_dir(task_id: str) -> Path:
    return task_dir(task_id) / "logs"


def task_log_path(task_id: str, component: str) -> Path:
    component = component.lower().strip()
    if component not in _ENGINE_NAMES:
        raise ValueError(f"未知运行组件: {component}")
    return task_logs_dir(task_id) / f"{component}.log"


def task_outputs_dir(task_id: str) -> Path:
    return task_dir(task_id) / "outputs"


def task_output_dir(task_id: str, engine: str) -> Path:
    engine = engine.lower().strip()
    if engine not in _ENGINE_NAMES:
        raise ValueError(f"未知运行组件: {engine}")
    path = task_outputs_dir(task_id) / engine
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_forum_db_path(task_id: str) -> Path:
    return task_dir(task_id) / "forum.sqlite3"


def task_metadata_path(task_id: str) -> Path:
    return task_dir(task_id) / "metadata.json"


def ensure_task(
    task_id: str,
    *,
    client_id: Optional[str] = None,
    query: Optional[str] = None,
) -> str:
    """Create task directories and merge non-empty metadata atomically."""
    task_id = validate_runtime_id(task_id)
    if client_id:
        client_id = validate_runtime_id(client_id, "client_id")

    root = task_dir(task_id)
    task_logs_dir(task_id).mkdir(parents=True, exist_ok=True)
    task_outputs_dir(task_id).mkdir(parents=True, exist_ok=True)

    metadata_path = task_metadata_path(task_id)
    metadata: Dict[str, str] = {}
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata.update({str(k): v for k, v in loaded.items()})
        except Exception:
            logger.warning(f"任务元数据损坏，将重建: {metadata_path}")

    metadata["task_id"] = task_id
    if client_id:
        metadata["client_id"] = client_id
    if query is not None and str(query).strip():
        metadata["query"] = str(query)

    tmp_path = root / f".metadata.{uuid4().hex}.tmp"
    tmp_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, metadata_path)
    return task_id


def read_task_metadata(task_id: str) -> Dict[str, str]:
    path = task_metadata_path(task_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def list_task_ids() -> list[str]:
    root = runtime_root()
    if not root.exists():
        return []
    task_ids = []
    for entry in root.iterdir():
        if entry.is_dir() and _ID_RE.fullmatch(entry.name):
            task_ids.append(entry.name)
    return task_ids


def latest_task_report(task_id: str, engine: str) -> Optional[Path]:
    directory = task_output_dir(task_id, engine)
    candidates = [p for p in directory.glob("*.md") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime_ns)


@contextmanager
def task_log_context(task_id: str, component: str) -> Iterator[None]:
    """Route loguru records from the current execution context to one task log."""
    task_id = ensure_task(task_id)
    component = component.lower().strip()
    log_path = task_log_path(task_id, component)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    sink_id = logger.add(
        str(log_path),
        level="DEBUG",
        enqueue=False,
        encoding="utf-8",
        mode="a",
        buffering=1,
        filter=lambda record: record["extra"].get("task_id") == task_id,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | "
            "{name}:{function}:{line} - {message}"
        ),
    )
    try:
        with logger.contextualize(task_id=task_id, component=component):
            yield
    finally:
        try:
            logger.remove(sink_id)
        except Exception:
            pass
