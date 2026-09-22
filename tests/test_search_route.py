import atexit
import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from flask import Blueprint


class DummyThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class DummySocketIO:
    def __init__(self, *args, **kwargs):
        pass

    def emit(self, *args, **kwargs):
        pass

    def on(self, _event):
        return lambda function: function

    def run(self, *args, **kwargs):
        pass

    def stop(self):
        pass


def _remove_modules(prefixes):
    saved = {
        name: module
        for name, module in list(sys.modules.items())
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
    }
    for name in saved:
        sys.modules.pop(name, None)
    return saved


@pytest.fixture
def root_app(monkeypatch, tmp_path):
    prefixes = ("bettafish_app_under_test", "MindSpider", "ReportEngine", "flask_socketio")
    saved_modules = _remove_modules(prefixes)

    mindspider_package = ModuleType("MindSpider")
    mindspider_package.__path__ = []
    mindspider_main = ModuleType("MindSpider.main")
    mindspider_main.MindSpider = type("MindSpider", (), {})

    report_package = ModuleType("ReportEngine")
    report_package.__path__ = []
    report_interface = ModuleType("ReportEngine.flask_interface")
    report_interface.report_bp = Blueprint("stub_report", __name__)
    report_interface.initialize_report_engine = lambda: True

    socketio_module = ModuleType("flask_socketio")
    socketio_module.SocketIO = DummySocketIO
    socketio_module.emit = lambda *args, **kwargs: None
    socketio_module.join_room = lambda *args, **kwargs: None

    monkeypatch.setitem(sys.modules, "MindSpider", mindspider_package)
    monkeypatch.setitem(sys.modules, "MindSpider.main", mindspider_main)
    monkeypatch.setitem(sys.modules, "ReportEngine", report_package)
    monkeypatch.setitem(sys.modules, "ReportEngine.flask_interface", report_interface)
    monkeypatch.setitem(sys.modules, "flask_socketio", socketio_module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BETTAFISH_RUNTIME_DIR", str(tmp_path / "runtime" / "tasks"))

    source_path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("bettafish_app_under_test", source_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module

    with patch.object(threading, "Thread", DummyThread), patch.object(
        atexit, "register", lambda *args, **kwargs: None
    ):
        spec.loader.exec_module(module)

    try:
        yield module, module.app.test_client(), tmp_path
    finally:
        _remove_modules(prefixes)
        sys.modules.update(saved_modules)


def test_search_returns_task_scoped_streamlit_launch_url(root_app, monkeypatch):
    module, client, tmp_path = root_app
    module.processes = {
        "insight": {"status": "running", "port": 8501},
        "media": {"status": "stopped", "port": 8502},
        "query": {"status": "stopped", "port": 8503},
        "forum": {"status": "running", "port": None},
    }
    monkeypatch.setattr(module, "check_app_status", lambda: None)
    post = Mock()
    monkeypatch.setattr(module.requests, "post", post)

    response = client.post(
        "/api/search",
        json={
            "query": "BettaFish 并发隔离",
            "task_id": "task_test_1",
            "client_id": "client_test_1",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["task_id"] == "task_test_1"
    assert payload["client_id"] == "client_test_1"
    assert set(payload["launch_urls"]) == {"insight"}

    parsed = urlparse(payload["launch_urls"]["insight"])
    assert parsed.port == 8501
    params = parse_qs(parsed.query)
    assert params["query"] == ["BettaFish 并发隔离"]
    assert params["auto_search"] == ["true"]
    assert params["task_id"] == ["task_test_1"]
    assert params["client_id"] == ["client_test_1"]

    # Streamlit没有Flask式/api/search，主应用不得再发送伪API POST。
    post.assert_not_called()
    assert (tmp_path / "runtime" / "tasks" / "task_test_1").is_dir()


def test_search_with_only_forum_returns_no_engine_error(root_app, monkeypatch):
    module, client, _ = root_app
    module.processes = {"forum": {"status": "running", "port": None}}
    monkeypatch.setattr(module, "check_app_status", lambda: None)

    response = client.post(
        "/api/search",
        json={
            "query": "BettaFish",
            "task_id": "task_forum_only",
            "client_id": "client_forum_only",
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["task_id"] == "task_forum_only"
    assert payload["results"] == {}


def test_output_requires_task_id(root_app):
    module, client, _ = root_app
    module.processes = {"query": {"status": "running", "port": 8503}}

    response = client.get("/api/output/query")

    assert response.status_code == 400
    assert response.get_json()["message"] == "缺少task_id"


def test_task_output_endpoint_never_reads_other_task(root_app):
    module, client, _ = root_app
    module.processes = {"query": {"status": "running", "port": 8503}}
    module.ensure_task("task_a", client_id="client_a", query="A")
    module.ensure_task("task_b", client_id="client_b", query="B")
    module.write_log_to_file("query", "A-only", task_id="task_a")
    module.write_log_to_file("query", "B-only", task_id="task_b")

    response = client.get("/api/output/query?task_id=task_a")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["output"] == ["A-only"]
    assert "B-only" not in payload["output"]
