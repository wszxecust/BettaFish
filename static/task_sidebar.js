(() => {
  "use strict";

  const COLLAPSED_KEY = "bettafish.sidebarCollapsed";
  const SEEN_RESULTS_PREFIX = "bettafish.seenTaskResults.";
  const REFRESH_MS = 4000;
  let tasks = [];
  let searchTerm = "";
  let refreshTimer = null;
  let menuTaskId = "";

  const icons = {
    panel: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"></rect><path d="M9 4v16"></path></svg>',
    compose: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z"></path></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path></svg>',
    more: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.35" fill="currentColor" stroke="none"></circle><circle cx="12" cy="12" r="1.35" fill="currentColor" stroke="none"></circle><circle cx="19" cy="12" r="1.35" fill="currentColor" stroke="none"></circle></svg>',
    rename: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z"></path></svg>',
    trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path><path d="M10 10v6M14 10v6"></path></svg>',
    key: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8.5" cy="8.5" r="4.5"></circle><path d="M12 12l8 8"></path><path d="M17 17l2-2"></path><path d="M15 15l2-2"></path></svg>'
  };

  function ctx() {
    return window.BettaFishTaskContext;
  }

  function seenResultsKey() {
    const taskContext = ctx();
    const workspaceId = taskContext ? taskContext.getWorkspaceId() : "default";
    return SEEN_RESULTS_PREFIX + workspaceId;
  }

  function loadSeenResults() {
    try {
      const raw = localStorage.getItem(seenResultsKey());
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function hasSeenResult(taskId) {
    const seen = loadSeenResults();
    return Boolean(seen[taskId]);
  }

  function markResultSeen(task) {
    if (!task || !["completed", "failed"].includes(task.status)) return;
    const seen = loadSeenResults();
    seen[task.task_id] = task.status;
    try {
      localStorage.setItem(seenResultsKey(), JSON.stringify(seen));
    } catch (_) {}
  }

  function forgetSeenResult(taskId) {
    const seen = loadSeenResults();
    if (!seen[taskId]) return;
    delete seen[taskId];
    try {
      localStorage.setItem(seenResultsKey(), JSON.stringify(seen));
    } catch (_) {}
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
      .bf-new-task,
      .bf-api-key {
        border: 0;
        background: transparent;
        color: #111;
        cursor: pointer;
        border-radius: 12px;
        transition: background .14s ease;
      }

      .bf-icon-btn:hover,
      .bf-new-task:hover,
      .bf-api-key:hover { background: #ececec; }

      .bf-icon-btn {
        width: 44px;
        height: 44px;
        display: grid;
        place-items: center;
        flex: 0 0 44px;
      }

      .bf-icon-btn svg,
      .bf-new-task svg,
      .bf-api-key svg {
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

      .bf-sidebar-footer {
        flex: 0 0 auto;
        margin-top: auto;
        padding-top: 8px;
        border-top: 1px solid #e5e5e5;
      }

      .bf-api-key {
        width: 100%;
        min-height: 46px;
        border: 0;
        border-radius: 12px;
        background: transparent;
        color: #111;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 0 12px;
        font-size: 15px;
        text-align: left;
        transition: background .14s ease;
      }

      .bf-api-key:hover { background: #ececec; }

      .bf-api-key svg {
        width: 23px;
        height: 23px;
        flex: 0 0 23px;
        fill: none;
        stroke: currentColor;
        stroke-width: 1.9;
        stroke-linecap: round;
        stroke-linejoin: round;
      }

      body.bf-sidebar-ready .search-title,
      body.bf-sidebar-ready #openConfigButton {
        display: none !important;
      }

      body.bf-sidebar-ready .search-section {
        flex: 0 0 auto;
        border-top: 2px solid #000;
        border-bottom: 0 !important;
        padding: 14px 20px 12px;
        background: #fff;
      }

      body.bf-sidebar-ready .search-row {
        margin: 0 auto;
        max-width: 950px;
      }

      body.bf-sidebar-ready .main-content {
        height: auto !important;
        flex: 1 1 auto;
        min-height: 0;
      }

      .config-modal-overlay { z-index: 1600 !important; }

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

      .bf-task-item {
        position: relative;
        width: 100%;
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
        box-sizing: border-box;
        transition: background .14s ease, padding-right .14s ease;
      }

      .bf-task-item:hover .bf-task-row,
      .bf-task-item.menu-open .bf-task-row {
        background: #ececec;
        padding-right: 44px;
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

      .bf-task-more {
        position: absolute;
        right: 6px;
        top: 50%;
        transform: translateY(-50%);
        width: 32px;
        height: 32px;
        border: 0;
        border-radius: 8px;
        background: transparent;
        color: #555;
        display: grid;
        place-items: center;
        cursor: pointer;
        opacity: 0;
        pointer-events: none;
        transition: opacity .12s ease, background .12s ease;
      }

      .bf-task-more svg {
        width: 19px;
        height: 19px;
      }

      .bf-task-item:hover .bf-task-more,
      .bf-task-item.menu-open .bf-task-more {
        opacity: 1;
        pointer-events: auto;
      }

      .bf-task-more:hover { background: #dedede; }

      .bf-task-menu {
        position: fixed;
        z-index: 1400;
        width: 158px;
        padding: 6px;
        border: 1px solid #e3e3e3;
        border-radius: 12px;
        background: #fff;
        box-shadow: 0 12px 32px rgba(0,0,0,.14);
      }

      .bf-task-menu[hidden] { display: none; }

      .bf-task-menu-item {
        width: 100%;
        height: 40px;
        border: 0;
        border-radius: 8px;
        background: transparent;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 0 10px;
        color: #222;
        cursor: pointer;
        font-size: 14px;
        text-align: left;
      }

      .bf-task-menu-item:hover { background: #f2f2f2; }
      .bf-task-menu-item.danger { color: #e5484d; }

      .bf-task-menu-item svg {
        width: 19px;
        height: 19px;
        fill: none;
        stroke: currentColor;
        stroke-width: 1.8;
        stroke-linecap: round;
        stroke-linejoin: round;
        flex: 0 0 19px;
      }

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
      body.bf-sidebar-collapsed .bf-api-key-label,
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

      body.bf-sidebar-collapsed .bf-new-task,
      body.bf-sidebar-collapsed .bf-api-key {
        width: 44px;
        height: 44px;
        min-height: 44px;
        padding: 0;
        margin-left: auto;
        margin-right: auto;
        justify-content: center;
      }

      body.bf-sidebar-collapsed .bf-sidebar-footer {
        border-top: 0;
        padding-top: 8px;
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
      <div class="bf-sidebar-footer">
        <button class="bf-api-key" id="bfApiKeyButton" type="button" title="API KEY" aria-label="API KEY">
          ${icons.key}
          <span class="bf-api-key-label">API KEY</span>
        </button>
      </div>
      <div class="bf-task-menu" id="bfTaskMenu" hidden>
        <button class="bf-task-menu-item" type="button" data-action="rename">${icons.rename}<span>重命名</span></button>
        <button class="bf-task-menu-item danger" type="button" data-action="delete">${icons.trash}<span>删除</span></button>
      </div>
    `;
    document.body.insertBefore(aside, document.body.firstChild);
  }

  function arrangeMainLayout() {
    const container = document.querySelector(".container");
    const searchSection = document.querySelector(".search-section");
    const mainContent = document.querySelector(".main-content");
    const statusBar = document.querySelector(".status-bar");
    if (!container || !searchSection || !mainContent) return;

    const title = searchSection.querySelector(".search-title");
    if (title) title.remove();

    const legacyConfigButton = document.getElementById("openConfigButton");
    if (legacyConfigButton) {
      legacyConfigButton.style.display = "none";
      legacyConfigButton.setAttribute("aria-hidden", "true");
      legacyConfigButton.tabIndex = -1;
    }

    const modalTitle = document.querySelector(".config-modal-title");
    if (modalTitle) modalTitle.textContent = "API KEY";

    // Main workspace first, research input docked at the bottom, status strip last.
    if (statusBar && searchSection.nextElementSibling !== statusBar) {
      container.insertBefore(searchSection, statusBar);
    } else if (!statusBar) {
      container.appendChild(searchSection);
    }
  }

  function openApiKeyConfig() {
    const legacyButton = document.getElementById("openConfigButton");
    if (legacyButton) {
      legacyButton.click();
      return;
    }
    if (typeof window.openConfigModal === "function") {
      window.openConfigModal();
    }
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
      const haystack = `${task.title || ""} ${task.query || ""}`.toLowerCase();
      return haystack.includes(searchTerm);
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
            const isTerminal = ["completed", "failed"].includes(status);
            const showStatus = status === "running" || (isTerminal && !hasSeenResult(task.task_id));
            const title = String(task.title || task.query || "未命名研究").trim() || "未命名研究";
            return `
              <div class="bf-task-item" data-task-id="${escapeHtml(task.task_id)}">
                <button class="bf-task-row ${task.task_id === current ? "active" : ""}"
                        type="button"
                        data-task-id="${escapeHtml(task.task_id)}"
                        title="${escapeHtml(title)}">
                  <span class="bf-task-title">${escapeHtml(title)}</span>
                  ${showStatus ? `<span class="bf-task-status ${status}" aria-label="${status}"></span>` : ""}
                </button>
                <button class="bf-task-more"
                        type="button"
                        data-task-id="${escapeHtml(task.task_id)}"
                        title="更多"
                        aria-label="更多">${icons.more}</button>
              </div>
            `;
          }).join("")}
        </section>
      `;
    }).join("");

    list.querySelectorAll(".bf-task-row").forEach(row => {
      row.addEventListener("click", () => switchTask(row.dataset.taskId));
    });
    list.querySelectorAll(".bf-task-more").forEach(button => {
      button.addEventListener("click", event => {
        event.stopPropagation();
        openTaskMenu(button.dataset.taskId, button);
      });
    });
  }

  function closeTaskMenu() {
    const menu = document.getElementById("bfTaskMenu");
    if (menu) menu.hidden = true;
    document.querySelectorAll(".bf-task-item.menu-open").forEach(item => {
      item.classList.remove("menu-open");
    });
    menuTaskId = "";
  }

  function openTaskMenu(taskId, anchor) {
    const menu = document.getElementById("bfTaskMenu");
    if (!menu || !anchor) return;
    closeTaskMenu();
    menuTaskId = taskId;
    const item = anchor.closest(".bf-task-item");
    if (item) item.classList.add("menu-open");
    menu.hidden = false;

    const rect = anchor.getBoundingClientRect();
    const width = 158;
    const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    const top = Math.min(rect.bottom + 6, window.innerHeight - 98);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  async function renameTask(taskId) {
    const task = tasks.find(item => item.task_id === taskId);
    if (!task) return;
    const currentTitle = String(task.title || task.query || "").trim();
    const nextTitle = window.prompt("重命名研究", currentTitle);
    if (nextTitle == null) return;
    const title = nextTitle.trim();
    if (!title || title === currentTitle) return;

    const response = await fetch("/api/tasks/" + encodeURIComponent(taskId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title })
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      window.alert(data.message || "重命名失败");
      return;
    }

    tasks = tasks.map(item => item.task_id === taskId ? data.task : item);
    renderTasks();
  }

  async function deleteTask(taskId) {
    const task = tasks.find(item => item.task_id === taskId);
    if (!task) return;
    const title = String(task.title || task.query || "该任务").trim() || "该任务";
    if (!window.confirm(`确定删除“${title}”吗？\n删除后该任务的历史日志、输出和报告将无法恢复。`)) {
      return;
    }

    const response = await fetch("/api/tasks/" + encodeURIComponent(taskId), {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      window.alert(data.message || "删除失败");
      return;
    }

    const wasActive = ctx() && ctx().activeTaskId() === taskId;
    tasks = tasks.filter(item => item.task_id !== taskId);
    forgetSeenResult(taskId);
    closeTaskMenu();
    renderTasks();

    if (wasActive) {
      newTask();
    }
  }

  async function loadTasks() {
    if (!ctx()) return;
    try {
      const response = await fetch("/api/tasks");
      const data = await response.json();
      if (!data.success) return;
      tasks = Array.isArray(data.tasks) ? data.tasks : [];
      const activeTaskId = ctx().activeTaskId();
      const activeTask = tasks.find(task => task.task_id === activeTaskId);
      if (activeTask) markResultSeen(activeTask);
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

  function subscribeTask(taskId, previousTaskId = "") {
    if (!taskId) return;
    try {
      if (typeof socket !== "undefined" && socket && socket.connected) {
        socket.emit("subscribe_task", {
          task_id: taskId,
          previous_task_id: previousTaskId || ""
        });
      }
    } catch (error) {
      console.warn("[TaskSidebar] 订阅task实时流失败:", error);
    }
  }

  function resetTaskViewState() {
    try {
      if (typeof lastLineCount !== "undefined") lastLineCount = {};
      if (typeof forumLogLineCount !== "undefined") forumLogLineCount = 0;
      if (typeof forumLogPosition !== "undefined") forumLogPosition = 0;
      if (typeof forumMessagesCache !== "undefined") forumMessagesCache = [];
      const chat = document.getElementById("forumChatArea");
      if (chat) chat.innerHTML = "";

      if (typeof consoleLayerApps !== "undefined" &&
          typeof clearConsoleLayer === "function") {
        consoleLayerApps.forEach(app => {
          clearConsoleLayer(app, "[系统] 正在恢复该任务的历史数据...");
        });
      }

      if (typeof safeCloseReportStream === "function") safeCloseReportStream(true);
      if (typeof stopProgressPolling === "function") stopProgressPolling();
      if (typeof reportLogManager !== "undefined" && reportLogManager) {
        reportLogManager.stop();
        reportLogManager.reset();
      }
      if (typeof reportTaskId !== "undefined") reportTaskId = null;
      if (typeof reportAutoPreviewLoaded !== "undefined") reportAutoPreviewLoaded = false;
      if (typeof lastCompletedReportTask !== "undefined") lastCompletedReportTask = null;
      if (typeof autoGenerateTriggered !== "undefined") autoGenerateTriggered = false;
    } catch (error) {
      console.warn("[TaskSidebar] 重置task视图状态失败:", error);
    }
  }

  function restoreAgentIframes(task) {
    const taskContext = ctx();
    if (!taskContext || !task || !task.task_id) return;

    const query = String(task.query || "");
    const ports = { insight: 8501, media: 8502, query: 8503 };
    for (const [app, port] of Object.entries(ports)) {
      try {
        if (typeof unloadIframe === "function") unloadIframe(app);
        if (!query || typeof lazyLoadIframe !== "function") continue;

        const iframe = lazyLoadIframe(app);
        if (!iframe) continue;
        const params = new URLSearchParams({
          query,
          auto_search: "false",
          view_only: "true",
          task_id: task.task_id,
          client_id: taskContext.getClientId(),
          workspace_id: taskContext.getWorkspaceId()
        });
        iframe.src = `http://${window.location.hostname}:${port}?${params.toString()}`;
      } catch (error) {
        console.warn(`[TaskSidebar] 恢复 ${app} 页面失败:`, error);
      }
    }
  }

  function restoreTaskData(task) {
    if (!task || !task.task_id) return;

    const searchInput = document.getElementById("searchInput");
    if (searchInput) searchInput.value = task.query || "";

    resetTaskViewState();
    restoreAgentIframes(task);

    try {
      if (typeof loadConsoleOutput === "function") {
        ["insight", "media", "query"].forEach(app => loadConsoleOutput(app));
      }
      if (typeof refreshForumMessages === "function") refreshForumMessages();
      if (typeof loadReportInterface === "function") loadReportInterface();
      if (typeof checkReportLockStatus === "function") checkReportLockStatus();
      if (typeof updateEmbeddedPage === "function" && typeof currentApp !== "undefined") {
        updateEmbeddedPage(currentApp);
      }
    } catch (error) {
      console.warn("[TaskSidebar] 恢复task历史数据失败:", error);
    }

    // Report界面的status接口返回该research task最近一次Report任务。
    // 已完成时直接恢复预览，而不是重新生成。
    window.setTimeout(async () => {
      try {
        const response = await fetch("/api/report/status");
        const data = await response.json();
        const reportTask = data && data.current_task;
        if (data && data.success && reportTask && reportTask.status === "completed" &&
            typeof viewReport === "function") {
          viewReport(reportTask.task_id);
        }
      } catch (_) {}
    }, 300);

    window.setTimeout(() => {
      try {
        if (typeof loadConsoleOutput === "function") {
          ["insight", "media", "query"].forEach(app => loadConsoleOutput(app));
        }
        if (typeof refreshForumMessages === "function") refreshForumMessages();
      } catch (_) {}
    }, 500);
  }

  async function switchTask(taskId, options = {}) {
    if (!ctx() || !taskId) return;
    try {
      const previousTaskId = ctx().activeTaskId();
      await ctx().registerTask(taskId, "");
      const task = await fetchTask(taskId);
      markResultSeen(task);
      ctx().rememberTask(taskId, task.query || "", {
        updateUrl: options.updateUrl !== false,
        dispatch: false
      });
      subscribeTask(taskId, previousTaskId);
      restoreTaskData(task);
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
    document.getElementById("bfApiKeyButton").addEventListener("click", openApiKeyConfig);

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

    const taskMenu = document.getElementById("bfTaskMenu");
    taskMenu.addEventListener("click", async event => {
      const button = event.target.closest(".bf-task-menu-item");
      if (!button || !menuTaskId) return;
      const taskId = menuTaskId;
      closeTaskMenu();
      if (button.dataset.action === "rename") {
        await renameTask(taskId);
      } else if (button.dataset.action === "delete") {
        await deleteTask(taskId);
      }
    });

    document.addEventListener("click", event => {
      if (!event.target.closest("#bfTaskMenu") && !event.target.closest(".bf-task-more")) {
        closeTaskMenu();
      }
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closeTaskMenu();
    });
    window.addEventListener("resize", closeTaskMenu);

    window.addEventListener("bettafish:task-changed", event => {
      const detail = event.detail || {};
      if (detail.taskId) {
        subscribeTask(detail.taskId, detail.previousTaskId || "");
      }
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
    arrangeMainLayout();
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
    setCollapsed,
    restoreTaskData,
    subscribeTask
  });
})();
