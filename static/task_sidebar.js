(() => {
  "use strict";

  const COLLAPSED_KEY = "bettafish.sidebarCollapsed";
  const REFRESH_MS = 4000;
  let tasks = [];
  let searchTerm = "";
  let refreshTimer = null;

  const icons = {
    panel: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"></rect><path d="M9 4v16"></path></svg>',
    compose: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z"></path></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path></svg>'
  };

  function ctx() {
    return window.BettaFishTaskContext;
  }

  function createStyles() {
    const style = document.createElement("style");
    style.id = "bf-task-sidebar-style";
    style.textContent = `
      :root { --bf-sidebar-width: 282px; }
      body.bf-sidebar-ready { padding-left: var(--bf-sidebar-width); transition: padding-left .2s ease; }
      body.bf-sidebar-collapsed { --bf-sidebar-width: 68px; }
      body.bf-sidebar-ready > .container {
        width: calc(100vw - var(--bf-sidebar-width));
        max-width: none;
        transition: width .2s ease;
      }

      #bfTaskSidebar {
        position: fixed;
        inset: 0 auto 0 0;
        width: var(--bf-sidebar-width);
        z-index: 1200;
        background: #f9f9f9;
        border-right: 1px solid #e7e7e7;
        display: flex;
        flex-direction: column;
        padding: 12px 10px;
        font-family: Arial, sans-serif;
        color: #111;
        overflow: hidden;
        transition: width .2s ease;
      }

      .bf-sidebar-top {
        height: 46px;
        display: flex;
        align-items: center;
        gap: 4px;
        margin-bottom: 8px;
      }

      .bf-brand {
        flex: 1;
        min-width: 0;
        padding: 0 12px;
        font-size: 20px;
        font-weight: 700;
        letter-spacing: -.3px;
        white-space: nowrap;
      }

      .bf-icon-btn,
      .bf-new-task {
        border: 0;
        background: transparent;
        color: #111;
        cursor: pointer;
        border-radius: 12px;
        transition: background .14s ease;
      }

      .bf-icon-btn:hover,
      .bf-new-task:hover { background: #ececec; }

      .bf-icon-btn {
        width: 44px;
        height: 44px;
        display: grid;
        place-items: center;
        flex: 0 0 44px;
      }

      .bf-icon-btn svg,
      .bf-new-task svg {
        width: 23px;
        height: 23px;
        fill: none;
        stroke: currentColor;
        stroke-width: 1.9;
        stroke-linecap: round;
        stroke-linejoin: round;
      }

      .bf-new-task {
        width: 100%;
        min-height: 48px;
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 0 12px;
        font-size: 16px;
        text-align: left;
        margin-bottom: 8px;
      }

      .bf-new-task svg { flex: 0 0 23px; }

      .bf-search-box {
        margin: 2px 4px 10px;
        position: relative;
        display: none;
      }

      .bf-search-box.visible { display: block; }

      .bf-search-box input {
        width: 100%;
        height: 38px;
        border: 1px solid #d8d8d8;
        border-radius: 10px;
        background: #fff;
        padding: 0 12px;
        outline: none;
        font-size: 14px;
      }

      .bf-search-box input:focus { border-color: #9b9b9b; }

      .bf-task-scroll {
        flex: 1;
        min-height: 0;
        overflow-y: auto;
        overflow-x: hidden;
        padding: 2px 0 12px;
        scrollbar-width: thin;
      }

      .bf-task-group { margin-top: 12px; }

      .bf-task-group-title {
        padding: 0 12px 6px;
        font-size: 12px;
        color: #777;
        font-weight: 600;
      }

      .bf-task-row {
        width: 100%;
        min-height: 42px;
        border: 0;
        border-radius: 9px;
        background: transparent;
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px 8px 12px;
        cursor: pointer;
        color: #1a1a1a;
        text-align: left;
      }

      .bf-task-row:hover { background: #ececec; }
      .bf-task-row.active { background: #e7e7e7; }

      .bf-task-title {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 14px;
        line-height: 20px;
      }

      .bf-task-status {
        width: 9px;
        height: 9px;
        border-radius: 50%;
        flex: 0 0 9px;
        margin-left: 5px;
      }

      .bf-task-status.completed { background: #19a974; }
      .bf-task-status.failed { background: #e5484d; }
      .bf-task-status.running {
        width: 10px;
        height: 10px;
        flex-basis: 10px;
        border: 1.5px solid #9a9a9a;
        border-top-color: transparent;
        background: transparent;
        animation: bf-spin 1.1s linear infinite;
      }

      @keyframes bf-spin { to { transform: rotate(360deg); } }

      .bf-empty {
        padding: 24px 12px;
        color: #8a8a8a;
        font-size: 13px;
        line-height: 1.6;
      }

      body.bf-sidebar-collapsed #bfTaskSidebar {
        padding-left: 12px;
        padding-right: 12px;
      }

      body.bf-sidebar-collapsed .bf-brand,
      body.bf-sidebar-collapsed #bfHistorySearchButton,
      body.bf-sidebar-collapsed .bf-new-task-label,
      body.bf-sidebar-collapsed .bf-search-box,
      body.bf-sidebar-collapsed .bf-task-scroll {
        display: none !important;
      }

      body.bf-sidebar-collapsed .bf-sidebar-top {
        justify-content: center;
      }

      body.bf-sidebar-collapsed #bfSidebarToggle {
        display: grid;
      }

      body.bf-sidebar-collapsed .bf-new-task {
        width: 44px;
        height: 44px;
        min-height: 44px;
        padding: 0;
        margin: 8px auto 0;
        justify-content: center;
      }

      @media (max-width: 860px) {
        body.bf-sidebar-ready,
        body.bf-sidebar-collapsed { padding-left: 0; }

        body.bf-sidebar-ready > .container,
        body.bf-sidebar-collapsed > .container { width: 100vw; }

        #bfTaskSidebar {
          box-shadow: 8px 0 30px rgba(0,0,0,.08);
        }

        body.bf-sidebar-collapsed #bfTaskSidebar {
          width: 64px;
          box-shadow: none;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function createSidebar() {
    const aside = document.createElement("aside");
    aside.id = "bfTaskSidebar";
    aside.setAttribute("aria-label", "研究任务历史");
    aside.innerHTML = `
      <div class="bf-sidebar-top">
        <div class="bf-brand">BettaFish</div>
        <button class="bf-icon-btn" id="bfHistorySearchButton" type="button" title="搜索任务" aria-label="搜索任务">${icons.search}</button>
        <button class="bf-icon-btn" id="bfSidebarToggle" type="button" title="收起侧边栏" aria-label="收起侧边栏">${icons.panel}</button>
      </div>
      <button class="bf-new-task" id="bfNewTaskButton" type="button" title="新研究">
        ${icons.compose}
        <span class="bf-new-task-label">新研究</span>
      </button>
      <div class="bf-search-box" id="bfTaskSearchBox">
        <input id="bfTaskSearchInput" type="search" placeholder="搜索历史任务" autocomplete="off">
      </div>
      <div class="bf-task-scroll" id="bfTaskList"></div>
    `;
    document.body.insertBefore(aside, document.body.firstChild);
  }

  function setCollapsed(collapsed) {
    document.body.classList.toggle("bf-sidebar-collapsed", collapsed);
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    const toggle = document.getElementById("bfSidebarToggle");
    if (toggle) {
      toggle.title = collapsed ? "展开侧边栏" : "收起侧边栏";
      toggle.setAttribute("aria-label", toggle.title);
    }
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function dayGroup(timestamp) {
    const d = new Date(Number(timestamp || 0) * 1000);
    if (Number.isNaN(d.getTime())) return "更早";
    const now = new Date();
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startYesterday = new Date(startToday.getTime() - 86400000);
    if (d >= startToday) return "今天";
    if (d >= startYesterday) return "昨天";
    return "更早";
  }

  function renderTasks() {
    const list = document.getElementById("bfTaskList");
    if (!list) return;

    const current = ctx() ? ctx().activeTaskId() : "";
    const filtered = tasks.filter(task => {
      if (!searchTerm) return true;
      return String(task.query || "").toLowerCase().includes(searchTerm);
    });

    if (!filtered.length) {
      list.innerHTML = '<div class="bf-empty">暂无历史任务。点击“新研究”开始一次新的分析。</div>';
      return;
    }

    const order = ["今天", "昨天", "更早"];
    const groups = new Map(order.map(name => [name, []]));
    filtered.forEach(task => groups.get(dayGroup(task.created_at)).push(task));

    list.innerHTML = order.map(groupName => {
      const items = groups.get(groupName);
      if (!items.length) return "";
      return `
        <section class="bf-task-group">
          <div class="bf-task-group-title">${groupName}</div>
          ${items.map(task => {
            const status = ["completed", "failed"].includes(task.status) ? task.status : "running";
            const title = String(task.query || "未命名研究").trim() || "未命名研究";
            return `
              <button class="bf-task-row ${task.task_id === current ? "active" : ""}"
                      type="button"
                      data-task-id="${escapeHtml(task.task_id)}"
                      title="${escapeHtml(title)}">
                <span class="bf-task-title">${escapeHtml(title)}</span>
                <span class="bf-task-status ${status}" aria-label="${status}"></span>
              </button>
            `;
          }).join("")}
        </section>
      `;
    }).join("");

    list.querySelectorAll(".bf-task-row").forEach(row => {
      row.addEventListener("click", () => switchTask(row.dataset.taskId));
    });
  }

  async function loadTasks() {
    if (!ctx()) return;
    try {
      const response = await fetch("/api/tasks");
      const data = await response.json();
      if (!data.success) return;
      tasks = Array.isArray(data.tasks) ? data.tasks : [];
      renderTasks();
    } catch (error) {
      console.warn("[TaskSidebar] 加载任务历史失败:", error);
    }
  }

  async function fetchTask(taskId) {
    const response = await fetch("/api/tasks/" + encodeURIComponent(taskId));
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || "任务读取失败");
    }
    return data.task;
  }

  async function switchTask(taskId, options = {}) {
    if (!ctx() || !taskId) return;
    try {
      await ctx().registerTask(taskId, "");
      const task = await fetchTask(taskId);
      ctx().rememberTask(taskId, task.query || "", {
        updateUrl: options.updateUrl !== false,
        dispatch: true
      });
      window.dispatchEvent(new CustomEvent("bettafish:task-selected", {
        detail: task
      }));
      renderTasks();
      if (window.innerWidth <= 860) setCollapsed(true);
    } catch (error) {
      console.error("[TaskSidebar] 切换任务失败:", error);
    }
  }

  function newTask() {
    if (ctx()) ctx().clearActiveTask();
    const url = new URL(window.location.href);
    url.searchParams.delete("task_id");
    window.location.assign(url.pathname + url.search + url.hash);
  }

  function bindEvents() {
    document.getElementById("bfSidebarToggle").addEventListener("click", () => {
      setCollapsed(!document.body.classList.contains("bf-sidebar-collapsed"));
    });

    document.getElementById("bfNewTaskButton").addEventListener("click", newTask);

    const searchButton = document.getElementById("bfHistorySearchButton");
    const searchBox = document.getElementById("bfTaskSearchBox");
    const searchInput = document.getElementById("bfTaskSearchInput");
    searchButton.addEventListener("click", () => {
      searchBox.classList.toggle("visible");
      if (searchBox.classList.contains("visible")) {
        requestAnimationFrame(() => searchInput.focus());
      } else {
        searchInput.value = "";
        searchTerm = "";
        renderTasks();
      }
    });
    searchInput.addEventListener("input", () => {
      searchTerm = searchInput.value.trim().toLowerCase();
      renderTasks();
    });

    window.addEventListener("bettafish:task-changed", () => {
      renderTasks();
      setTimeout(loadTasks, 150);
      setTimeout(loadTasks, 1200);
    });

    window.addEventListener("bettafish:task-status-changed", loadTasks);
  }

  async function init() {
    if (!ctx()) return;
    createStyles();
    createSidebar();
    document.body.classList.add("bf-sidebar-ready");

    const storedCollapsed = localStorage.getItem(COLLAPSED_KEY) === "1";
    setCollapsed(window.innerWidth <= 860 ? true : storedCollapsed);
    bindEvents();
    await loadTasks();

    const active = ctx().activeTaskId();
    if (active) {
      try {
        await switchTask(active, { updateUrl: true });
      } catch (_) {}
    }

    refreshTimer = window.setInterval(loadTasks, REFRESH_MS);
    window.addEventListener("beforeunload", () => {
      if (refreshTimer) window.clearInterval(refreshTimer);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }

  window.BettaFishTaskSidebar = Object.freeze({
    loadTasks,
    switchTask,
    renderTasks,
    setCollapsed
  });
})();
