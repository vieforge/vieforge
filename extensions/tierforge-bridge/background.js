// TierForge AI Bridge — Manifest V3 Background Worker
// Works on Chrome, Coc Coc, Edge, Brave, Opera, Vivaldi, and Firefox MV3.

const DEFAULT_CONFIG = {
  enabled: true,
  port: 19876,
  token: "",
};

const SERVICE_URLS = {
  gemini: "https://gemini.google.com/app",
  chatgpt: "https://chatgpt.com/",
  claude: "https://claude.ai/",
  flow: "https://labs.google/fx/tools/flow",
};

const SERVICE_MATCHERS = {
  gemini: (u) => u.includes("gemini.google.com"),
  chatgpt: (u) => u.includes("chatgpt.com") || u.includes("chat.openai.com"),
  claude: (u) => u.includes("claude.ai"),
  flow: (u) => u.includes("flow.google.com") || u.includes("labs.google"),
};

let isBusy = false;
let clientId = "";

function detectBrowserName() {
  const ua = navigator.userAgent || "";
  if (ua.includes("coc_coc_browser")) return "coccoc";
  if (ua.includes("Edg/")) return "edge";
  if (ua.includes("OPR/")) return "opera";
  if (ua.includes("Firefox/")) return "firefox";
  if (ua.includes("Brave")) return "brave";
  return "chrome";
}

async function getConfig() {
  const stored = await chrome.storage.local.get(["enabled", "port", "token", "clientId"]);
  if (!stored.clientId) {
    clientId = `${detectBrowserName()}_${Math.random().toString(36).slice(2, 10)}`;
    await chrome.storage.local.set({ clientId });
  } else {
    clientId = stored.clientId;
  }
  return {
    enabled: stored.enabled !== undefined ? Boolean(stored.enabled) : DEFAULT_CONFIG.enabled,
    port: Number(stored.port || DEFAULT_CONFIG.port),
    token: String(stored.token || ""),
    clientId,
  };
}

async function listAiTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs
    .map((t) => String(t.url || ""))
    .filter((u) =>
      u.includes("gemini.google.com") ||
      u.includes("chatgpt.com") ||
      u.includes("claude.ai") ||
      u.includes("labs.google") ||
      u.includes("flow.google.com")
    );
}

async function findOrOpenServiceTab(service) {
  const matcher = SERVICE_MATCHERS[service] || SERVICE_MATCHERS.gemini;
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((t) => matcher(String(t.url || "")));
  if (existing && existing.id) {
    return existing;
  }
  const url = SERVICE_URLS[service] || SERVICE_URLS.gemini;
  const created = await chrome.tabs.create({ url, active: false });
  await new Promise((resolve) => {
    const listener = (tabId, info) => {
      if (tabId === created.id && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, 12000);
  });
  return created;
}

async function setBadge(text, color) {
  try {
    await chrome.action.setBadgeText({ text });
    if (color) {
      await chrome.action.setBadgeBackgroundColor({ color });
    }
  } catch (_err) {
    // Ignore on browsers without badge API support
  }
}

async function appendActivity(service, chars, status) {
  const stored = await chrome.storage.local.get(["activityLog"]);
  const log = Array.isArray(stored.activityLog) ? stored.activityLog : [];
  const time = new Date().toTimeString().slice(0, 8);
  log.unshift({ time, service, chars, status });
  await chrome.storage.local.set({ activityLog: log.slice(0, 10) });
}

async function executeJob(cfg, job) {
  isBusy = true;
  await setBadge("RUN", "#ea580c");
  const svc = job.service || "gemini";
  const promptLen = String(job.prompt || "").length;
  try {
    const tab = await findOrOpenServiceTab(svc);
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content_bridge.js"],
    });
    const res = await chrome.tabs.sendMessage(tab.id, {
      type: "TIERFORGE_RUN_PROMPT",
      service: svc,
      prompt: job.prompt,
    });
    const ok = Boolean(res && res.ok);
    await appendActivity(svc, promptLen, ok ? "OK" : "ERR");
    await postResult(cfg, {
      job_id: job.job_id,
      ok,
      response_text: (res && res.response_text) || "",
      error: (res && res.error) || "",
    });
  } catch (err) {
    await appendActivity(svc, promptLen, "ERR");
    await postResult(cfg, {
      job_id: job.job_id,
      ok: false,
      response_text: "",
      error: String(err && err.message ? err.message : err),
    });
  } finally {
    isBusy = false;
    await setBadge("ON", "#16a34a");
  }
}

async function postResult(cfg, payload) {
  const headers = { "Content-Type": "application/json" };
  if (cfg.token) headers["X-TierForge-Token"] = cfg.token;
  await fetch(`http://127.0.0.1:${cfg.port}/extension/result`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
}

async function tryAutoPair(port) {
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/extension/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ browser: detectBrowserName() }),
    });
    if (!resp.ok) return "";
    const data = await resp.json();
    if (data && data.ok && typeof data.token === "string") {
      await chrome.storage.local.set({ token: data.token, autoPaired: true });
      return data.token;
    }
  } catch (_err) {
    // Bridge offline; will retry automatically on next tick
  }
  return "";
}

async function heartbeatTick() {
  const cfg = await getConfig();
  if (!cfg.enabled) {
    await setBadge("OFF", "#475569");
    await chrome.storage.local.set({ lastStatus: "Paused by user", lastConnected: false });
    return;
  }
  if (isBusy) return;
  try {
    // Zero-config: auto-pair silently if token is empty
    let activeToken = cfg.token;
    if (!activeToken) {
      activeToken = await tryAutoPair(cfg.port);
    }

    const tabs = await listAiTabs();
    const buildHeaders = (tok) => {
      const h = { "Content-Type": "application/json" };
      if (tok) h["X-TierForge-Token"] = tok;
      return h;
    };

    let resp = await fetch(`http://127.0.0.1:${cfg.port}/extension/heartbeat`, {
      method: "POST",
      headers: buildHeaders(activeToken),
      body: JSON.stringify({
        client_id: cfg.clientId,
        browser: detectBrowserName(),
        tabs,
      }),
    });

    // If token rotated or stale (HTTP 401), re-pair automatically once without user action
    if (resp.status === 401) {
      const freshToken = await tryAutoPair(cfg.port);
      if (freshToken) {
        activeToken = freshToken;
        cfg.token = freshToken;
        resp = await fetch(`http://127.0.0.1:${cfg.port}/extension/heartbeat`, {
          method: "POST",
          headers: buildHeaders(activeToken),
          body: JSON.stringify({
            client_id: cfg.clientId,
            browser: detectBrowserName(),
            tabs,
          }),
        });
      }
    }

    if (!resp.ok) {
      await setBadge("ERR", "#dc2626");
      await chrome.storage.local.set({ lastStatus: `HTTP ${resp.status}`, lastConnected: false });
      return;
    }
    const data = await resp.json();
    await setBadge("ON", "#16a34a");
    await chrome.storage.local.set({
      lastStatus: "Connected (Auto-Paired ✓)",
      lastConnected: true,
      lastHeartbeatAt: Date.now(),
    });
    if (data && data.job) {
      cfg.token = activeToken;
      await executeJob(cfg, data.job);
    }
  } catch (_err) {
    await setBadge("", "#475569");
    await chrome.storage.local.set({ lastStatus: "Offline (Waiting for local bridge...)", lastConnected: false });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("tierforge_poll", { periodInMinutes: 0.5 });
  heartbeatTick();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "tierforge_poll") {
    heartbeatTick();
  }
});

// High-frequency local loop while service worker is awake (every 2s)
setInterval(() => {
  heartbeatTick();
}, 2000);

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "TIERFORGE_PING_NOW") {
    heartbeatTick().then(() => sendResponse({ ok: true }));
    return true;
  }
  return false;
});
