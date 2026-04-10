/**
 * app.js — Frontend logic
 * CS6620 Group 9
 *
 * API_BASE_URL is loaded at runtime from /config.json (written to S3 by
 * 04_upload_frontend.sh). This avoids baking the URL into the JS bundle,
 * so the endpoint can be updated by re-deploying config.json alone.
 */

let API_BASE_URL = null;

async function initConfig() {
  const res = await fetch("/config.json");
  const cfg = await res.json();
  API_BASE_URL = (cfg.apiUrl || "").replace(/\/$/, "");
}

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
let _currentReport   = null;

// ── Student ID (localStorage) ─────────────────────────────────────────────────

function getStudentId() {
  return (document.getElementById("student-id")?.value || "").trim() || "anonymous";
}

function saveStudentId(value) {
  localStorage.setItem("sast_student_id", value.trim());
}

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
  const titles = { scanner: "Scanner", results: "Results", history: "History" };
  if (title) title.textContent = titles[name] || name;

  if (name === "results" && !_currentReport) setResultsEmptyState(true);
  if (name === "history") loadHistory();
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await initConfig();
  initDarkMode();
  initTheme();
  // Restore student ID — show login modal if none saved
  const stored = localStorage.getItem(LS_STUDENT_ID);
  if (stored) {
    document.getElementById("student-id").value = stored;
    setStudentIdHint(stored);
  } else {
    document.getElementById("login-modal").classList.remove("hidden");
    setTimeout(() => document.getElementById("login-student-id").focus(), 100);
  }

  // Allow pressing Enter to confirm in login modal
  document.getElementById("login-student-id").addEventListener("keydown", e => {
    if (e.key === "Enter") confirmStudentId();
  });

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
      updateCodeStats();
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
      showUploadModal();
      setSubmitLoading(false);
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

function confirmStudentId() {
  const val = document.getElementById("login-student-id").value.trim();
  if (!val) {
    document.getElementById("login-error").classList.remove("hidden");
    return;
  }
  localStorage.setItem(LS_STUDENT_ID, val);
  document.getElementById("student-id").value = val;
  setStudentIdHint(val);
  document.getElementById("login-modal").classList.add("hidden");
}

function showUploadModal() {
  document.getElementById("upload-modal").classList.remove("hidden");
}

function closeUploadModal() {
  document.getElementById("upload-modal").classList.add("hidden");
}

function handleModalOverlayClick(e) {
  if (e.target === document.getElementById("upload-modal")) closeUploadModal();
}

// ── Polling ───────────────────────────────────────────────────────────────────

function startPolling(scanId) {
  _currentScanId   = scanId;
  _currentInterval = POLL_INITIAL_MS;
  _pollDeadline    = Date.now() + POLL_TIMEOUT_MS;

  showRunning(scanId);
  progressShow();
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
      `${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}&student_id=${encodeURIComponent(getStudentId())}`
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
    progressFail();
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
    // Attach presigned URL metadata so results.js can render the JSON download link.
    report.report_url            = statusData.report_url;
    report.report_url_expires_at = statusData.report_url_expires_at;
    renderReport(report);
    progressComplete();
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
      `${API_BASE_URL}/status?scan_id=${encodeURIComponent(_currentScanId)}&student_id=${encodeURIComponent(getStudentId())}`
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
    container.innerHTML = '<div class="history-empty">Enter your Student ID above to load history.</div>';
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

    container.innerHTML = `
      <div class="history-table-header">
        <span>Scan ID</span>
        <span>Language</span>
        <span>Date</span>
        <span>Status</span>
        <span></span>
      </div>
      <ul class="history-list">
        ${scans.map(s => `
          <li class="history-item" onclick="loadHistoryScan('${s.scan_id}')">
            <span class="history-scan-id">${s.scan_id}</span>
            <span class="history-lang">${s.language || "—"}</span>
            <span class="history-date">${formatDate(s.created_at)}</span>
            <span class="history-status ${s.status}">${s.status}</span>
            <span class="history-action">View findings →</span>
          </li>
        `).join("")}
      </ul>`;
  } catch (_) {
    container.innerHTML = '<div class="history-empty">Could not load history. Check your connection.</div>';
  }
}

async function loadHistoryScan(scanId) {
  try {
    const res  = await fetch(`${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}&student_id=${encodeURIComponent(getStudentId())}`);
    const data = await res.json().catch(() => ({}));

    if (data.status === "DONE" && data.report_url) {
      const reportRes = await fetch(data.report_url);
      const report    = await reportRes.json();
      _currentScanId  = scanId;
      switchView("results");
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

function setResultsEmptyState(empty) {
  document.getElementById("results-empty-state").style.display = empty ? "" : "none";
  document.querySelector(".results-top").style.display         = empty ? "none" : "";
  document.getElementById("result-meta-bar").style.display    = empty ? "none" : "";
  document.querySelector(".findings-panel").style.display     = empty ? "none" : "";
}

function renderReport(report) {
  _currentReport = report;
  setResultsEmptyState(false);

  const summary = report?.summary || {};
  const high   = summary.HIGH   ?? 0;
  const medium = summary.MEDIUM ?? 0;
  const low    = summary.LOW    ?? 0;
  const total  = report?.vuln_count ?? 0;

  animateCount("kpi-high",   high);
  animateCount("kpi-medium", medium);
  animateCount("kpi-low",    low);
  animateCount("kpi-total",  total);
  renderDonut(high, medium, low);

  const meta = document.getElementById("result-meta-bar");
  if (meta) {
    meta.innerHTML =
      `<span>Scan ID: <strong>${report?.scan_id || "—"}</strong></span>` +
      `<span>Language: <strong>${report?.language || "—"}</strong></span>` +
      `<span>Tool: <strong>${report?.tool || "—"}</strong></span>`;
  }

  const dlBtn = document.getElementById("btn-download-md");
  if (dlBtn) { dlBtn.disabled = false; dlBtn.title = "Download findings as Markdown"; }

  const searchInput = document.getElementById("findings-search");
  if (searchInput) searchInput.value = "";

  // reset severity filter
  _activeSeverity = "all";
  document.querySelectorAll(".sev-chip").forEach(c =>
    c.classList.toggle("active", c.dataset.sev === "all")
  );

  // toast
  const highCount = report?.summary?.HIGH ?? 0;
  const sub = highCount > 0
    ? `${highCount} High · ${report?.summary?.MEDIUM ?? 0} Medium · ${report?.summary?.LOW ?? 0} Low`
    : "No high-severity findings";
  showToast("Scan complete", sub);

  window.renderScanResults(report, "results");
}

let _activeSeverity = "all";

function filterFindings(query) {
  applyFindingsFilter();
}

function filterBySeverity(sev) {
  _activeSeverity = sev;
  document.querySelectorAll(".sev-chip").forEach(c =>
    c.classList.toggle("active", c.dataset.sev === sev)
  );
  applyFindingsFilter();
}

function applyFindingsFilter() {
  const q   = (document.getElementById("findings-search")?.value || "").trim().toLowerCase();
  const sev = _activeSeverity;
  const rows = document.querySelectorAll("#results tbody tr");
  let shown = 0;

  rows.forEach(row => {
    const text      = row.textContent.toLowerCase();
    const badge     = row.querySelector(".severity-badge");
    const rowSev    = badge ? badge.textContent.trim() : "";
    const matchQ    = !q || text.includes(q);
    const matchSev  = sev === "all" || rowSev === sev;
    const visible   = matchQ && matchSev;
    row.style.display = visible ? "" : "none";
    if (visible) shown++;
  });

  let noMatch = document.getElementById("findings-no-match");
  if (!noMatch) {
    noMatch = document.createElement("div");
    noMatch.id = "findings-no-match";
    noMatch.className = "history-empty";
    document.getElementById("results").appendChild(noMatch);
  }
  const active = q || sev !== "all";
  noMatch.style.display = (active && shown === 0) ? "" : "none";
  noMatch.textContent   = "No findings match your filter.";
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(title, sub = "") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `
    <span class="toast-icon">✓</span>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      ${sub ? `<div class="toast-sub">${sub}</div>` : ""}
    </div>`;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  }, 4000);
}

// ── Jump to line ──────────────────────────────────────────────────────────────

function jumpToLine(lineNum) {
  if (!lineNum) return;
  switchView("scanner");
  const ta = document.getElementById("code");
  if (!ta || !ta.value) return;

  const lines = ta.value.split("\n");
  let pos = 0;
  for (let i = 0; i < Math.min(lineNum - 1, lines.length); i++) {
    pos += lines[i].length + 1;
  }
  const lineLen = (lines[lineNum - 1] || "").length;
  ta.focus();
  ta.setSelectionRange(pos, pos + lineLen);
  const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 22;
  ta.scrollTop = (lineNum - 1) * lineHeight - ta.clientHeight / 3;
}

function downloadAsMarkdown() {
  if (!_currentReport) return;

  const r        = _currentReport;
  const summary  = r.summary || {};
  const findings = Array.isArray(r.findings) ? r.findings : [];
  const date     = new Date().toLocaleString();

  let md = `# Security Scan Report\n\n`;
  md += `| Field | Value |\n|---|---|\n`;
  md += `| **Scan ID** | ${r.scan_id || "—"} |\n`;
  md += `| **Language** | ${r.language || "—"} |\n`;
  md += `| **Tool** | ${r.tool || "—"} |\n`;
  md += `| **Generated** | ${date} |\n\n`;

  md += `## Summary\n\n`;
  md += `| Severity | Count |\n|---|---|\n`;
  md += `| 🔴 High | ${summary.HIGH ?? 0} |\n`;
  md += `| 🟠 Medium | ${summary.MEDIUM ?? 0} |\n`;
  md += `| 🟢 Low | ${summary.LOW ?? 0} |\n`;
  md += `| **Total** | **${r.vuln_count ?? findings.length}** |\n\n`;

  md += `## Findings\n\n`;

  if (findings.length === 0) {
    md += `_No findings._\n`;
  } else {
    md += `| # | Severity | Confidence | Line | Rule | Issue | Code Snippet |\n`;
    md += `|---|---|---|---|---|---|---|\n`;
    findings.forEach((f, i) => {
      const sev      = String(f.severity || "LOW").toUpperCase();
      const conf     = f.confidence || "UNKNOWN";
      const line     = f.line ?? "—";
      const rule     = f.rule_id || "UNKNOWN";
      const issue    = (f.issue || "Unknown issue").replace(/\|/g, "\\|");
      const snippet  = (f.code_snippet || "").replace(/\n/g, " ").replace(/\|/g, "\\|").trim();
      md += `| ${i + 1} | ${sev} | ${conf} | ${line} | ${rule} | ${issue} | \`${snippet}\` |\n`;
    });
  }

  const blob = new Blob([md], { type: "text/markdown" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `scan-${r.scan_id || "report"}.md`;
  a.click();
  URL.revokeObjectURL(url);
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

// ── Code stats ───────────────────────────────────────────────────────────────

function updateCodeStats() {
  const code  = document.getElementById("code").value;
  const stats = document.getElementById("code-stats");
  const clearBtn = document.getElementById("btn-clear-code");
  if (!stats) return;

  if (!code) {
    stats.textContent = "";
    clearBtn?.classList.remove("visible");
    return;
  }

  const lines = code.split("\n").length;
  const bytes = new TextEncoder().encode(code).length;
  const size  = bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
  stats.textContent = `${lines} lines · ${size}`;
  clearBtn?.classList.add("visible");
}

function clearCode() {
  document.getElementById("code").value = "";
  updateCodeStats();
  document.getElementById("code").focus();
}

// ── KPI animation ────────────────────────────────────────────────────────────

function animateCount(id, target, duration = 900) {
  const el = document.getElementById(id);
  if (!el) return;
  const start = performance.now();
  function tick(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
    el.textContent = Math.round(target * eased);
    if (t < 1) requestAnimationFrame(tick);
  }
  el.textContent = "0";
  requestAnimationFrame(tick);
}

// ── Donut chart ───────────────────────────────────────────────────────────────

function renderDonut(high, medium, low) {
  const svg = document.getElementById("donut-svg");
  if (!svg) return;

  const r = 38, cx = 50, cy = 50;
  const circ = 2 * Math.PI * r;
  const total = high + medium + low;

  if (total === 0) {
    svg.innerHTML = `
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="10"/>
      <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central"
            font-size="18" font-weight="800" fill="var(--text-muted)">—</text>`;
    return;
  }

  const segs = [
    { val: high,   color: "var(--sev-high-fg)" },
    { val: medium, color: "var(--sev-med-fg)"  },
    { val: low,    color: "var(--sev-low-fg)"  },
  ];

  let cumulative = 0;
  const circles = segs.map(s => {
    if (s.val === 0) return "";
    const len = (s.val / total) * circ;
    const gap = circ - len;
    const offset = circ - cumulative;
    cumulative += len;
    return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
      stroke="${s.color}" stroke-width="10"
      stroke-dasharray="${len} ${gap}"
      stroke-dashoffset="${offset}"
      transform="rotate(-90 ${cx} ${cy})"/>`;
  }).join("");

  svg.innerHTML = `
    ${circles}
    <text x="${cx}" y="${cy - 7}" text-anchor="middle" dominant-baseline="central"
          font-size="20" font-weight="800" fill="var(--text)">${total}</text>
    <text x="${cx}" y="${cy + 13}" text-anchor="middle" dominant-baseline="central"
          font-size="10" letter-spacing="1" fill="var(--text-muted)">TOTAL</text>`;
}

// ── Progress bar ─────────────────────────────────────────────────────────────

let _progressTimer = null;
let _progressValue = 0;

function progressShow() {
  _progressValue = 0;
  const wrap = document.getElementById("scan-progress-wrap");
  const bar  = document.getElementById("scan-progress-bar");
  wrap.classList.remove("hidden");
  bar.style.background = "var(--accent)";
  bar.style.width = "0%";
  _tickProgress();
}

function _tickProgress() {
  if (_progressTimer) clearTimeout(_progressTimer);
  // Slow down as we approach 90%
  const remaining = 90 - _progressValue;
  const step = Math.max(0.5, remaining * 0.06);
  _progressValue = Math.min(90, _progressValue + step);
  document.getElementById("scan-progress-bar").style.width = _progressValue + "%";
  const delay = 400 + (_progressValue / 90) * 800;
  _progressTimer = setTimeout(_tickProgress, delay);
}

function progressComplete() {
  if (_progressTimer) { clearTimeout(_progressTimer); _progressTimer = null; }
  const bar = document.getElementById("scan-progress-bar");
  bar.style.width = "100%";
  setTimeout(() => {
    const wrap = document.getElementById("scan-progress-wrap");
    wrap.classList.add("hidden");
    bar.style.width = "0%";
  }, 600);
}

function progressFail() {
  if (_progressTimer) { clearTimeout(_progressTimer); _progressTimer = null; }
  const bar = document.getElementById("scan-progress-bar");
  bar.style.background = "var(--red)";
  bar.style.width = "100%";
  setTimeout(() => {
    const wrap = document.getElementById("scan-progress-wrap");
    wrap.classList.add("hidden");
    bar.style.width = "0%";
  }, 800);
}

// ── Dark mode ─────────────────────────────────────────────────────────────────

function setTheme(name) {
  document.body.dataset.theme = name;
  localStorage.setItem("sast_theme", name);
  document.querySelectorAll(".theme-swatch").forEach(s => {
    s.classList.toggle("active", s.dataset.theme === name);
  });
}

function initTheme() {
  const saved = localStorage.getItem("sast_theme") || "green";
  setTheme(saved);
}

function toggleDarkMode() {
  const isDark = document.body.classList.toggle("dark");
  localStorage.setItem("sast_dark_mode", isDark ? "1" : "0");
  document.getElementById("dark-mode-icon").textContent  = isDark ? "☀️" : "🌙";
  document.getElementById("dark-mode-label").textContent = isDark ? "Light Mode" : "Dark Mode";
}

function initDarkMode() {
  if (localStorage.getItem("sast_dark_mode") === "1") {
    document.body.classList.add("dark");
    document.getElementById("dark-mode-icon").textContent  = "☀️";
    document.getElementById("dark-mode-label").textContent = "Light Mode";
  }
}

// ── Public API for results.js ─────────────────────────────────────────────────

window.pollStatus = async function pollStatus(scanId) {
  const res = await fetch(
    `${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}&student_id=${encodeURIComponent(getStudentId())}`
  );
  if (!res.ok) throw new Error(`Status request failed (${res.status})`);
  return res.json();
};
