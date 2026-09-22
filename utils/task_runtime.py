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
import time
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


def task_clients_dir(task_id: str) -> Path:
    return task_dir(task_id) / "clients"


def task_runs_dir(task_id: str) -> Path:
    return task_dir(task_id) / "runs"


def _engine_run_paths(task_id: str, engine: str) -> tuple[Path, Path]:
    engine = engine.lower().strip()
    if engine not in _ENGINE_NAMES:
        raise ValueError(f"未知运行组件: {engine}")
    directory = task_runs_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{engine}.lock", directory / f"{engine}.done"


def claim_engine_run(
    task_id: str,
    engine: str,
    *,
    stale_after_seconds: float = 12 * 60 * 60,
) -> tuple[Optional[str], str]:
    """Atomically claim one engine execution for a research task.

    Returns (token, "claimed") for the single caller allowed to execute,
    otherwise (None, "running") or (None, "completed"). This makes
    opening the same task from a second browser/device a subscription action
    instead of accidentally launching duplicate research.
    """
    task_id = ensure_task(task_id)
    lock_path, done_path = _engine_run_paths(task_id, engine)
    if done_path.exists():
        return None, "completed"

    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            age = 0
        if age <= stale_after_seconds:
            return None, "running"
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return None, "running"

    token = uuid4().hex
    try:
        fd = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        return None, "running"

    try:
        payload = {
            "token": token,
            "pid": os.getpid(),
            "started_at": time.time(),
        }
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)

    # Close the tiny race where another worker completed between our first
    # done-check and acquiring a lock after that worker removed its own lock.
    if done_path.exists():
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        return None, "completed"

    return token, "claimed"


def finish_engine_run(
    task_id: str,
    engine: str,
    token: str,
    *,
    success: bool,
) -> None:
    """Release a claimed engine run and persist completion only on success."""
    task_id = validate_runtime_id(task_id)
    lock_path, done_path = _engine_run_paths(task_id, engine)
    try:
        current = json.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if current.get("token") != token:
        return

    if success:
        tmp = done_path.with_name(f".{done_path.name}.{uuid4().hex}.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "engine": engine,
                    "completed_at": time.time(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, done_path)

    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def engine_run_status(task_id: str, engine: str) -> str:
    lock_path, done_path = _engine_run_paths(task_id, engine)
    if done_path.exists():
        return "completed"
    if lock_path.exists():
        return "running"
    return "idle"

def register_task_client(task_id: str, client_id: str) -> str:
    task_id = validate_runtime_id(task_id)
    client_id = validate_runtime_id(client_id, "client_id")
    directory = task_clients_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    # Marker files avoid a shared mutable client list and are safe when
    # different Streamlit worker processes register the same task concurrently.
    (directory / client_id).touch(exist_ok=True)
    return client_id


def list_task_clients(task_id: str) -> list[str]:
    directory = task_clients_dir(task_id)
    if not directory.exists():
        return []
    result = []
    for entry in directory.iterdir():
        if entry.is_file() and _ID_RE.fullmatch(entry.name):
            result.append(entry.name)
    return sorted(set(result))


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
    task_clients_dir(task_id).mkdir(parents=True, exist_ok=True)
    task_runs_dir(task_id).mkdir(parents=True, exist_ok=True)
    if client_id:
        register_task_client(task_id, client_id)

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
