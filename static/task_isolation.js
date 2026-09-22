(() => {
  "use strict";

  const TASK_KEY = "bettafish.activeTaskId";
  const CLIENT_KEY = "bettafish.clientId";
  const WORKSPACE_KEY = "bettafish.workspaceId";
  const QUERY_KEY = "bettafish.activeTaskQuery";
  const originalFetch = window.fetch ? window.fetch.bind(window) : null;
  let launch = null;

  function randomId(prefix) {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return prefix + "_" + window.crypto.randomUUID().replace(/-/g, "");
    }
    const bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
      return prefix + "_" + Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    }
    return prefix + "_" + Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function getClientId() {
    let id = sessionStorage.getItem(CLIENT_KEY);
    if (!id) {
      id = randomId("client");
      sessionStorage.setItem(CLIENT_KEY, id);
    }
    return id;
  }

  function getWorkspaceId() {
    let id = localStorage.getItem(WORKSPACE_KEY);
    if (!id) {
      id = randomId("workspace");
      localStorage.setItem(WORKSPACE_KEY, id);
    }
    return id;
  }

  function activeTaskId() {
    return sessionStorage.getItem(TASK_KEY) || "";
  }

  function activeQuery() {
    return sessionStorage.getItem(QUERY_KEY) || "";
  }

  function updateTaskUrl(taskId) {
    const url = new URL(window.location.href);
    if (taskId) {
      url.searchParams.set("task_id", taskId);
    } else {
      url.searchParams.delete("task_id");
    }
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  }

  function rememberTask(taskId, query, options = {}) {
    const previousTaskId = activeTaskId();
    if (taskId) {
      sessionStorage.setItem(TASK_KEY, taskId);
    } else {
      sessionStorage.removeItem(TASK_KEY);
    }
    if (query != null && String(query).trim()) {
      sessionStorage.setItem(QUERY_KEY, String(query));
    } else if (!taskId) {
      sessionStorage.removeItem(QUERY_KEY);
    }

    if (options.updateUrl !== false) updateTaskUrl(taskId);
    if (options.dispatch !== false) {
      window.dispatchEvent(new CustomEvent("bettafish:task-changed", {
        detail: {
          taskId: taskId || "",
          previousTaskId,
          query: query || activeQuery(),
          clientId: getClientId(),
          workspaceId: getWorkspaceId()
        }
      }));
    }
  }

  function clearActiveTask() {
    rememberTask("", "", { updateUrl: true, dispatch: true });
  }

  async function registerTask(taskId, query) {
    if (!originalFetch || !taskId) return null;
    try {
      const response = await originalFetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: taskId,
          client_id: getClientId(),
          workspace_id: getWorkspaceId(),
          query: query || ""
        })
      });
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  function beginLaunch(query) {
    const now = Date.now();
    const normalized = String(query || "").trim();
    if (launch && launch.query === normalized && now - launch.createdAt < 5000) {
      return launch.taskId;
    }
    const taskId = randomId("task");
    launch = { taskId, query: normalized, createdAt: now };
    rememberTask(taskId, normalized);
    registerTask(taskId, normalized);
    return taskId;
  }

  function augmentEngineUrl(raw) {
    if (typeof raw !== "string" || !raw) return raw;
    let url;
    try {
      url = new URL(raw, window.location.href);
    } catch (_) {
      return raw;
    }

    const query = url.searchParams.get("query");
    const autoSearch = (url.searchParams.get("auto_search") || "").toLowerCase();
    if (!query || autoSearch !== "true") return raw;

    let taskId = url.searchParams.get("task_id");
    if (!taskId) taskId = beginLaunch(query);

    url.searchParams.set("task_id", taskId);
    url.searchParams.set("client_id", getClientId());
    url.searchParams.set("workspace_id", getWorkspaceId());
    rememberTask(taskId, query);
    registerTask(taskId, query);

    if (/^https?:/i.test(raw)) return url.toString();
    return url.pathname + url.search + url.hash;
  }

  try {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "src");
    if (descriptor && descriptor.get && descriptor.set) {
      Object.defineProperty(HTMLIFrameElement.prototype, "src", {
        configurable: descriptor.configurable,
        enumerable: descriptor.enumerable,
        get: descriptor.get,
        set(value) {
          return descriptor.set.call(this, augmentEngineUrl(String(value)));
        }
      });
    }
  } catch (_) {}

  const originalSetAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(name, value) {
    if (this instanceof HTMLIFrameElement && String(name).toLowerCase() === "src") {
      value = augmentEngineUrl(String(value));
    }
    return originalSetAttribute.call(this, name, value);
  };

  function patchExistingIframes(root = document) {
    if (!root.querySelectorAll) return;
    root.querySelectorAll("iframe[src]").forEach(frame => {
      const current = frame.getAttribute("src");
      const next = augmentEngineUrl(current);
      if (next !== current) originalSetAttribute.call(frame, "src", next);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    patchExistingIframes();
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === "attributes" && record.target instanceof HTMLIFrameElement) {
          const current = record.target.getAttribute("src") || "";
          const next = augmentEngineUrl(current);
          if (next !== current) originalSetAttribute.call(record.target, "src", next);
        }
        for (const node of record.addedNodes || []) {
          if (node.nodeType === 1) patchExistingIframes(node);
        }
      }
    });
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["src"]
    });
  });

  function isTaskSensitivePath(pathname) {
    return pathname === "/api/search" ||
      pathname.startsWith("/api/tasks") ||
      pathname.startsWith("/api/forum/") ||
      pathname.startsWith("/api/output/") ||
      pathname.startsWith("/api/test_log/") ||
      pathname.startsWith("/api/report/");
  }

  if (originalFetch) {
    window.fetch = async function(input, init) {
      let requestUrl;
      try {
        requestUrl = new URL(
          typeof input === "string" ? input : input.url,
          window.location.href
        );
      } catch (_) {
        return originalFetch(input, init);
      }

      if (requestUrl.origin !== window.location.origin ||
          !isTaskSensitivePath(requestUrl.pathname)) {
        return originalFetch(input, init);
      }

      init = Object.assign({}, init || {});
      const method = String(init.method || (input && input.method) || "GET").toUpperCase();
      let taskId = activeTaskId();

      if (requestUrl.pathname === "/api/search" && method !== "GET") {
        try {
          const parsed = JSON.parse(init.body || "{}");
          if (parsed.query) taskId = parsed.task_id || beginLaunch(parsed.query);
        } catch (_) {}
      }

      if (method === "GET" || method === "HEAD") {
        if (!requestUrl.pathname.startsWith("/api/tasks") && taskId) {
          requestUrl.searchParams.set("task_id", taskId);
        }
        requestUrl.searchParams.set("client_id", getClientId());
        requestUrl.searchParams.set("workspace_id", getWorkspaceId());
        return originalFetch(requestUrl.toString(), init);
      }

      const contentType = new Headers(init.headers || {}).get("Content-Type") || "";
      if (!contentType || contentType.includes("application/json")) {
        let body = {};
        try { body = JSON.parse(init.body || "{}"); } catch (_) {}
        if (taskId && !body.task_id && !requestUrl.pathname.startsWith("/api/tasks")) {
          body.task_id = taskId;
        }
        if (taskId && requestUrl.pathname === "/api/report/generate" && !body.research_task_id) {
          body.research_task_id = taskId;
        }
        if (!body.client_id) body.client_id = getClientId();
        if (!body.workspace_id) body.workspace_id = getWorkspaceId();
        init.headers = Object.assign({}, init.headers || {}, {
          "Content-Type": "application/json"
        });
        init.body = JSON.stringify(body);
      }
      return originalFetch(requestUrl.toString(), init);
    };
  }

  const clientId = getClientId();
  const workspaceId = getWorkspaceId();

  function wrapIoFactory(factory) {
    if (typeof factory !== "function" || factory.__bettafishWrapped) return factory;
    function wrappedIo(...args) {
      const optionsIndex = (typeof args[0] === "string") ? 1 : 0;
      const options = Object.assign({}, args[optionsIndex] || {});
      options.auth = Object.assign({}, options.auth || {}, {
        client_id: clientId,
        workspace_id: workspaceId
      });
      options.query = Object.assign({}, options.query || {}, {
        client_id: clientId,
        workspace_id: workspaceId
      });
      args[optionsIndex] = options;
      return factory.apply(this, args);
    }
    Object.assign(wrappedIo, factory);
    wrappedIo.__bettafishWrapped = true;
    return wrappedIo;
  }

  try {
    if (typeof window.io === "function") {
      window.io = wrapIoFactory(window.io);
    } else {
      let storedIo;
      Object.defineProperty(window, "io", {
        configurable: true,
        enumerable: true,
        get() { return storedIo; },
        set(value) { storedIo = wrapIoFactory(value); }
      });
    }
  } catch (_) {}

  window.BettaFishTaskContext = Object.freeze({
    getClientId,
    getWorkspaceId,
    activeTaskId,
    activeQuery,
    rememberTask,
    clearActiveTask,
    registerTask,
    beginLaunch,
    augmentEngineUrl,
    randomId
  });

  const sharedTaskId = new URL(window.location.href).searchParams.get("task_id");
  if (sharedTaskId) {
    rememberTask(sharedTaskId, "", { updateUrl: false, dispatch: false });
    registerTask(sharedTaskId, "");
  }
})();
