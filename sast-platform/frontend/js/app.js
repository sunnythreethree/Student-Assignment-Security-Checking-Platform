/**
 * app.js — Frontend logic
 * CS6620 Group 9
 *
 * API_BASE_URL is replaced at deploy time by 04_upload_frontend.sh
 * using: sed -i "s|__LAMBDA_URL__|$LAMBDA_URL|g" app.js
 */

const API_BASE_URL = "__LAMBDA_URL__";

const POLL_INITIAL_MS  = 2000;
const POLL_BACKOFF     = 1.5;
const POLL_MAX_MS      = 30000;
const POLL_TIMEOUT_MS  = 5 * 60 * 1000;

const LS_STUDENT_ID = "sasc_student_id";

// Map file extension → language selector value
const EXT_TO_LANGUAGE = {
  py:   "python",
  java: "java",
  js:   "javascript",
  ts:   "typescript",
  go:   "go",
  rb:   "ruby",
  c:    "c",
  cpp:  "cpp",
  cc:   "cpp",
  cxx:  "cpp",
};

let _currentScanId   = null;
let _pollTimer       = null;
let _pollDeadline    = null;
let _currentInterval = POLL_INITIAL_MS;
let _reportUrl       = null;

// ── View switching ────────────────────────────────────────────────────────────

function switchView(name) {
  document.querySelectorAll(".view").forEach(v => {
    v.classList.remove("active");
    v.classList.add("hidden");
  });
  document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));

  const view = document.getElementById(`view-${name}`);
  if (view) { view.classList.remove("hidden"); view.classList.add("active"); }

  const navItem = document.querySelector(`.nav-item[data-view="${name}"]`);
  if (navItem) navItem.classList.add("active");

  const title = document.getElementById("topbar-title");
  if (title) title.textContent = name === "results" ? "Results & History" : "Scanner";

  if (name === "results") loadHistory();
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Restore student ID
  const stored = localStorage.getItem(LS_STUDENT_ID);
  if (stored) {
    document.getElementById("student-id").value = stored;
    setStudentIdHint(stored);
  }

  // Save student ID on change
  document.getElementById("student-id").addEventListener("input", e => {
    const val = e.target.value.trim();
    if (val) {
      localStorage.setItem(LS_STUDENT_ID, val);
      setStudentIdHint(val);
    } else {
      localStorage.removeItem(LS_STUDENT_ID);
      document.getElementById("student-id-hint").textContent =
        "Auto-saved · history persists across sessions";
      document.getElementById("student-id-hint").className = "student-id-hint";
    }
  });

  // Drag-and-drop
  const dropZone = document.getElementById("drop-zone");

  ["dragenter", "dragover"].forEach(evt =>
    dropZone.addEventListener(evt, e => {
      e.preventDefault();
      dropZone.classList.add("drag-over");
    })
  );

  ["dragleave", "drop"].forEach(evt =>
    dropZone.addEventListener(evt, () => dropZone.classList.remove("drag-over"))
  );

  dropZone.addEventListener("drop", e => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (!file) return;

    const ext  = file.name.split(".").pop().toLowerCase();
    const lang = EXT_TO_LANGUAGE[ext];

    if (!lang) {
      const supported = Object.keys(EXT_TO_LANGUAGE).map(x => "." + x).join(", ");
      showError(`Unsupported file type ".${ext}". Supported: ${supported}`);
      return;
    }

    document.getElementById("language").value = lang;

    const reader = new FileReader();
    reader.onload = ev => {
      document.getElementById("code").value = ev.target.result;
      dismissError();
    };
    reader.readAsText(file);
  });
});

function setStudentIdHint(id) {
  const hint = document.getElementById("student-id-hint");
  hint.textContent = `Saved as "${id}" · history will load automatically`;
  hint.className = "student-id-hint saved";
}

function getStudentId() {
  return document.getElementById("student-id").value.trim() || "anonymous";
}

// ── Submit ────────────────────────────────────────────────────────────────────

async function handleSubmit() {
  const language  = document.getElementById("language").value;
  const code      = document.getElementById("code").value.trim();
  const studentId = getStudentId();

  dismissError();

  if (!language) { showError("Please select a language."); return; }
  if (!code)     { showError("Please paste some code to scan."); return; }

  setSubmitLoading(true);
  resetStatus();

  try {
    const res = await fetch(`${API_BASE_URL}/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, language, student_id: studentId }),
    });

    const data = await res.json().catch(() => ({}));

    if (res.status === 202) {
      startPolling(data.scan_id);
      return;
    }

    if (res.status === 400)      showError(data.error || "Invalid request.");
    else if (res.status === 429) showError("Too many requests — please wait.");
    else                         showError("Server error. Please try again.");

  } catch (_) {
    showError("Could not reach server. Check your connection.");
  }

  setSubmitLoading(false);
}

// ── Polling ───────────────────────────────────────────────────────────────────

function startPolling(scanId) {
  _currentScanId   = scanId;
  _currentInterval = POLL_INITIAL_MS;
  _pollDeadline    = Date.now() + POLL_TIMEOUT_MS;

  showRunning(scanId);
  _pollTimer = setTimeout(() => poll(scanId), _currentInterval);
}

async function poll(scanId) {
  if (Date.now() > _pollDeadline) {
    showFailed(scanId);
    showError(`Scan timed out. Scan ID: ${scanId}`);
    setSubmitLoading(false);
    return;
  }

  let data;
  try {
    const res = await fetch(
      `${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}`
    );
    data = await res.json().catch(() => ({}));
  } catch (_) {
    showError("Could not reach server. Check your connection.");
    setSubmitLoading(false);
    return;
  }

  if (data.status === "DONE") {
    await handleDone(data);
    return;
  }

  if (data.status === "FAILED") {
    showFailed(scanId);
    showError(`Scan failed. (Scan ID: ${scanId})`);
    setSubmitLoading(false);
    return;
  }

  if (data.scan_expires_at) {
    const serverDeadline = new Date(data.scan_expires_at).getTime();
    if (Date.now() >= serverDeadline) {
      showFailed(scanId);
      showError(`Scan expired. (Scan ID: ${scanId})`);
      setSubmitLoading(false);
      return;
    }
    if (serverDeadline < _pollDeadline) _pollDeadline = serverDeadline;
  }

  const hintMs = data.retry_after_seconds
    ? data.retry_after_seconds * 1000
    : _currentInterval;
  _currentInterval = Math.min(_currentInterval * POLL_BACKOFF, POLL_MAX_MS);
  _pollTimer = setTimeout(() => poll(scanId), Math.min(hintMs, POLL_MAX_MS));
}

async function handleDone(statusData) {
  _reportUrl = statusData.report_url;

  if (!_reportUrl) {
    showError("Scan complete but no report URL was returned.");
    setSubmitLoading(false);
    return;
  }

  try {
    const reportRes = await fetch(_reportUrl);
    if (reportRes.status === 403) {
      showError('Download link expired. <button class="btn-link" onclick="refreshReportLink()">Refresh.</button>');
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
  dismissError();
  try {
    const res  = await fetch(
      `${API_BASE_URL}/status?scan_id=${encodeURIComponent(_currentScanId)}`
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

// ── History ───────────────────────────────────────────────────────────────────

async function loadHistory() {
  const studentId = getStudentId();
  const container = document.getElementById("history-content");

  if (studentId === "anonymous") {
    container.innerHTML = '<div class="history-empty">Enter your Student ID in the sidebar to load history.</div>';
    return;
  }

  container.innerHTML = '<div class="history-empty">Loading history…</div>';

  try {
    const res  = await fetch(
      `${API_BASE_URL}/history?student_id=${encodeURIComponent(studentId)}`
    );
    const data = await res.json().catch(() => ({}));
    const scans = data.scans || [];

    if (scans.length === 0) {
      container.innerHTML = `<div class="history-empty">No scans yet for "${studentId}".</div>`;
      return;
    }

    container.innerHTML = `<ul class="history-list">
      ${scans.map(s => `
        <li class="history-item" onclick="loadHistoryScan('${s.scan_id}')">
          <span class="history-scan-id">${s.scan_id}</span>
          <span class="history-lang">${s.language || "—"}</span>
          <span class="history-date">${formatDate(s.created_at)}</span>
          <span class="history-status ${s.status}">${s.status}</span>
        </li>
      `).join("")}
    </ul>`;
  } catch (_) {
    container.innerHTML = '<div class="history-empty">Could not load history. Check your connection.</div>';
  }
}

async function loadHistoryScan(scanId) {
  try {
    const res  = await fetch(`${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}`);
    const data = await res.json().catch(() => ({}));

    if (data.status === "DONE" && data.report_url) {
      const reportRes = await fetch(data.report_url);
      const report    = await reportRes.json();
      _currentScanId  = scanId;
      renderReport(report);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } else {
      showError(`Scan ${scanId} is ${data.status} — no report available yet.`);
    }
  } catch (_) {
    showError("Could not load scan. Check your connection.");
  }
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return iso;
  }
}

// ── Render report ─────────────────────────────────────────────────────────────

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

// ── UI helpers ────────────────────────────────────────────────────────────────

function setSubmitLoading(loading) {
  const btn = document.getElementById("submit-btn");
  const lbl = document.getElementById("btn-label");
  btn.disabled    = loading;
  lbl.textContent = loading ? "Scanning…" : "Scan Code";
}

function resetStatus() {
  document.getElementById("status-idle").classList.remove("hidden");
  document.getElementById("status-running").classList.add("hidden");
  document.getElementById("status-done").classList.add("hidden");
  document.getElementById("status-failed").classList.add("hidden");
}

function showRunning(scanId) {
  document.getElementById("status-idle").classList.add("hidden");
  document.getElementById("status-running").classList.remove("hidden");
  document.getElementById("status-scan-id").textContent = scanId;
}

function showDone() {
  document.getElementById("status-running").classList.add("hidden");
  document.getElementById("status-done").classList.remove("hidden");
}

function showFailed(scanId) {
  document.getElementById("status-running").classList.add("hidden");
  document.getElementById("status-failed").classList.remove("hidden");
  const el = document.getElementById("fail-scan-id");
  if (el) el.textContent = scanId;
}

function showError(html) {
  const banner = document.getElementById("error-banner");
  document.getElementById("error-message").innerHTML = html;
  banner.classList.remove("hidden");
}

function dismissError() {
  document.getElementById("error-banner").classList.add("hidden");
}

// ── Public API for results.js ─────────────────────────────────────────────────

window.pollStatus = async function pollStatus(scanId) {
  const res = await fetch(
    `${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}`
  );
  if (!res.ok) throw new Error(`Status request failed (${res.status})`);
  return res.json();
};
