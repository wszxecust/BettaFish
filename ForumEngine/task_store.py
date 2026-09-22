"""Task-scoped forum event store.

The historical ForumEngine inferred conversations by tailing three global log
files and appending everything into one logs/forum.log. Locks cannot make
that logical stream safe for independent concurrent research tasks.

Each task now owns one SQLite event store. SQLite gives the three engine
processes transactional writes while keeping tasks physically isolated.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

from loguru import logger

from utils.task_runtime import ensure_task, task_forum_db_path, validate_runtime_id

AGENT_SOURCES = ("QUERY", "MEDIA", "INSIGHT")
VALID_SOURCES = set(AGENT_SOURCES) | {"HOST", "SYSTEM"}
DEFAULT_HOST_THRESHOLD = 5
HOST_LEASE_SECONDS = 300.0


def _connect(task_id: str) -> sqlite3.Connection:
    task_id = validate_runtime_id(task_id)
    ensure_task(task_id)
    path = task_forum_db_path(task_id)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coordination (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    return conn


def initialize_task_forum(task_id: str) -> None:
    conn = _connect(task_id)
    conn.close()


def _normalize_source(source: str) -> str:
    source = (source or "").strip().upper()
    if source not in VALID_SOURCES:
        raise ValueError(f"未知 Forum 来源: {source}")
    return source


def append_event(task_id: str, source: str, content: str) -> Dict[str, object]:
    task_id = validate_runtime_id(task_id)
    source = _normalize_source(source)
    content = str(content or "").strip()
    if not content:
        raise ValueError("Forum 事件内容不能为空")
    created_at = datetime.now(timezone.utc).isoformat()

    conn = _connect(task_id)
    try:
        cursor = conn.execute(
            "INSERT INTO events(source, content, created_at) VALUES (?, ?, ?)",
            (source, content, created_at),
        )
        event_id = int(cursor.lastrowid)
    finally:
        conn.close()
    return {
        "id": event_id,
        "task_id": task_id,
        "source": source,
        "content": content,
        "created_at": created_at,
    }


def list_events(
    task_id: str,
    *,
    after_id: int = 0,
    limit: int = 1000,
    sources: Optional[Sequence[str]] = None,
) -> List[Dict[str, object]]:
    task_id = validate_runtime_id(task_id)
    limit = max(1, min(int(limit), 5000))
    params: list[object] = [max(0, int(after_id))]
    sql = "SELECT id, source, content, created_at FROM events WHERE id > ?"
    if sources:
        normalized = [_normalize_source(source) for source in sources]
        placeholders = ",".join("?" for _ in normalized)
        sql += f" AND source IN ({placeholders})"
        params.extend(normalized)
    sql += " ORDER BY id ASC LIMIT ?"
    params.append(limit)

    conn = _connect(task_id)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "task_id": task_id,
            "source": row["source"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def latest_host_speech(task_id: str) -> Optional[str]:
    conn = _connect(task_id)
    try:
        row = conn.execute(
            "SELECT content FROM events WHERE source='HOST' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return str(row["content"]) if row else None


def all_host_speeches(task_id: str) -> List[Dict[str, object]]:
    return list_events(task_id, sources=["HOST"], limit=5000)


def recent_agent_speeches(task_id: str, limit: int = 5) -> List[Dict[str, object]]:
    conn = _connect(task_id)
    try:
        rows = conn.execute(
            """
            SELECT id, source, content, created_at
            FROM events
            WHERE source IN ('QUERY', 'MEDIA', 'INSIGHT')
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "task_id": task_id,
            "source": row["source"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]


def render_forum_log(task_id: str) -> str:
    lines = []
    for event in list_events(task_id, limit=5000):
        try:
            dt = datetime.fromisoformat(str(event["created_at"]))
            timestamp = dt.astimezone().strftime("%H:%M:%S")
        except Exception:
            timestamp = "--:--:--"
        content = str(event["content"]).replace("\n", "\\n").replace("\r", "\\r")
        lines.append(f"[{timestamp}] [{event['source']}] {content}")
    return "\n".join(lines) + ("\n" if lines else "")


def _get_coord(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM coordination WHERE key=?", (key,)
    ).fetchone()
    return str(row["value"]) if row else default


def _set_coord(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        """
        INSERT INTO coordination(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )


def _claim_host_batch(
    task_id: str,
    threshold: int = DEFAULT_HOST_THRESHOLD,
) -> Optional[Dict[str, object]]:
    """Claim the next host batch transactionally across engine processes."""
    conn = _connect(task_id)
    token = uuid4().hex
    now = time.time()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor_id = int(_get_coord(conn, "host_cursor", "0") or 0)
        lease_until = float(_get_coord(conn, "host_lease_until", "0") or 0)
        if lease_until > now:
            conn.execute("COMMIT")
            return None

        rows = conn.execute(
            """
            SELECT id, source, content, created_at
            FROM events
            WHERE id > ?
              AND source IN ('QUERY', 'MEDIA', 'INSIGHT')
            ORDER BY id ASC
            LIMIT ?
            """,
            (cursor_id, int(threshold)),
        ).fetchall()
        if len(rows) < threshold:
            _set_coord(conn, "host_lease_token", "")
            _set_coord(conn, "host_lease_until", "0")
            conn.execute("COMMIT")
            return None

        end_id = int(rows[-1]["id"])
        _set_coord(conn, "host_lease_token", token)
        _set_coord(conn, "host_lease_until", now + HOST_LEASE_SECONDS)
        _set_coord(conn, "host_lease_end_id", end_id)
        conn.execute("COMMIT")
        return {
            "token": token,
            "end_id": end_id,
            "events": [dict(row) for row in rows],
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _finish_host_batch(
    task_id: str,
    token: str,
    end_id: int,
    host_content: str,
) -> Optional[Dict[str, object]]:
    conn = _connect(task_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _get_coord(conn, "host_lease_token", "") != token:
            conn.execute("ROLLBACK")
            return None
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO events(source, content, created_at) VALUES ('HOST', ?, ?)",
            (host_content.strip(), created_at),
        )
        event_id = int(cursor.lastrowid)
        _set_coord(conn, "host_cursor", int(end_id))
        _set_coord(conn, "host_lease_token", "")
        _set_coord(conn, "host_lease_until", "0")
        _set_coord(conn, "host_lease_end_id", "0")
        conn.execute("COMMIT")
        return {
            "id": event_id,
            "task_id": task_id,
            "source": "HOST",
            "content": host_content.strip(),
            "created_at": created_at,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _release_host_claim(task_id: str, token: str) -> None:
    conn = _connect(task_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _get_coord(conn, "host_lease_token", "") == token:
            _set_coord(conn, "host_lease_token", "")
            _set_coord(conn, "host_lease_until", "0")
            _set_coord(conn, "host_lease_end_id", "0")
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
    finally:
        conn.close()


def _generate_host_if_ready(task_id: str) -> None:
    claim = _claim_host_batch(task_id)
    if not claim:
        return
    token = str(claim["token"])
    try:
        from .llm_host import generate_host_speech

        speeches = []
        for event in claim["events"]:
            created_at = str(event.get("created_at", ""))
            try:
                timestamp = datetime.fromisoformat(created_at).astimezone().strftime("%H:%M:%S")
            except Exception:
                timestamp = "--:--:--"
            content = str(event["content"]).replace("\n", "\\n").replace("\r", "\\r")
            speeches.append(f"[{timestamp}] [{event['source']}] {content}")

        host_speech = generate_host_speech(speeches)
        if host_speech:
            _finish_host_batch(
                task_id,
                token,
                int(claim["end_id"]),
                str(host_speech),
            )
        else:
            _release_host_claim(task_id, token)
    except Exception:
        logger.exception(f"ForumEngine: task={task_id} 主持人生成失败")
        _release_host_claim(task_id, token)


def publish_agent_speech(
    task_id: str,
    source: str,
    content: str,
    *,
    trigger_host: bool = True,
) -> Dict[str, object]:
    source = _normalize_source(source)
    if source not in AGENT_SOURCES:
        raise ValueError("publish_agent_speech 只接受 QUERY/MEDIA/INSIGHT")
    event = append_event(task_id, source, content)
    if trigger_host:
        thread = threading.Thread(
            target=_generate_host_if_ready,
            args=(task_id,),
            daemon=True,
            name=f"forum-host-{task_id[:12]}",
        )
        thread.start()
    return event
