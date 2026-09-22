import atexit
import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

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
    monkeypatch.setenv("BETTAFISH_RUNTIME_DIR", str(tmp_path / "runtime" / "tasks"))
    monkeypatch.chdir(tmp_path)

    source_path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("bettafish_app_under_test", source_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module

    with patch.object(threading, "Thread", DummyThread), patch.object(
        atexit, "register", lambda *args, **kwargs: None
    ):
        spec.loader.exec_module(module)

    try:
        yield module, module.app.test_client()
    finally:
        _remove_modules(prefixes)
        sys.modules.update(saved_modules)


def test_search_propagates_task_identity_to_searchable_engine(root_app, monkeypatch):
    module, client = root_app
    module.processes = {
        "insight": {"status": "running"},
        "forum": {"status": "running"},
    }
    monkeypatch.setattr(module, "check_app_status", lambda: None)
    engine_response = Mock(status_code=200)
    engine_response.json.return_value = {"success": True, "items": ["result"]}
    post = Mock(return_value=engine_response)
    monkeypatch.setattr(module.requests, "post", post)

    response = client.post(
        "/api/search",
        json={
            "query": "BettaFish",
            "task_id": "task_alpha",
            "client_id": "client_tab_a",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "query": "BettaFish",
        "task_id": "task_alpha",
        "client_id": "client_tab_a",
        "results": {"insight": {"success": True, "items": ["result"]}},
    }
    post.assert_called_once_with(
        "http://localhost:8501/api/search",
        json={
            "query": "BettaFish",
            "task_id": "task_alpha",
            "client_id": "client_tab_a",
        },
        timeout=10,
    )


def test_search_with_only_forum_keeps_task_but_calls_no_engine(root_app, monkeypatch):
    module, client = root_app
    module.processes = {"forum": {"status": "running"}}
    monkeypatch.setattr(module, "check_app_status", lambda: None)
    post = Mock()
    monkeypatch.setattr(module.requests, "post", post)

    response = client.post(
        "/api/search",
        json={
            "query": "BettaFish",
            "task_id": "task_forum_only",
            "client_id": "client_tab_b",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "query": "BettaFish",
        "task_id": "task_forum_only",
        "client_id": "client_tab_b",
        "results": {},
    }
    post.assert_not_called()


def test_search_rejects_path_traversal_task_id(root_app, monkeypatch):
    module, client = root_app
    module.processes = {"insight": {"status": "running"}}
    monkeypatch.setattr(module, "check_app_status", lambda: None)

    response = client.post(
        "/api/search",
        json={
            "query": "BettaFish",
            "task_id": "../another-user",
            "client_id": "client_tab_c",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_forum_api_never_returns_another_tasks_events(root_app):
    module, client = root_app
    from ForumEngine.task_store import append_event

    module.ensure_task("task_a", client_id="client_a", query="alpha")
    module.ensure_task("task_b", client_id="client_b", query="beta")
    append_event("task_a", "QUERY", "alpha-only evidence")
    append_event("task_b", "MEDIA", "beta-only evidence")
    append_event("task_a", "HOST", "alpha-only host")

    response_a = client.get("/api/forum/log?task_id=task_a")
    response_b = client.get("/api/forum/log?task_id=task_b")

    assert response_a.status_code == 200
    assert response_b.status_code == 200

    body_a = response_a.get_json()
    body_b = response_b.get_json()
    content_a = "\n".join(body_a["log_lines"])
    content_b = "\n".join(body_b["log_lines"])

    assert "alpha-only evidence" in content_a
    assert "alpha-only host" in content_a
    assert "beta-only evidence" not in content_a

    assert "beta-only evidence" in content_b
    assert "alpha-only evidence" not in content_b
    assert "alpha-only host" not in content_b
