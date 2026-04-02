/**
 * app.js — Frontend logic
 * Mengshan Li | CS6620 Group 9
 *
 * API_BASE_URL is replaced at deploy time by 04_upload_frontend.sh
 * using: sed -i "s|__LAMBDA_URL__|$LAMBDA_URL|g" app.js
 */

const API_BASE_URL = "__LAMBDA_URL__";

const POLL_INITIAL_MS  = 2000;
const POLL_BACKOFF     = 1.5;
const POLL_MAX_MS      = 30000;
const POLL_TIMEOUT_MS  = 5 * 60 * 1000;
const EXPIRY_WARN_SECS = 300;

const LS_API_KEY = "sasc_api_key";

let _currentScanId   = null;
let _pollTimer       = null;
let _pollDeadline    = null;
let _currentInterval = POLL_INITIAL_MS;
let _reportUrl       = null;

// ── View switching ────────────────────────────────────────────────────────────

function switchView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));

  const view = document.getElementById(`view-${name}`);
  if (view) view.classList.add("active");

  const navItem = document.querySelector(`.nav-item[data-view="${name}"]`);
  if (navItem) navItem.classList.add("active");

  const title = document.getElementById("topbar-title");
  if (title) title.textContent = name.charAt(0).toUpperCase() + name.slice(1);
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  const stored = localStorage.getItem(LS_API_KEY);
  if (stored) document.getElementById("api-key").value = stored;
});

// ── Submit ────────────────────────────────────────────────────────────────────

async function handleSubmit() {
  const apiKey   = document.getElementById("api-key").value.trim();
  const language = document.getElementById("language").value;
  const code     = document.getElementById("code").value.trim();

  dismissError();

  if (!apiKey)   { showError("Please enter your API key in the sidebar."); return; }
  if (!language) { showError("Please select a language."); return; }
  if (!code)     { showError("Please paste some code to scan."); return; }

  setSubmitLoading(true);
  resetStatus();

  let data;
  try {
    const res = await fetch(`${API_BASE_URL}/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-student-key": apiKey },
      body: JSON.stringify({ code, language }),
    });

    data = await res.json().catch(() => ({}));

    if (res.status === 202) {
      localStorage.setItem(LS_API_KEY, apiKey);
      startPolling(data.scan_id, apiKey);
      return;
    }

    if (res.status === 400)      showError(data.error || "Invalid request.");
    else if (res.status === 401) handleUnauthorized();
    else if (res.status === 429) showError("Too many requests — please wait.");
    else                         showError("Server error. Please try again.");

  } catch (_) {
    showError("Could not reach server. Check your connection.");
  }

  setSubmitLoading(false);
}

// ── Polling ───────────────────────────────────────────────────────────────────

function startPolling(scanId, apiKey) {
  _currentScanId   = scanId;
  _currentInterval = POLL_INITIAL_MS;
  _pollDeadline    = Date.now() + POLL_TIMEOUT_MS;

  showRunning(scanId);
  _pollTimer = setTimeout(() => poll(scanId, apiKey), _currentInterval);
}

async function poll(scanId, apiKey) {
  if (Date.now() > _pollDeadline) {
    showFailed(scanId);
    showError(`Scan timed out. Check back later with Scan ID: ${scanId}`);
    setSubmitLoading(false);
    return;
  }

  let data;
  try {
    const res = await fetch(
      `${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}`,
      { headers: { "x-student-key": apiKey } }
    );
    data = await res.json().catch(() => ({}));
    if (res.status === 401) { handleUnauthorized(); return; }
  } catch (_) {
    showError("Could not reach server. Check your connection.");
    setSubmitLoading(false);
    return;
  }

  if (data.status === "DONE") {
    await handleDone(data, apiKey);
    return;
  }

  if (data.status === "FAILED") {
    showFailed(scanId);
    showError(`Scan failed. Code could not be processed. (Scan ID: ${scanId})`);
    setSubmitLoading(false);
    return;
  }

  _currentInterval = Math.min(_currentInterval * POLL_BACKOFF, POLL_MAX_MS);
  _pollTimer = setTimeout(() => poll(scanId, apiKey), _currentInterval);
}

async function handleDone(statusData, apiKey) {
  _reportUrl = statusData.report_url;

  if (!_reportUrl) {
    showError("Scan complete but no report URL was returned.");
    setSubmitLoading(false);
    return;
  }

  if (statusData.report_url_expires_at) {
    const secsLeft = statusData.report_url_expires_at - Math.floor(Date.now() / 1000);
    if (secsLeft < EXPIRY_WARN_SECS) {
      document.getElementById("expiry-warning").classList.remove("hidden");
    }
  }

  try {
    const reportRes = await fetch(_reportUrl);
    if (reportRes.status === 403) {
      showError('Download link expired. <button class="btn-link" onclick="refreshReportLink()">Click to refresh.</button>');
      setSubmitLoading(false);
      return;
    }
    const report = await reportRes.json();
    renderReport(report);
    showDone();
  } catch (_) {
    showError("Could not fetch scan report. Check your connection.");
  }

  setSubmitLoading(false);
}

async function refreshReportLink() {
  if (!_currentScanId) return;
  const apiKey = localStorage.getItem(LS_API_KEY) || "";
  dismissError();
  document.getElementById("expiry-warning").classList.add("hidden");
  try {
    const res  = await fetch(
      `${API_BASE_URL}/status?scan_id=${encodeURIComponent(_currentScanId)}`,
      { headers: { "x-student-key": apiKey } }
    );
    const data = await res.json().catch(() => ({}));
    if (data.report_url) {
      _reportUrl = data.report_url;
      const reportRes = await fetch(_reportUrl);
      const report    = await reportRes.json();
      renderReport(report);
    }
  } catch (_) {
    showError("Could not refresh download link.");
  }
}

// ── Render report + KPI cards ─────────────────────────────────────────────────

function renderReport(report) {
  const summary = report?.summary || {};
  document.getElementById("kpi-high").textContent   = summary.HIGH   ?? 0;
  document.getElementById("kpi-medium").textContent = summary.MEDIUM ?? 0;
  document.getElementById("kpi-low").textContent    = summary.LOW    ?? 0;
  document.getElementById("kpi-total").textContent  = report?.vuln_count ?? 0;

  const meta = document.getElementById("result-meta-bar");
  if (meta) {
    meta.innerHTML =
      `<span>Scan ID: <strong>${report?.scan_id || "—"}</strong></span>` +
      `<span>Language: <strong>${report?.language || "—"}</strong></span>` +
      `<span>Tool: <strong>${report?.tool || "—"}</strong></span>`;
  }

  window.renderScanResults(report, "results");
}

// ── Auth ──────────────────────────────────────────────────────────────────────

function handleUnauthorized() {
  localStorage.removeItem(LS_API_KEY);
  document.getElementById("api-key").value = "";
  showError("Invalid API key. Please re-enter your key.");
  setSubmitLoading(false);
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function setSubmitLoading(loading) {
  const btn = document.getElementById("submit-btn");
  const lbl = document.getElementById("btn-label");
  btn.disabled = loading;
  lbl.textContent = loading ? "⬡ Scanning…" : "▶ Run Scan";
}

function resetStatus() {
  ["status-idle","status-running","status-done","status-failed"].forEach(id => {
    document.getElementById(id)?.classList.add("hidden");
  });
  document.getElementById("status-idle").classList.remove("hidden");
  document.getElementById("status-badge")?.classList.add("hidden");
}

function showRunning(scanId) {
  document.getElementById("status-idle").classList.add("hidden");
  document.getElementById("status-running").classList.remove("hidden");
  document.getElementById("status-scan-id").textContent = scanId;
  const badge = document.getElementById("status-badge");
  badge.textContent = "SCANNING";
  badge.classList.remove("hidden");
}

function showDone() {
  document.getElementById("status-running").classList.add("hidden");
  document.getElementById("status-done").classList.remove("hidden");
  const badge = document.getElementById("status-badge");
  badge.textContent = "DONE";
  badge.style.background = "rgba(0,229,160,.15)";
  badge.style.color = "var(--green)";
}

function showFailed(scanId) {
  document.getElementById("status-running").classList.add("hidden");
  document.getElementById("status-failed").classList.remove("hidden");
  const el = document.getElementById("fail-scan-id");
  if (el) el.textContent = `Scan ID: ${scanId}`;
  const badge = document.getElementById("status-badge");
  badge.textContent = "FAILED";
  badge.style.background = "rgba(255,77,106,.15)";
  badge.style.color = "#ff4d6a";
  badge.classList.remove("hidden");
}

function showError(html) {
  const banner = document.getElementById("error-banner");
  document.getElementById("error-message").innerHTML = html;
  banner.classList.remove("hidden");
}

function dismissError() {
  document.getElementById("error-banner").classList.add("hidden");
}
