/**
 * app.js — Frontend submission and polling logic
 * Mengshan Li | CS6620 Group 9
 *
 * API_BASE_URL is replaced at deploy time by 04_upload_frontend.sh
 * using: sed -i "s|__LAMBDA_URL__|$LAMBDA_URL|g" app.js
 */

const API_BASE_URL = "__LAMBDA_URL__";

const POLL_INITIAL_MS   = 2000;
const POLL_BACKOFF      = 1.5;
const POLL_MAX_MS       = 30000;
const POLL_TIMEOUT_MS   = 5 * 60 * 1000; // 5 minutes
const EXPIRY_WARN_SECS  = 300;            // warn when < 5 min left on presigned URL

const LS_API_KEY = "sasc_api_key";

let _currentScanId   = null;
let _pollTimer       = null;
let _pollDeadline    = null;
let _currentInterval = POLL_INITIAL_MS;
let _reportUrl       = null;

// ---------------------------------------------------------------------------
// Initialise
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const stored = localStorage.getItem(LS_API_KEY);
  if (stored) {
    document.getElementById("api-key").value = stored;
  }
});

// ---------------------------------------------------------------------------
// Submit handler
// ---------------------------------------------------------------------------

async function handleSubmit() {
  const apiKey   = document.getElementById("api-key").value.trim();
  const language = document.getElementById("language").value;
  const code     = document.getElementById("code").value.trim();

  dismissError();

  if (!apiKey)   { showError("Please enter your API key."); return; }
  if (!language) { showError("Please select a language."); return; }
  if (!code)     { showError("Please paste some code to scan."); return; }

  setSubmitLoading(true);

  let data;
  try {
    const res = await fetch(`${API_BASE_URL}/scan`, {
      method:  "POST",
      headers: {
        "Content-Type": "application/json",
        "x-student-key": apiKey,
      },
      body: JSON.stringify({ code, language }),
    });

    data = await res.json().catch(() => ({}));

    if (res.status === 202) {
      localStorage.setItem(LS_API_KEY, apiKey);
      startPolling(data.scan_id, apiKey);
      return;
    }

    if (res.status === 400) { showError(data.error || "Invalid request."); }
    else if (res.status === 401) { handleUnauthorized(); }
    else if (res.status === 429) { showError("Too many requests — please wait a moment and try again."); }
    else { showError("Server error. Please try again."); }

  } catch (_) {
    showError("Could not reach server. Check your connection.");
  }

  setSubmitLoading(false);
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

function startPolling(scanId, apiKey) {
  _currentScanId   = scanId;
  _currentInterval = POLL_INITIAL_MS;
  _pollDeadline    = Date.now() + POLL_TIMEOUT_MS;

  showStatusSection(scanId);
  setStatus("Scanning...", true);

  _pollTimer = setTimeout(() => poll(scanId, apiKey), _currentInterval);
}

async function poll(scanId, apiKey) {
  if (Date.now() > _pollDeadline) {
    setStatus("Scan is taking longer than expected.", false);
    showError(`Scan is taking longer than expected. Check back later with Scan ID: ${scanId}`);
    setSubmitLoading(false);
    return;
  }

  let data;
  try {
    const res = await fetch(`${API_BASE_URL}/status?scan_id=${encodeURIComponent(scanId)}`, {
      headers: { "x-student-key": apiKey },
    });

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
    setStatus("Failed", false);
    showError(`Scan failed. The code could not be processed. (Scan ID: ${scanId})`);
    setSubmitLoading(false);
    return;
  }

  // Still PENDING — back off and re-poll
  _currentInterval = Math.min(_currentInterval * POLL_BACKOFF, POLL_MAX_MS);
  _pollTimer = setTimeout(() => poll(scanId, apiKey), _currentInterval);
}

async function handleDone(statusData, apiKey) {
  setStatus("Done", false);
  _reportUrl = statusData.report_url;

  if (!_reportUrl) {
    showError("Scan complete but no report URL was returned.");
    setSubmitLoading(false);
    return;
  }

  // Warn if presigned URL is near expiry
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
    showResultsSection();
    window.renderScanResults(report, "results");
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
    const res  = await fetch(`${API_BASE_URL}/status?scan_id=${encodeURIComponent(_currentScanId)}`, {
      headers: { "x-student-key": apiKey },
    });
    const data = await res.json().catch(() => ({}));
    if (data.report_url) {
      _reportUrl = data.report_url;
      const reportRes = await fetch(_reportUrl);
      const report    = await reportRes.json();
      showResultsSection();
      window.renderScanResults(report, "results");
    }
  } catch (_) {
    showError("Could not refresh the download link. Check your connection.");
  }
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

function handleUnauthorized() {
  localStorage.removeItem(LS_API_KEY);
  document.getElementById("api-key").value = "";
  showError("Invalid API key. Please re-enter your key and try again.");
  setSubmitLoading(false);
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function setSubmitLoading(loading) {
  const btn = document.getElementById("submit-btn");
  btn.disabled    = loading;
  btn.textContent = loading ? "Scanning..." : "Scan Code";
}

function showStatusSection(scanId) {
  document.getElementById("status-scan-id").textContent = scanId;
  document.getElementById("status-section").classList.remove("hidden");
}

function showResultsSection() {
  document.getElementById("results-section").classList.remove("hidden");
}

function setStatus(text, spinning) {
  document.getElementById("status-text").textContent = text;
  const spinner = document.getElementById("spinner");
  if (spinning) { spinner.classList.remove("hidden"); }
  else          { spinner.classList.add("hidden"); }
}

function showError(html) {
  const banner = document.getElementById("error-banner");
  document.getElementById("error-message").innerHTML = html;
  banner.classList.remove("hidden");
}

function dismissError() {
  document.getElementById("error-banner").classList.add("hidden");
}
