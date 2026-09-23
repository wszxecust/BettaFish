"""Task-scoped Forum reader helpers.

Forum coordination is no longer read from the process-global logs/forum.log.
Every call must identify the research task whose HOST guidance is allowed to
enter the current agent's reasoning context.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from ForumEngine.task_store import (
    all_host_speeches,
    latest_host_speech,
    recent_agent_speeches,
)
from utils.task_runtime import validate_runtime_id


def get_latest_host_speech(task_id: str) -> Optional[str]:
    """Return only the newest HOST speech for one research task."""
    try:
        task_id = validate_runtime_id(task_id)
        speech = latest_host_speech(task_id)
        if speech:
            logger.info(f"task={task_id} 找到最新HOST发言，长度: {len(speech)}字符")
        else:
            logger.debug(f"task={task_id} 未找到HOST发言")
        return speech
    except Exception as exc:
        logger.error(f"读取task={task_id} HOST发言失败: {exc}")
        return None


def get_all_host_speeches(task_id: str) -> List[Dict[str, str]]:
    """Return all HOST speeches belonging to one task."""
    try:
        task_id = validate_runtime_id(task_id)
        events = all_host_speeches(task_id)
        return [
            {
                "timestamp": str(event.get("created_at", "")),
                "content": str(event.get("content", "")),
            }
            for event in events
        ]
    except Exception as exc:
        logger.error(f"读取task={task_id} HOST历史失败: {exc}")
        return []


def get_recent_agent_speeches(task_id: str, limit: int = 5) -> List[Dict[str, str]]:
    """Return recent Query/Media/Insight speeches for one task only."""
    try:
        task_id = validate_runtime_id(task_id)
        events = recent_agent_speeches(task_id, limit=limit)
        return [
            {
                "timestamp": str(event.get("created_at", "")),
                "agent": str(event.get("source", "")),
                "content": str(event.get("content", "")),
            }
            for event in events
        ]
    except Exception as exc:
        logger.error(f"读取task={task_id} Agent发言失败: {exc}")
        return []


def format_host_speech_for_prompt(host_speech: str) -> str:
    """Format HOST guidance for insertion into an agent prompt."""
    if not host_speech:
        return ""

    return f"""
### 论坛主持人最新总结
以下是论坛主持人对各Agent讨论的最新总结和引导，请参考其中的观点和建议：

{host_speech}

---
"""
