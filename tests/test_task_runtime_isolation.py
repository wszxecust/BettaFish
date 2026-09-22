from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loguru import logger

from ForumEngine.task_store import append_event, latest_host_speech, list_events, publish_agent_speech
from utils.task_runtime import (
    claim_engine_run,
    engine_run_status,
    ensure_task,
    finish_engine_run,
    latest_task_report,
    list_task_clients,
    list_task_workspaces,
    task_log_context,
    task_summary,
    task_log_path,
    task_output_dir,
    validate_runtime_id,
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



def test_task_id_rejects_path_traversal(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)
    invalid_ids = (
        "",
        "../victim",
        "task/other",
        "task\\other",
        ".hidden",
        "a" * 97,
    )
    for invalid in invalid_ids:
        with pytest.raises(ValueError):
            validate_runtime_id(invalid)


def test_concurrent_forum_writers_never_cross_task_boundary(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)
    ensure_task("task_concurrent_a", client_id="client_a")
    ensure_task("task_concurrent_b", client_id="client_b")

    def write(task_id, index):
        source = ("QUERY", "MEDIA", "INSIGHT")[index % 3]
        publish_agent_speech(
            task_id,
            source,
            f"{task_id}-event-{index}",
            trigger_host=False,
        )

    futures = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for index in range(40):
            futures.append(pool.submit(write, "task_concurrent_a", index))
            futures.append(pool.submit(write, "task_concurrent_b", index))
        for future in futures:
            future.result(timeout=15)

    events_a = list_events("task_concurrent_a", limit=100)
    events_b = list_events("task_concurrent_b", limit=100)

    assert len(events_a) == 40
    assert len(events_b) == 40
    assert all(
        str(event["content"]).startswith("task_concurrent_a-")
        for event in events_a
    )
    assert all(
        str(event["content"]).startswith("task_concurrent_b-")
        for event in events_b
    )


def test_latest_report_lookup_never_crosses_task(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)

    alpha_dir = task_output_dir("task_report_a", "query")
    beta_dir = task_output_dir("task_report_b", "query")
    alpha = alpha_dir / "alpha.md"
    beta = beta_dir / "beta.md"
    alpha.write_text("alpha report", encoding="utf-8")
    beta.write_text("beta report", encoding="utf-8")

    assert latest_task_report("task_report_a", "query") == alpha
    assert latest_task_report("task_report_b", "query") == beta


def test_engine_run_claim_is_single_owner(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)

    token, status = claim_engine_run("task_once", "query")
    assert token
    assert status == "claimed"
    assert engine_run_status("task_once", "query") == "running"

    second_token, second_status = claim_engine_run("task_once", "query")
    assert second_token is None
    assert second_status == "running"

    finish_engine_run("task_once", "query", token, success=True)
    assert engine_run_status("task_once", "query") == "completed"

    third_token, third_status = claim_engine_run("task_once", "query")
    assert third_token is None
    assert third_status == "completed"


def test_failed_engine_run_stays_failed_when_history_is_open(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)

    token, status = claim_engine_run("task_failed", "media")
    assert status == "claimed"
    finish_engine_run("task_failed", "media", token, success=False)
    assert engine_run_status("task_failed", "media") == "failed"

    second_token, second_status = claim_engine_run("task_failed", "media")
    assert second_token is None
    assert second_status == "failed"
    assert engine_run_status("task_failed", "media") == "failed"



def test_workspace_can_own_same_task_across_multiple_devices(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)
    ensure_task(
        "task_shared_devices",
        client_id="client_desktop",
        workspace_id="workspace_desktop",
        query="共享任务",
    )
    ensure_task(
        "task_shared_devices",
        client_id="client_phone",
        workspace_id="workspace_phone",
    )

    assert list_task_workspaces("task_shared_devices") == [
        "workspace_desktop",
        "workspace_phone",
    ]


def test_task_summary_status_uses_right_side_indicator_states(monkeypatch, tmp_path):
    _use_tmp_runtime(monkeypatch, tmp_path)
    task_id = "task_status"
    tokens = {}
    for engine in ("insight", "media", "query"):
        token, status = claim_engine_run(task_id, engine)
        assert status == "claimed"
        tokens[engine] = token

    assert task_summary(task_id)["status"] == "running"

    finish_engine_run(task_id, "insight", tokens["insight"], success=True)
    finish_engine_run(task_id, "media", tokens["media"], success=True)
    finish_engine_run(task_id, "query", tokens["query"], success=True)
    assert task_summary(task_id)["status"] == "completed"

    retry_task = "task_failed_status"
    token, _ = claim_engine_run(retry_task, "query")
    finish_engine_run(retry_task, "query", token, success=False)
    assert task_summary(retry_task)["status"] == "failed"
