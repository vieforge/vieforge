async function refreshUi() {
  const st = await chrome.storage.local.get([
    "enabled",
    "port",
    "token",
    "lastStatus",
    "lastConnected",
    "activityLog",
  ]);
  const enabled = st.enabled !== undefined ? Boolean(st.enabled) : true;
  const activePort = Number(st.port || 19876);
  document.getElementById("port").value = activePort;
  document.getElementById("token").value = st.token || "";

  const badge = document.getElementById("statusBadge");
  const text = document.getElementById("statusText");
  const pauseBtn = document.getElementById("pauseBtn");
  const portWarn = document.getElementById("portWarn");

  if (portWarn) {
    portWarn.style.display = activePort !== 19876 ? "block" : "none";
  }

  if (!enabled) {
    badge.textContent = "Paused";
    badge.className = "badge off";
    pauseBtn.textContent = "Resume Bridge";
    pauseBtn.className = "resume";
  } else if (st.lastConnected) {
    badge.textContent = "Connected";
    badge.className = "badge ok";
    pauseBtn.textContent = "Pause Bridge";
    pauseBtn.className = "danger";
  } else {
    badge.textContent = "Offline";
    badge.className = "badge off";
    pauseBtn.textContent = "Pause Bridge";
    pauseBtn.className = "danger";
  }

  if (st.lastStatus) {
    text.textContent = st.lastStatus;
  }

  const logEl = document.getElementById("activityLog");
  logEl.textContent = "";
  const log = Array.isArray(st.activityLog) ? st.activityLog : [];
  if (log.length === 0) {
    const emptyDiv = document.createElement("div");
    emptyDiv.className = "hint";
    emptyDiv.textContent = "No jobs run yet.";
    logEl.appendChild(emptyDiv);
  } else {
    for (const e of log) {
      const item = document.createElement("div");
      item.className = "log-item";
      item.textContent = `[${e.time || ""}] ${e.service || "ai"} • ${Number(e.chars || 0)} chars • ${e.status || ""}`;
      logEl.appendChild(item);
    }
  }
}

document.getElementById("pauseBtn").addEventListener("click", async () => {
  const st = await chrome.storage.local.get(["enabled"]);
  const cur = st.enabled !== undefined ? Boolean(st.enabled) : true;
  await chrome.storage.local.set({ enabled: !cur });
  await chrome.runtime.sendMessage({ type: "TIERFORGE_PING_NOW" });
  setTimeout(refreshUi, 250);
});

document.getElementById("saveBtn").addEventListener("click", async () => {
  await chrome.storage.local.set({
    enabled: true,
    port: Number(document.getElementById("port").value || 19876),
    token: document.getElementById("token").value.trim(),
  });
  await chrome.runtime.sendMessage({ type: "TIERFORGE_PING_NOW" });
  setTimeout(refreshUi, 400);
});

const setupBtn = document.getElementById("setupBtn");
if (setupBtn) {
  setupBtn.addEventListener("click", async () => {
    const st = await chrome.storage.local.get(["port"]);
    const port = Number(st.port || 19876);
    chrome.tabs.create({ url: `http://127.0.0.1:${port}/setup` });
  });
}

refreshUi();
