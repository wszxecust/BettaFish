"""
Flask主应用 - 统一管理三个Streamlit应用
"""

import os
import sys

# 【修复】尽早设置环境变量，确保所有模块都使用无缓冲模式
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONUNBUFFERED'] = '1'  # 禁用Python输出缓冲，确保日志实时输出

import subprocess
import time
import threading
from datetime import datetime
from queue import Queue
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit, join_room
import atexit
import requests
from loguru import logger
import importlib
from pathlib import Path
from MindSpider.main import MindSpider
from ForumEngine.task_store import initialize_task_forum, list_events, render_forum_log
from utils.task_runtime import (
    ensure_task,
    list_task_ids,
    new_client_id,
    new_task_id,
    read_task_metadata,
    task_log_path,
    validate_runtime_id,
)

# 导入ReportEngine
try:
    from ReportEngine.flask_interface import report_bp, initialize_report_engine
    REPORT_ENGINE_AVAILABLE = True
except ImportError as e:
    logger.error(f"ReportEngine导入失败: {e}")
    REPORT_ENGINE_AVAILABLE = False

app = Flask(__name__)
app.config['SECRET_KEY'] = 'Dedicated-to-creating-a-concise-and-versatile-public-opinion-analysis-platform'
socketio = SocketIO(app, cors_allowed_origins="*")

# eventlet 在客户端主动断开时偶尔会抛出 ConnectionAbortedError，这里做一次防御性包裹，
# 避免无意义的堆栈污染日志（仅在 eventlet 可用时启用）。
def _patch_eventlet_disconnect_logging():
    try:
        import eventlet.wsgi  # type: ignore
    except Exception as exc:  # pragma: no cover - 仅在生产环境有效
        logger.debug(f"eventlet 不可用，跳过断开补丁: {exc}")
        return

    try:
        original_finish = eventlet.wsgi.HttpProtocol.finish  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover
        logger.debug(f"eventlet 缺少 HttpProtocol.finish，跳过断开补丁: {exc}")
        return

    def _safe_finish(self, *args, **kwargs):  # pragma: no cover - 运行时才会触发
        try:
            return original_finish(self, *args, **kwargs)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            try:
                environ = getattr(self, 'environ', {}) or {}
                method = environ.get('REQUEST_METHOD', '')
                path = environ.get('PATH_INFO', '')
                logger.warning(f"客户端已主动断开，忽略异常: {method} {path} ({exc})")
            except Exception:
                logger.warning(f"客户端已主动断开，忽略异常: {exc}")
            return

    eventlet.wsgi.HttpProtocol.finish = _safe_finish  # type: ignore[attr-defined]
    logger.info("已对 eventlet 连接中断进行安全防护")

_patch_eventlet_disconnect_logging()

# 注册ReportEngine Blueprint
if REPORT_ENGINE_AVAILABLE:
    app.register_blueprint(report_bp, url_prefix='/api/report')
    logger.info("ReportEngine接口已注册")
else:
    logger.info("ReportEngine不可用，跳过接口注册")

# 创建日志目录
LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)

CONFIG_MODULE_NAME = 'config'
CONFIG_FILE_PATH = Path(__file__).resolve().parent / 'config.py'
CONFIG_KEYS = [
    'HOST',
    'PORT',
    'DB_DIALECT',
    'DB_HOST',
    'DB_PORT',
    'DB_USER',
    'DB_PASSWORD',
    'DB_NAME',
    'DB_CHARSET',
    'INSIGHT_ENGINE_API_KEY',
    'INSIGHT_ENGINE_BASE_URL',
    'INSIGHT_ENGINE_MODEL_NAME',
    'MEDIA_ENGINE_API_KEY',
    'MEDIA_ENGINE_BASE_URL',
    'MEDIA_ENGINE_MODEL_NAME',
    'QUERY_ENGINE_API_KEY',
    'QUERY_ENGINE_BASE_URL',
    'QUERY_ENGINE_MODEL_NAME',
    'REPORT_ENGINE_API_KEY',
    'REPORT_ENGINE_BASE_URL',
    'REPORT_ENGINE_MODEL_NAME',
    'FORUM_HOST_API_KEY',
    'FORUM_HOST_BASE_URL',
    'FORUM_HOST_MODEL_NAME',
    'KEYWORD_OPTIMIZER_API_KEY',
    'KEYWORD_OPTIMIZER_BASE_URL',
    'KEYWORD_OPTIMIZER_MODEL_NAME',
    'TAVILY_API_KEY',
    'SEARCH_TOOL_TYPE',
    'BOCHA_WEB_SEARCH_API_KEY',
    'ANSPIRE_API_KEY'
]


def _load_config_module():
    """Load or reload the config module to ensure latest values are available."""
    importlib.invalidate_caches()
    module = sys.modules.get(CONFIG_MODULE_NAME)
    try:
        if module is None:
            module = importlib.import_module(CONFIG_MODULE_NAME)
        else:
            module = importlib.reload(module)
    except ModuleNotFoundError:
        return None
    return module


def read_config_values():
    """Return the current configuration values that are exposed to the frontend."""
    try:
        # 重新加载配置以获取最新的 Settings 实例
        from config import reload_settings, settings
        reload_settings()
        
        values = {}
        for key in CONFIG_KEYS:
            # 从 Pydantic Settings 实例读取值
            value = getattr(settings, key, None)
            # Convert to string for uniform handling on the frontend.
            if value is None:
                values[key] = ''
            else:
                values[key] = str(value)
        return values
    except Exception as exc:
        logger.exception(f"读取配置失败: {exc}")
        return {}


def _serialize_config_value(value):
    """Serialize Python values back to a config.py assignment-friendly string."""
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return 'None'

    value_str = str(value)
    escaped = value_str.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def write_config_values(updates):
    """Persist configuration updates to .env file (Pydantic Settings source)."""
    from pathlib import Path
    
    # 确定 .env 文件路径（与 config.py 中的逻辑一致）
    project_root = Path(__file__).resolve().parent
    cwd_env = Path.cwd() / ".env"
    env_file_path = cwd_env if cwd_env.exists() else (project_root / ".env")
    
    # 读取现有的 .env 文件内容
    env_lines = []
    env_key_indices = {}  # 记录每个键在文件中的索引位置
    if env_file_path.exists():
        env_lines = env_file_path.read_text(encoding='utf-8').splitlines()
        # 提取已存在的键及其索引
        for i, line in enumerate(env_lines):
            line_stripped = line.strip()
            if line_stripped and not line_stripped.startswith('#'):
                if '=' in line_stripped:
                    key = line_stripped.split('=')[0].strip()
                    env_key_indices[key] = i
    
    # 更新或添加配置项
    for key, raw_value in updates.items():
        # 格式化值用于 .env 文件（不需要引号，除非是字符串且包含空格）
        if raw_value is None or raw_value == '':
            env_value = ''
        elif isinstance(raw_value, (int, float)):
            env_value = str(raw_value)
        elif isinstance(raw_value, bool):
            env_value = 'True' if raw_value else 'False'
        else:
            value_str = str(raw_value)
            # 如果包含空格或特殊字符，需要引号
            if ' ' in value_str or '\n' in value_str or '#' in value_str:
                escaped = value_str.replace('\\', '\\\\').replace('"', '\\"')
                env_value = f'"{escaped}"'
            else:
                env_value = value_str
        
        # 更新或添加配置项
        if key in env_key_indices:
            # 更新现有行
            env_lines[env_key_indices[key]] = f'{key}={env_value}'
        else:
            # 添加新行到文件末尾
            env_lines.append(f'{key}={env_value}')
    
    # 写入 .env 文件
    env_file_path.parent.mkdir(parents=True, exist_ok=True)
    env_file_path.write_text('\n'.join(env_lines) + '\n', encoding='utf-8')
    
    # 重新加载配置模块（这会重新读取 .env 文件并创建新的 Settings 实例）
    _load_config_module()


system_state_lock = threading.Lock()
system_state = {
    'started': False,
    'starting': False,
    'shutdown_in_progress': False
}


def _set_system_state(*, started=None, starting=None):
    """Safely update the cached system state flags."""
    with system_state_lock:
        if started is not None:
            system_state['started'] = started
        if starting is not None:
            system_state['starting'] = starting


def _get_system_state():
    """Return a shallow copy of the system state flags."""
    with system_state_lock:
        return system_state.copy()


def _prepare_system_start():
    """Mark the system as starting if it is not already running or starting."""
    with system_state_lock:
        if system_state['started']:
            return False, '系统已启动'
        if system_state['starting']:
            return False, '系统正在启动'
        system_state['starting'] = True
        return True, None

def _mark_shutdown_requested():
    """标记关机已请求；若已有关机流程则返回 False。"""
    with system_state_lock:
        if system_state.get('shutdown_in_progress'):
            return False
        system_state['shutdown_in_progress'] = True
        return True


def initialize_system_components():
    """启动所有依赖组件（Streamlit 子应用、ForumEngine、ReportEngine）。"""
    logs = []
    errors = []
    
    spider = MindSpider()
    if spider.initialize_database():
        logger.info("数据库初始化成功")
    else:
        logger.error("数据库初始化失败")

    try:
        stop_forum_engine()
        logs.append("已停止 ForumEngine 监控器以避免文件冲突")
    except Exception as exc:  # pragma: no cover - 安全捕获
        message = f"停止 ForumEngine 时发生异常: {exc}"
        logs.append(message)
        logger.exception(message)

    processes['forum']['status'] = 'stopped'

    for app_name, script_path in STREAMLIT_SCRIPTS.items():
        logs.append(f"检查文件: {script_path}")
        if os.path.exists(script_path):
            success, message = start_streamlit_app(app_name, script_path, processes[app_name]['port'])
            logs.append(f"{app_name}: {message}")
            if success:
                startup_success, startup_message = wait_for_app_startup(app_name, 30)
                logs.append(f"{app_name} 启动检查: {startup_message}")
                if not startup_success:
                    errors.append(f"{app_name} 启动失败: {startup_message}")
            else:
                errors.append(f"{app_name} 启动失败: {message}")
        else:
            msg = f"文件不存在: {script_path}"
            logs.append(f"错误: {msg}")
            errors.append(f"{app_name}: {msg}")

    forum_started = False
    try:
        start_forum_engine()
        processes['forum']['status'] = 'running'
        logs.append("ForumEngine 启动完成")
        forum_started = True
    except Exception as exc:  # pragma: no cover - 保底捕获
        error_msg = f"ForumEngine 启动失败: {exc}"
        logs.append(error_msg)
        errors.append(error_msg)

    if REPORT_ENGINE_AVAILABLE:
        try:
            if initialize_report_engine():
                logs.append("ReportEngine 初始化成功")
            else:
                msg = "ReportEngine 初始化失败"
                logs.append(msg)
                errors.append(msg)
        except Exception as exc:  # pragma: no cover
            msg = f"ReportEngine 初始化异常: {exc}"
            logs.append(msg)
            errors.append(msg)

    if errors:
        cleanup_processes()
        processes['forum']['status'] = 'stopped'
        if forum_started:
            try:
                stop_forum_engine()
            except Exception:  # pragma: no cover
                logger.exception("停止ForumEngine失败")
        return False, logs, errors

    return True, logs, []

# 初始化ForumEngine的forum.log文件
def start_forum_engine():
    """Forum现在是task-scoped事件服务，无需全局日志监控进程。"""
    logger.info("ForumEngine: task-scoped事件服务已就绪")
    return True


def stop_forum_engine():
    """Forum事件存储随任务存在；停止操作不删除任何任务数据。"""
    logger.info("ForumEngine: task-scoped事件服务无需单独停止")
    return True


def _forum_event_to_message(event):
    """将结构化Forum事件转换为现有前端可消费的消息格式。"""
    source = str(event.get('source', '')).upper()
    if source == 'SYSTEM' or source not in {'QUERY', 'INSIGHT', 'MEDIA', 'HOST'}:
        return None
    try:
        created = datetime.fromisoformat(str(event.get('created_at', '')))
        timestamp = created.astimezone().strftime('%H:%M:%S')
    except Exception:
        timestamp = datetime.now().strftime('%H:%M:%S')

    return {
        'type': 'host' if source == 'HOST' else 'agent',
        'sender': 'Forum Host' if source == 'HOST' else f'{source.title()} Engine',
        'content': str(event.get('content', '')).strip(),
        'timestamp': timestamp,
        'source': source,
        'task_id': event.get('task_id')
    }


def _forum_event_to_log_line(event):
    message = _forum_event_to_message(event)
    if not message:
        return None
    content = message['content'].replace('\n', '\\n').replace('\r', '\\r')
    return f"[{message['timestamp']}] [{message['source']}] {content}"


def _client_room(client_id):
    return f"client:{validate_runtime_id(client_id, 'client_id')}"


def _task_client_room(task_id):
    metadata = read_task_metadata(task_id)
    client_id = metadata.get('client_id')
    if not client_id:
        return None
    try:
        return _client_room(client_id)
    except ValueError:
        return None


forum_event_positions = {}
task_log_positions = {}


def _read_new_task_log_lines(task_id, app_name):
    """增量读取单个task/agent日志；位置也按(task, agent)隔离。"""
    path = task_log_path(task_id, app_name)
    key = (task_id, app_name)
    if not path.exists():
        return []

    try:
        current_size = path.stat().st_size
        last_position = task_log_positions.get(key, 0)
        if current_size < last_position:
            last_position = 0
        with open(path, 'r', encoding='utf-8', errors='ignore') as stream:
            stream.seek(last_position)
            lines = [line.rstrip('\n\r') for line in stream if line.strip()]
            task_log_positions[key] = stream.tell()
        return lines
    except Exception as exc:
        logger.warning(f"task={task_id} 读取{app_name}日志失败: {exc}")
        return []


def monitor_task_runtime():
    """只向创建该task的浏览器tab推送Forum事件和Agent日志。"""
    while True:
        try:
            for task_id in list_task_ids():
                room = _task_client_room(task_id)
                if not room:
                    continue

                after_id = forum_event_positions.get(task_id, 0)
                events = list_events(task_id, after_id=after_id, limit=1000)
                for event in events:
                    message = _forum_event_to_message(event)
                    if message:
                        socketio.emit('forum_message', message, room=room)
                        line = _forum_event_to_log_line(event)
                        if line:
                            socketio.emit(
                                'console_output',
                                {'app': 'forum', 'line': line, 'task_id': task_id},
                                room=room,
                            )
                    forum_event_positions[task_id] = max(
                        forum_event_positions.get(task_id, 0),
                        int(event.get('id', 0)),
                    )

                for app_name in ('insight', 'media', 'query'):
                    for line in _read_new_task_log_lines(task_id, app_name):
                        socketio.emit(
                            'console_output',
                            {'app': app_name, 'line': line, 'task_id': task_id},
                            room=room,
                        )
            time.sleep(0.5)
        except Exception as exc:
            logger.exception(f"task runtime监听异常: {exc}")
            time.sleep(2)


task_runtime_monitor_thread = threading.Thread(
    target=monitor_task_runtime,
    daemon=True,
    name='task-runtime-monitor',
)
task_runtime_monitor_thread.start()

# 全局变量存储进程信息
processes = {
    'insight': {'process': None, 'port': 8501, 'status': 'stopped', 'output': [], 'log_file': None, 'healthcheck_started_at': None},
    'media': {'process': None, 'port': 8502, 'status': 'stopped', 'output': [], 'log_file': None, 'healthcheck_started_at': None},
    'query': {'process': None, 'port': 8503, 'status': 'stopped', 'output': [], 'log_file': None, 'healthcheck_started_at': None},
    'forum': {'process': None, 'port': None, 'status': 'stopped', 'output': [], 'log_file': None}  # 启动后标记为 running
}

STREAMLIT_SCRIPTS = {
    'insight': 'SingleEngineApp/insight_engine_streamlit_app.py',
    'media': 'SingleEngineApp/media_engine_streamlit_app.py',
    'query': 'SingleEngineApp/query_engine_streamlit_app.py'
}

def _log_shutdown_step(message: str):
    """统一记录关机步骤，便于排查。"""
    logger.info(f"[Shutdown] {message}")


def _describe_running_children():
    """列出当前存活的子进程。"""
    running = []
    for name, info in processes.items():
        proc = info.get('process')
        if proc is not None and proc.poll() is None:
            port_desc = f", port={info.get('port')}" if info.get('port') else ""
            running.append(f"{name}(pid={proc.pid}{port_desc})")
    return running

# 输出队列
output_queues = {
    'insight': Queue(),
    'media': Queue(),
    'query': Queue(),
    'forum': Queue()
}

def write_log_to_file(app_name, line, task_id=None):
    """写入task私有日志。研究日志禁止回退到logs/<agent>.log。"""
    if not task_id:
        raise ValueError("写入研究日志必须提供task_id")
    path = task_log_path(task_id, app_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as stream:
        stream.write(str(line) + '\n')
        stream.flush()


def read_log_from_file(app_name, tail_lines=None, task_id=None):
    """读取指定task的Agent/Forum日志，不读取历史全局共享文件。"""
    if not task_id:
        return []
    try:
        task_id = validate_runtime_id(task_id)
        if app_name == 'forum':
            lines = [line for line in render_forum_log(task_id).splitlines() if line.strip()]
        else:
            path = task_log_path(task_id, app_name)
            if not path.exists():
                return []
            with open(path, 'r', encoding='utf-8', errors='ignore') as stream:
                lines = [line.rstrip('\n\r') for line in stream if line.strip()]
        if tail_lines:
            return lines[-int(tail_lines):]
        return lines
    except Exception as exc:
        logger.exception(f"task={task_id} 读取{app_name}日志失败: {exc}")
        return []


def read_process_output(process, app_name):
    """Drain shared Streamlit stdout without persisting/broadcasting research text."""
    import select
    import sys

    while True:
        try:
            if process.poll() is not None:
                if process.stdout:
                    process.stdout.read()
                break
            if sys.platform == 'win32':
                output = process.stdout.readline()
                if not output:
                    time.sleep(0.1)
            else:
                ready, _, _ = select.select([process.stdout], [], [], 0.1)
                if ready:
                    process.stdout.readline()
        except Exception as exc:
            logger.warning(f"读取{app_name}子进程stdout失败: {exc}")
            break


def start_streamlit_app(app_name, script_path, port):
    """启动共享Streamlit服务；具体研究会话由task_id隔离。"""
    try:
        if processes[app_name]['process'] is not None:
            return False, "应用已经在运行"
        if not os.path.exists(script_path):
            return False, f"文件不存在: {script_path}"

        logger.info(f"启动 {app_name} Streamlit服务")
        cmd = [
            sys.executable, '-m', 'streamlit', 'run',
            script_path,
            '--server.port', str(port),
            '--server.headless', 'true',
            '--browser.gatherUsageStats', 'false',
            '--logger.level', 'info',
            '--server.enableCORS', 'false'
        ]
        env = os.environ.copy()
        env.update({
            'PYTHONIOENCODING': 'utf-8',
            'PYTHONUTF8': '1',
            'LANG': 'en_US.UTF-8',
            'LC_ALL': 'en_US.UTF-8',
            'PYTHONUNBUFFERED': '1',
            'STREAMLIT_BROWSER_GATHER_USAGE_STATS': 'false'
        })
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            universal_newlines=False,
            cwd=os.getcwd(),
            env=env,
            encoding=None,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )

        processes[app_name]['process'] = process
        processes[app_name]['status'] = 'starting'
        processes[app_name]['output'] = []
        processes[app_name]['healthcheck_started_at'] = time.time()

        threading.Thread(
            target=read_process_output,
            args=(process, app_name),
            daemon=True,
            name=f"{app_name}-stdout-drain",
        ).start()
        return True, f"{app_name} 应用启动中..."
    except Exception as exc:
        logger.exception(f"启动{app_name}失败: {exc}")
        return False, f"启动失败: {exc}"


def stop_streamlit_app(app_name):
    """停止Streamlit应用"""
    try:
        process = processes[app_name]['process']
        if process is None:
            _log_shutdown_step(f"{app_name} 未运行，跳过停止")
            return False, "应用未运行"
        try:
            pid = process.pid
        except Exception:
            pid = 'unknown'
        _log_shutdown_step(f"正在停止 {app_name} (pid={pid})")
        process.terminate()
        try:
            process.wait(timeout=5)
            _log_shutdown_step(f"{app_name} 退出完成，returncode={process.returncode}")
        except subprocess.TimeoutExpired:
            _log_shutdown_step(f"{app_name} 终止超时，尝试强制结束 (pid={pid})")
            process.kill()
            process.wait()
            _log_shutdown_step(f"{app_name} 已强制结束，returncode={process.returncode}")
        processes[app_name]['process'] = None
        processes[app_name]['status'] = 'stopped'
        processes[app_name]['healthcheck_started_at'] = None
        return True, f"{app_name} 应用已停止"
    except Exception as exc:
        _log_shutdown_step(f"{app_name} 停止失败: {exc}")
        return False, f"停止失败: {exc}"


HEALTHCHECK_PATH = "/_stcore/health"
HEALTHCHECK_PROXIES = {'http': None, 'https': None}
HEALTHCHECK_GRACE_SECONDS = 15


def _build_healthcheck_url(port):
    return f"http://127.0.0.1:{port}{HEALTHCHECK_PATH}"


def _healthcheck_grace_active(app_name: str) -> bool:
    started_at = processes.get(app_name, {}).get('healthcheck_started_at')
    if not started_at:
        return False
    return (time.time() - started_at) < HEALTHCHECK_GRACE_SECONDS


def _log_healthcheck_failure(app_name: str, exc: Exception):
    if _healthcheck_grace_active(app_name):
        logger.debug(f"正在启动{app_name}，请等待")
        return
    logger.warning(f"{app_name} 健康检查失败: {exc}")


def check_app_status():
    """检查应用状态"""
    for app_name, info in processes.items():
        if info['process'] is not None:
            if info['process'].poll() is None:
                # 进程仍在运行，检查端口是否可访问
                try:
                    response = requests.get(
                        _build_healthcheck_url(info['port']),
                        timeout=2,
                        proxies=HEALTHCHECK_PROXIES
                    )
                    if response.status_code == 200:
                        info['status'] = 'running'
                    else:
                        info['status'] = 'starting'
                except Exception as exc:
                    _log_healthcheck_failure(app_name, exc)
                    info['status'] = 'starting'
            else:
                # 进程已结束
                info['process'] = None
                info['status'] = 'stopped'
                info['healthcheck_started_at'] = None

def wait_for_app_startup(app_name, max_wait_time=90):
    """等待应用启动完成"""
    import time
    start_time = time.time()
    while time.time() - start_time < max_wait_time:
        info = processes[app_name]
        if info['process'] is None:
            return False, "进程已停止"
        
        if info['process'].poll() is not None:
            return False, "进程启动失败"
        
        try:
            response = requests.get(
                _build_healthcheck_url(info['port']),
                timeout=2,
                proxies=HEALTHCHECK_PROXIES
            )
            if response.status_code == 200:
                info['status'] = 'running'
                return True, "启动成功"
        except Exception as exc:
            _log_healthcheck_failure(app_name, exc)

        time.sleep(1)

    return False, "启动超时"

def cleanup_processes():
    """清理所有进程"""
    _log_shutdown_step("开始串行清理子进程")
    for app_name in STREAMLIT_SCRIPTS:
        stop_streamlit_app(app_name)

    processes['forum']['status'] = 'stopped'
    try:
        stop_forum_engine()
    except Exception:  # pragma: no cover
        logger.exception("停止ForumEngine失败")
    _log_shutdown_step("子进程清理完成")
    _set_system_state(started=False, starting=False)

def cleanup_processes_concurrent(timeout: float = 6.0):
    """并发清理所有子进程，超时后强制杀掉残留进程。"""
    _log_shutdown_step(f"开始并发清理子进程（超时 {timeout}s）")
    _log_shutdown_step("仅终止当前控制台启动并记录的子进程，不做端口扫描")
    running_before = _describe_running_children()
    if running_before:
        _log_shutdown_step("当前存活子进程: " + ", ".join(running_before))
    else:
        _log_shutdown_step("未检测到存活子进程，仍将发送关闭指令")

    threads = []

    # 并发关闭 Streamlit 子进程
    for app_name in STREAMLIT_SCRIPTS:
        t = threading.Thread(target=stop_streamlit_app, args=(app_name,), daemon=True)
        threads.append(t)
        t.start()

    # 并发关闭 ForumEngine
    forum_thread = threading.Thread(target=stop_forum_engine, daemon=True)
    threads.append(forum_thread)
    forum_thread.start()

    # 等待所有线程完成，最多 timeout 秒
    end_time = time.time() + timeout
    for t in threads:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        t.join(timeout=remaining)

    # 二次检查：强制杀掉仍存活的子进程
    for app_name in STREAMLIT_SCRIPTS:
        proc = processes[app_name]['process']
        if proc is not None and proc.poll() is None:
            try:
                _log_shutdown_step(f"{app_name} 进程仍存活，触发二次终止 (pid={proc.pid})")
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    _log_shutdown_step(f"{app_name} 二次终止失败，尝试kill (pid={proc.pid})")
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    logger.warning(f"{app_name} 进程强制退出失败，继续关机")
            finally:
                processes[app_name]['process'] = None
                processes[app_name]['status'] = 'stopped'

    processes['forum']['status'] = 'stopped'
    _log_shutdown_step("并发清理结束，标记系统未启动")
    _set_system_state(started=False, starting=False)

def _schedule_server_shutdown(delay_seconds: float = 0.1):
    """在清理完成后尽快退出，避免阻塞当前请求。"""
    def _shutdown():
        time.sleep(delay_seconds)
        try:
            socketio.stop()
        except Exception as exc:  # pragma: no cover
            logger.warning(f"SocketIO 停止时异常，继续退出: {exc}")
        _log_shutdown_step("SocketIO 停止指令已发送，即将退出主进程")
        os._exit(0)

    threading.Thread(target=_shutdown, daemon=True).start()

def _start_async_shutdown(cleanup_timeout: float = 3.0):
    """异步触发清理并强制退出，避免HTTP请求阻塞。"""
    _log_shutdown_step(f"收到关机指令，启动异步清理（超时 {cleanup_timeout}s）")

    def _force_exit():
        _log_shutdown_step("关机超时，触发强制退出")
        os._exit(0)

    # 硬超时保护，即便清理线程异常也能退出
    hard_timeout = cleanup_timeout + 2.0
    force_timer = threading.Timer(hard_timeout, _force_exit)
    force_timer.daemon = True
    force_timer.start()

    def _cleanup_and_exit():
        try:
            cleanup_processes_concurrent(timeout=cleanup_timeout)
        except Exception as exc:  # pragma: no cover
            logger.exception(f"关机清理异常: {exc}")
        finally:
            _log_shutdown_step("清理线程结束，调度主进程退出")
            _schedule_server_shutdown(0.05)

    threading.Thread(target=_cleanup_and_exit, daemon=True).start()

# 注册清理函数
atexit.register(cleanup_processes)

@app.route('/')
def index():
    """主页：先加载task隔离bootstrap，再执行旧版页面脚本。"""
    html = render_template('index.html')
    bootstrap = '<script src="/static/task_isolation.js"></script>'
    if '<head>' in html:
        html = html.replace('<head>', '<head>' + bootstrap, 1)
    else:
        html = bootstrap + html
    return Response(html, mimetype='text/html')

@app.route('/api/status')
def get_status():
    """获取所有应用状态"""
    check_app_status()
    return jsonify({
        app_name: {
            'status': info['status'],
            'port': info['port'],
            'output_lines': len(info['output'])
        }
        for app_name, info in processes.items()
    })

@app.route('/api/start/<app_name>')
def start_app(app_name):
    """启动指定应用"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': '未知应用'})

    if app_name == 'forum':
        try:
            start_forum_engine()
            processes['forum']['status'] = 'running'
            return jsonify({'success': True, 'message': 'ForumEngine已启动'})
        except Exception as exc:  # pragma: no cover
            logger.exception("手动启动ForumEngine失败")
            return jsonify({'success': False, 'message': f'ForumEngine启动失败: {exc}'})

    script_path = STREAMLIT_SCRIPTS.get(app_name)
    if not script_path:
        return jsonify({'success': False, 'message': '该应用不支持启动操作'})

    success, message = start_streamlit_app(
        app_name,
        script_path,
        processes[app_name]['port']
    )

    if success:
        # 等待应用启动
        startup_success, startup_message = wait_for_app_startup(app_name, 15)
        if not startup_success:
            message += f" 但启动检查失败: {startup_message}"
    
    return jsonify({'success': success, 'message': message})

@app.route('/api/stop/<app_name>')
def stop_app(app_name):
    """停止指定应用"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': '未知应用'})

    if app_name == 'forum':
        try:
            stop_forum_engine()
            processes['forum']['status'] = 'stopped'
            return jsonify({'success': True, 'message': 'ForumEngine已停止'})
        except Exception as exc:  # pragma: no cover
            logger.exception("手动停止ForumEngine失败")
            return jsonify({'success': False, 'message': f'ForumEngine停止失败: {exc}'})

    success, message = stop_streamlit_app(app_name)
    return jsonify({'success': success, 'message': message})

def _task_id_from_request(payload=None):
    payload = payload if isinstance(payload, dict) else {}
    raw = (
        payload.get('task_id')
        or payload.get('research_task_id')
        or request.args.get('task_id')
        or request.headers.get('X-BettaFish-Task-ID')
    )
    if not raw:
        return None
    return validate_runtime_id(str(raw))


@app.route('/api/tasks', methods=['POST'])
def create_research_task():
    """创建/确认一个研究task及其浏览器tab归属。"""
    data = request.get_json(silent=True) or {}
    try:
        task_id = validate_runtime_id(str(data.get('task_id') or new_task_id()))
        client_id = validate_runtime_id(
            str(data.get('client_id') or new_client_id()),
            'client_id',
        )
        query = str(data.get('query') or '')
        ensure_task(task_id, client_id=client_id, query=query)
        initialize_task_forum(task_id)
        return jsonify({
            'success': True,
            'task_id': task_id,
            'client_id': client_id,
            'query': query,
        })
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@app.route('/api/output/<app_name>')
def get_output(app_name):
    """只返回指定task的输出，禁止读取共享Agent日志。"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': '未知应用'}), 404
    try:
        task_id = _task_id_from_request()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    if not task_id:
        return jsonify({'success': False, 'message': '缺少task_id'}), 400

    output_lines = read_log_from_file(app_name, task_id=task_id)
    return jsonify({
        'success': True,
        'task_id': task_id,
        'output': output_lines,
        'total_lines': len(output_lines),
    })


@app.route('/api/test_log/<app_name>')
def test_log(app_name):
    """向指定task写入测试日志。"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': '未知应用'}), 404
    try:
        task_id = _task_id_from_request()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    if not task_id:
        return jsonify({'success': False, 'message': '缺少task_id'}), 400

    test_msg = f"[{datetime.now().strftime('%H:%M:%S')}] 测试日志消息 - {datetime.now()}"
    write_log_to_file(app_name, test_msg, task_id=task_id)
    room = _task_client_room(task_id)
    if room:
        socketio.emit(
            'console_output',
            {'app': app_name, 'line': test_msg, 'task_id': task_id},
            room=room,
        )
    return jsonify({'success': True, 'task_id': task_id, 'message': '测试消息已写入task日志'})


@app.route('/api/forum/start')
def start_forum_monitoring_api():
    start_forum_engine()
    processes['forum']['status'] = 'running'
    return jsonify({'success': True, 'message': 'task-scoped Forum服务已就绪'})


@app.route('/api/forum/stop')
def stop_forum_monitoring_api():
    stop_forum_engine()
    processes['forum']['status'] = 'stopped'
    return jsonify({'success': True, 'message': 'Forum服务已标记停止'})


@app.route('/api/forum/log')
def get_forum_log():
    """返回指定task的完整Forum事件历史。"""
    try:
        task_id = _task_id_from_request()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    if not task_id:
        return jsonify({'success': False, 'message': '缺少task_id'}), 400

    try:
        events = list_events(task_id, limit=5000)
        lines = [line for line in (_forum_event_to_log_line(e) for e in events) if line]
        messages = [msg for msg in (_forum_event_to_message(e) for e in events) if msg]
        return jsonify({
            'success': True,
            'task_id': task_id,
            'log_lines': lines,
            'parsed_messages': messages,
            'total_lines': len(lines),
            'position': int(events[-1]['id']) if events else 0,
        })
    except Exception as exc:
        logger.exception(f"task={task_id} 读取Forum历史失败")
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/forum/log/history', methods=['POST'])
def get_forum_log_history():
    """按Forum事件ID增量读取指定task；position不再是全局文件字节偏移。"""
    data = request.get_json(silent=True) or {}
    try:
        task_id = _task_id_from_request(data)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    if not task_id:
        return jsonify({'success': False, 'message': '缺少task_id'}), 400

    start_position = max(0, int(data.get('position', 0) or 0))
    max_lines = max(1, min(int(data.get('max_lines', 1000) or 1000), 5000))
    events = list_events(task_id, after_id=start_position, limit=max_lines + 1)
    has_more = len(events) > max_lines
    returned = events[:max_lines]
    lines = [line for line in (_forum_event_to_log_line(e) for e in returned) if line]
    position = int(returned[-1]['id']) if returned else start_position
    return jsonify({
        'success': True,
        'task_id': task_id,
        'log_lines': lines,
        'position': position,
        'has_more': has_more,
    })


@app.route('/api/search', methods=['POST'])
def search():
    """统一搜索接口：生成/沿用task_id并把它传给每个子引擎。"""
    data = request.get_json(silent=True) or {}
    query = str(data.get('query', '')).strip()
    if not query:
        return jsonify({'success': False, 'message': '搜索查询不能为空'}), 400

    try:
        task_id = validate_runtime_id(str(data.get('task_id') or new_task_id()))
        client_id = validate_runtime_id(
            str(data.get('client_id') or new_client_id()),
            'client_id',
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    ensure_task(task_id, client_id=client_id, query=query)
    initialize_task_forum(task_id)

    check_app_status()
    running_apps = [name for name, info in processes.items() if info['status'] == 'running']
    if not running_apps:
        return jsonify({'success': False, 'message': '没有运行中的应用'}), 400

    results = {}
    api_ports = {'insight': 8501, 'media': 8502, 'query': 8503}
    payload = {'query': query, 'task_id': task_id, 'client_id': client_id}

    for app_name in running_apps:
        if app_name not in api_ports:
            continue
        try:
            response = requests.post(
                f"http://localhost:{api_ports[app_name]}/api/search",
                json=payload,
                timeout=10,
            )
            if response.status_code == 200:
                results[app_name] = response.json()
            else:
                results[app_name] = {'success': False, 'message': 'API调用失败'}
        except Exception as exc:
            results[app_name] = {'success': False, 'message': str(exc)}

    return jsonify({
        'success': True,
        'query': query,
        'task_id': task_id,
        'client_id': client_id,
        'results': results,
    })


@app.route('/api/config', methods=['GET'])
def get_config():
    """Expose selected configuration values to the frontend."""
    try:
        config_values = read_config_values()
        return jsonify({'success': True, 'config': config_values})
    except Exception as exc:
        logger.exception("读取配置失败")
        return jsonify({'success': False, 'message': f'读取配置失败: {exc}'}), 500


@app.route('/api/config', methods=['POST'])
def update_config():
    """Update configuration values and persist them to config.py."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        return jsonify({'success': False, 'message': '请求体不能为空'}), 400

    updates = {}
    for key, value in payload.items():
        if key in CONFIG_KEYS:
            updates[key] = value if value is not None else ''

    if not updates:
        return jsonify({'success': False, 'message': '没有可更新的配置项'}), 400

    try:
        write_config_values(updates)
        updated_config = read_config_values()
        return jsonify({'success': True, 'config': updated_config})
    except Exception as exc:
        logger.exception("更新配置失败")
        return jsonify({'success': False, 'message': f'更新配置失败: {exc}'}), 500


@app.route('/api/system/status')
def get_system_status():
    """返回系统启动状态。"""
    state = _get_system_state()
    return jsonify({
        'success': True,
        'started': state['started'],
        'starting': state['starting']
    })


@app.route('/api/system/start', methods=['POST'])
def start_system():
    """在接收到请求后启动完整系统。"""
    allowed, message = _prepare_system_start()
    if not allowed:
        return jsonify({'success': False, 'message': message}), 400

    try:
        success, logs, errors = initialize_system_components()
        if success:
            _set_system_state(started=True)
            return jsonify({'success': True, 'message': '系统启动成功', 'logs': logs})

        _set_system_state(started=False)
        return jsonify({
            'success': False,
            'message': '系统启动失败',
            'logs': logs,
            'errors': errors
        }), 500
    except Exception as exc:  # pragma: no cover - 保底捕获
        logger.exception("系统启动过程中出现异常")
        _set_system_state(started=False)
        return jsonify({'success': False, 'message': f'系统启动异常: {exc}'}), 500
    finally:
        _set_system_state(starting=False)

@app.route('/api/system/shutdown', methods=['POST'])
def shutdown_system():
    """优雅停止所有组件并关闭当前服务进程。"""
    state = _get_system_state()
    if state['starting']:
        return jsonify({'success': False, 'message': '系统正在启动/重启，请稍候'}), 400

    target_ports = [
        f"{name}:{info['port']}"
        for name, info in processes.items()
        if info.get('port')
    ]

    # 已有关机请求执行中时，返回当前存活的子进程，便于前端判断进度
    if not _mark_shutdown_requested():
        running = _describe_running_children()
        detail = '关机指令已下发，请稍等...'
        if running:
            detail = f"关机指令已下发，等待进程退出: {', '.join(running)}"
        if target_ports:
            detail = f"{detail}（端口: {', '.join(target_ports)}）"
        return jsonify({'success': True, 'message': detail, 'ports': target_ports})

    running = _describe_running_children()
    if running:
        _log_shutdown_step("开始关闭系统，正在等待子进程退出: " + ", ".join(running))
    else:
        _log_shutdown_step("开始关闭系统，未检测到存活子进程")

    try:
        _set_system_state(started=False, starting=False)
        _start_async_shutdown(cleanup_timeout=6.0)
        message = '关闭系统指令已下发，正在停止进程'
        if running:
            message = f"{message}: {', '.join(running)}"
        if target_ports:
            message = f"{message}（端口: {', '.join(target_ports)}）"
        return jsonify({'success': True, 'message': message, 'ports': target_ports})
    except Exception as exc:  # pragma: no cover - 兜底捕获
        logger.exception("系统关闭过程中出现异常")
        return jsonify({'success': False, 'message': f'系统关闭异常: {exc}'}), 500

@socketio.on('connect')
def handle_connect(auth=None):
    """每个浏览器tab加入独立client room，避免跨设备/跨tab广播。"""
    auth = auth if isinstance(auth, dict) else {}
    raw_client_id = auth.get('client_id') or request.args.get('client_id')
    if raw_client_id:
        try:
            client_id = validate_runtime_id(str(raw_client_id), 'client_id')
            join_room(_client_room(client_id))
        except ValueError:
            logger.warning("Socket连接携带了非法client_id，未加入私有room")
    emit('status', 'Connected to Flask server')

@socketio.on('request_status')
def handle_status_request():
    """请求状态更新"""
    check_app_status()
    emit('status_update', {
        app_name: {
            'status': info['status'],
            'port': info['port']
        }
        for app_name, info in processes.items()
    })

if __name__ == '__main__':
    # 从配置文件读取 HOST 和 PORT
    from config import settings
    HOST = settings.HOST
    PORT = settings.PORT
    
    logger.info("等待配置确认，系统将在前端指令后启动组件...")
    logger.info(f"Flask服务器已启动，访问地址: http://{HOST}:{PORT}")
    
    try:
        socketio.run(app, host=HOST, port=PORT, debug=False)
    except KeyboardInterrupt:
        logger.info("\n正在关闭应用...")
        cleanup_processes()
        
    
