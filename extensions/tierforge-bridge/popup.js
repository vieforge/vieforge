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
  document.getElementById("port").value = st.port || 19876;
  document.getElementById("token").value = st.token || "";

  const badge = document.getElementById("statusBadge");
  const text = document.getElementById("statusText");
  const pauseBtn = document.getElementById("pauseBtn");

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
  const log = Array.isArray(st.activityLog) ? st.activityLog : [];
  if (log.length === 0) {
    logEl.innerHTML = '<div class="hint">No jobs run yet.</div>';
  } else {
    logEl.innerHTML = log
      .map((e) => `<div class="log-item">[${e.time}] ${e.service} • ${e.chars} chars • ${e.status}</div>`)
      .join("");
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

refreshUi();
