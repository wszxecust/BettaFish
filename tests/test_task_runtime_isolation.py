from pathlib import Path

from loguru import logger

from ForumEngine.task_store import append_event, latest_host_speech, list_events
from utils.task_runtime import (
    ensure_task,
    list_task_clients,
    task_log_context,
    task_log_path,
    task_output_dir,
)


def _use_tmp_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("BETTAFISH_RUNTIME_DIR", str(tmp_path / "runtime" / "tasks"))


def test_task_paths_are_physically_isolated(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)
    ensure_task("task_a", client_id="client_a", query="A")
    ensure_task("task_b", client_id="client_b", query="B")

    a_log = task_log_path("task_a", "query")
    b_log = task_log_path("task_b", "query")
    assert a_log != b_log
    assert task_output_dir("task_a", "query") != task_output_dir("task_b", "query")

    a_log.parent.mkdir(parents=True, exist_ok=True)
    b_log.parent.mkdir(parents=True, exist_ok=True)
    a_log.write_text("only-a", encoding="utf-8")
    b_log.write_text("only-b", encoding="utf-8")

    assert a_log.read_text(encoding="utf-8") == "only-a"
    assert b_log.read_text(encoding="utf-8") == "only-b"


def test_same_task_can_have_multiple_clients(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)
    ensure_task("task_shared", client_id="client_one", query="shared")
    ensure_task("task_shared", client_id="client_two", query="shared")

    assert list_task_clients("task_shared") == ["client_one", "client_two"]


def test_forum_events_never_cross_task_boundary(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)
    append_event("task_a", "HOST", "host-a")
    append_event("task_b", "HOST", "host-b")
    append_event("task_a", "QUERY", "query-a")

    assert latest_host_speech("task_a") == "host-a"
    assert latest_host_speech("task_b") == "host-b"

    a_contents = [event["content"] for event in list_events("task_a")]
    b_contents = [event["content"] for event in list_events("task_b")]
    assert a_contents == ["host-a", "query-a"]
    assert b_contents == ["host-b"]


def test_loguru_task_context_filters_concurrent_sinks(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)

    with task_log_context("task_a", "query"):
        logger.info("message-a")
    with task_log_context("task_b", "query"):
        logger.info("message-b")

    a_text = task_log_path("task_a", "query").read_text(encoding="utf-8")
    b_text = task_log_path("task_b", "query").read_text(encoding="utf-8")
    assert "message-a" in a_text
    assert "message-b" not in a_text
    assert "message-b" in b_text
    assert "message-a" not in b_text
