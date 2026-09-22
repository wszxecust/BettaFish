"""
Streamlit Web界面
为Query Agent提供友好的Web界面
"""

import os
import sys
import streamlit as st
from datetime import datetime
import json
import locale
from loguru import logger

# 设置UTF-8编码环境
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'

# 设置系统编码
try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except locale.Error:
        pass

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from QueryEngine import DeepSearchAgent, Settings
from config import settings
from utils.github_issues import error_with_issue_link
from utils.task_runtime import (
    claim_engine_run,
    engine_run_status,
    ensure_task,
    finish_engine_run,
    latest_task_report,
    new_task_id,
    task_log_context,
    task_output_dir,
)


def main():
    """主函数"""
    st.set_page_config(
        page_title="Query Agent",
        page_icon="",
        layout="wide"
    )

    st.title("Query Agent")
    st.markdown("具备强大网页搜索能力的AI代理")
    st.markdown("广度爬取官方报道与新闻，注重国内外资源相结合理解舆情")

    # 检查URL参数
    try:
        # 尝试使用新版本的query_params
        query_params = st.query_params
        auto_query = query_params.get('query', '')
        auto_search = query_params.get('auto_search', 'false').lower() == 'true'
        view_only = query_params.get('view_only', 'false').lower() == 'true'
        auto_task_id = query_params.get('task_id', '')
        auto_client_id = query_params.get('client_id', '')
    except AttributeError:
        # 兼容旧版本
        query_params = st.experimental_get_query_params()
        auto_query = query_params.get('query', [''])[0]
        auto_search = query_params.get('auto_search', ['false'])[0].lower() == 'true'
        view_only = query_params.get('view_only', ['false'])[0].lower() == 'true'
        auto_task_id = query_params.get('task_id', [''])[0]
        auto_client_id = query_params.get('client_id', [''])[0]

    # ----- 配置被硬编码 -----
    # 强制使用 DeepSeek
    model_name = settings.QUERY_ENGINE_MODEL_NAME or "deepseek-chat"
    # 默认高级配置
    max_reflections = 2
    max_content_length = 20000

    # 简化的研究查询展示区域

    # 如果有自动查询，使用它作为默认值，否则显示占位符
    display_query = auto_query if auto_query else "等待从主页面接收分析内容..."

    # 只读的查询展示区域
    st.text_area(
        "当前查询",
        value=display_query,
        height=100,
        disabled=True,
        help="查询内容由主页面的搜索框控制",
        label_visibility="hidden"
    )

    # 自动搜索逻辑：同一浏览器会话可以连续运行多个独立task。
    start_research = False
    query = auto_query
    task_id = (auto_task_id or '').strip() or new_task_id()
    client_id = (auto_client_id or '').strip() or None
    execution_key = f"auto_search_executed:{task_id}"

    # 历史task只读恢复：绝不claim运行锁，也绝不重新执行Agent。
    if view_only and auto_query:
        status = engine_run_status(task_id, "query")
        report_path = latest_task_report(task_id, "query")
        if report_path and report_path.exists():
            if status == "failed":
                st.warning("Query Agent上次运行失败，下面显示失败前已保存的结果。")
            elif status == "running":
                st.info("Query Agent仍在运行，下面显示当前已保存的结果。")
            else:
                st.info("Query Agent历史结果")
            st.markdown(report_path.read_text(encoding="utf-8"))
        elif status == "running":
            st.info("Query Agent正在运行，可在主界面实时日志中查看进度。")
        elif status == "failed":
            st.error("Query Agent上次运行失败，暂无可恢复的报告。")
        else:
            st.info("Query Agent暂无已保存结果。")
        return

    if auto_search and auto_query and execution_key not in st.session_state:
        ensure_task(task_id, client_id=client_id, query=query)
        st.session_state[execution_key] = True
        start_research = True
    elif auto_query and not auto_search:
        st.warning("等待搜索启动信号...")

    # 验证配置
    if start_research:
        if not query.strip():
            st.error("请输入研究查询")
            return

        # 由于强制使用DeepSeek，检查相关的API密钥
        if not settings.QUERY_ENGINE_API_KEY:
            st.error("请在您的环境变量中设置QUERY_ENGINE_API_KEY")
            return
        if not settings.TAVILY_API_KEY:
            st.error("请在您的环境变量中设置TAVILY_API_KEY")
            return

        # 自动使用配置文件中的API密钥
        engine_key = settings.QUERY_ENGINE_API_KEY
        tavily_key = settings.TAVILY_API_KEY

        # 创建配置
        config = Settings(
            QUERY_ENGINE_API_KEY=engine_key,
            QUERY_ENGINE_BASE_URL=settings.QUERY_ENGINE_BASE_URL,
            QUERY_ENGINE_MODEL_NAME=model_name,
            TAVILY_API_KEY=tavily_key,
            MAX_REFLECTIONS=max_reflections,
            SEARCH_CONTENT_MAX_LENGTH=max_content_length,
            OUTPUT_DIR=str(task_output_dir(task_id, "query"))
        )

        # 执行研究
        execute_research(query, config, task_id)


def execute_research(query: str, config: Settings, task_id: str):
    """保证同一task的Query Agent只执行一次；其他设备只订阅/查看。"""
    run_token, run_status = claim_engine_run(task_id, "query")
    if run_status == "running":
        st.session_state.pop(f"auto_search_executed:{task_id}", None)
        st.info("该任务的Query Agent已在其他页面运行，本页面不会重复启动。")
        return False
    if run_status == "completed":
        st.info("该任务的Query Agent已经完成，直接显示已保存结果。")
        report_path = latest_task_report(task_id, "query")
        if report_path and report_path.exists():
            st.markdown(report_path.read_text(encoding="utf-8"))
        return True

    success = False
    try:
        with task_log_context(task_id, "query"):
            success = bool(_execute_research(query, config, task_id))
        return success
    finally:
        finish_engine_run(
            task_id,
            "query",
            run_token,
            success=success,
        )
        if not success:
            st.session_state.pop(f"auto_search_executed:{task_id}", None)


def _execute_research(query: str, config: Settings, task_id: str):
    """执行研究"""
    try:
        # 创建进度条
        progress_bar = st.progress(0)
        status_text = st.empty()

        # 初始化Agent
        status_text.text("正在初始化Agent...")
        agent = DeepSearchAgent(config, task_id=task_id)
        st.session_state.agent = agent

        progress_bar.progress(10)

        # 生成报告结构
        status_text.text("正在生成报告结构...")
        agent._generate_report_structure(query)
        progress_bar.progress(20)

        # 处理段落
        total_paragraphs = len(agent.state.paragraphs)
        for i in range(total_paragraphs):
            status_text.text(f"正在处理段落 {i + 1}/{total_paragraphs}: {agent.state.paragraphs[i].title}")

            # 初始搜索和总结
            agent._initial_search_and_summary(i)
            progress_value = 20 + (i + 0.5) / total_paragraphs * 60
            progress_bar.progress(int(progress_value))

            # 反思循环
            agent._reflection_loop(i)
            agent.state.paragraphs[i].research.mark_completed()

            progress_value = 20 + (i + 1) / total_paragraphs * 60
            progress_bar.progress(int(progress_value))

        # 生成最终报告
        status_text.text("正在生成最终报告...")
        final_report = agent._generate_final_report()
        progress_bar.progress(90)

        # 保存报告
        status_text.text("正在保存报告...")
        agent._save_report(final_report)
        progress_bar.progress(100)

        status_text.text("研究完成！")

        # 显示结果
        display_results(agent, final_report)
        return True

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        error_display = error_with_issue_link(
            f"研究过程中发生错误: {str(e)}",
            error_traceback,
            app_name="Query Engine Streamlit App"
        )
        st.error(error_display)
        logger.exception(f"研究过程中发生错误: {str(e)}")
        return False


def display_results(agent: DeepSearchAgent, final_report: str):
    """显示研究结果"""
    st.header("研究结果")

    # 结果标签页（已移除下载选项）
    tab1, tab2 = st.tabs(["研究小结", "引用信息"])

    with tab1:
        st.markdown(final_report)

    with tab2:
        # 段落详情
        st.subheader("段落详情")
        for i, paragraph in enumerate(agent.state.paragraphs):
            with st.expander(f"段落 {i + 1}: {paragraph.title}"):
                st.write("**预期内容:**", paragraph.content)
                st.write("**最终内容:**", paragraph.research.latest_summary[:300] + "..."
                if len(paragraph.research.latest_summary) > 300
                else paragraph.research.latest_summary)
                st.write("**搜索次数:**", paragraph.research.get_search_count())
                st.write("**反思次数:**", paragraph.research.reflection_iteration)

        # 搜索历史
        st.subheader("搜索历史")
        all_searches = []
        for paragraph in agent.state.paragraphs:
            all_searches.extend(paragraph.research.search_history)

        if all_searches:
            for i, search in enumerate(all_searches):
                with st.expander(f"搜索 {i + 1}: {search.query}"):
                    st.write("**URL:**", search.url)
                    st.write("**标题:**", search.title)
                    st.write("**内容预览:**",
                             search.content[:200] + "..." if len(search.content) > 200 else search.content)
                    if search.score:
                        st.write("**相关度评分:**", search.score)


if __name__ == "__main__":
    main()
