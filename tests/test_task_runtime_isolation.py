from concurrent.futures import ThreadPoolExecutor

import pytest

from ForumEngine.task_store import (
    append_event,
    latest_host_speech,
    list_events,
    publish_agent_speech,
    render_forum_log,
)
from utils.task_runtime import (
    ensure_task,
    latest_task_report,
    read_task_metadata,
    task_log_path,
    task_output_dir,
    validate_runtime_id,
)


@pytest.fixture
def isolated_runtime(monkeypatch, tmp_path):
    root = tmp_path / "runtime" / "tasks"
    monkeypatch.setenv("BETTAFISH_RUNTIME_DIR", str(root))
    return root


def test_two_tasks_have_disjoint_paths_metadata_and_forum(isolated_runtime):
    ensure_task("task_alpha", client_id="client_alpha", query="alpha question")
    ensure_task("task_beta", client_id="client_beta", query="beta question")

    assert task_log_path("task_alpha", "query") != task_log_path("task_beta", "query")
    assert task_output_dir("task_alpha", "query") != task_output_dir("task_beta", "query")

    meta_a = read_task_metadata("task_alpha")
    meta_b = read_task_metadata("task_beta")
    assert meta_a["client_id"] == "client_alpha"
    assert meta_a["query"] == "alpha question"
    assert meta_b["client_id"] == "client_beta"
    assert meta_b["query"] == "beta question"

    append_event("task_alpha", "QUERY", "alpha evidence")
    append_event("task_alpha", "HOST", "alpha host")
    append_event("task_beta", "MEDIA", "beta evidence")
    append_event("task_beta", "HOST", "beta host")

    events_a = list_events("task_alpha")
    events_b = list_events("task_beta")

    assert [event["content"] for event in events_a] == ["alpha evidence", "alpha host"]
    assert [event["content"] for event in events_b] == ["beta evidence", "beta host"]
    assert latest_host_speech("task_alpha") == "alpha host"
    assert latest_host_speech("task_beta") == "beta host"

    log_a = render_forum_log("task_alpha")
    log_b = render_forum_log("task_beta")
    assert "alpha evidence" in log_a and "beta evidence" not in log_a
    assert "beta evidence" in log_b and "alpha evidence" not in log_b


def test_task_id_rejects_path_traversal(isolated_runtime):
    for invalid in ("", "../victim", "task/other", "task\\other", ".hidden", "a" * 97):
        with pytest.raises(ValueError):
            validate_runtime_id(invalid)


def test_concurrent_writers_keep_every_event_inside_its_task(isolated_runtime):
    ensure_task("task_alpha", client_id="client_alpha")
    ensure_task("task_beta", client_id="client_beta")

    def write(task_id, index):
        source = ("QUERY", "MEDIA", "INSIGHT")[index % 3]
        publish_agent_speech(
            task_id,
            source,
            f"{task_id}-event-{index}",
            trigger_host=False,
        )

    jobs = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for i in range(40):
            jobs.append(pool.submit(write, "task_alpha", i))
            jobs.append(pool.submit(write, "task_beta", i))
        for job in jobs:
            job.result(timeout=10)

    events_a = list_events("task_alpha", limit=100)
    events_b = list_events("task_beta", limit=100)

    assert len(events_a) == 40
    assert len(events_b) == 40
    assert all(str(event["content"]).startswith("task_alpha-") for event in events_a)
    assert all(str(event["content"]).startswith("task_beta-") for event in events_b)


def test_latest_report_lookup_cannot_cross_task_boundary(isolated_runtime):
    alpha_dir = task_output_dir("task_alpha", "query")
    beta_dir = task_output_dir("task_beta", "query")

    alpha = alpha_dir / "alpha.md"
    beta = beta_dir / "beta.md"
    alpha.write_text("alpha report", encoding="utf-8")
    beta.write_text("beta report", encoding="utf-8")

    assert latest_task_report("task_alpha", "query") == alpha
    assert latest_task_report("task_beta", "query") == beta
