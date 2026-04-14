function escapeHtml(value) {
	const text = String(value ?? "");
	return text
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;")
		.replaceAll('"', "&quot;")
		.replaceAll("'", "&#39;");
}

function severityBadgeClass(severity) {
	const normalized = String(severity || "LOW").toUpperCase();
	if (normalized === "HIGH") return "severity-badge severity-high";
	if (normalized === "MEDIUM") return "severity-badge severity-medium";
	return "severity-badge severity-low";
}

function normalizeSummary(summary) {
	return {
		HIGH: Number(summary?.HIGH || 0),
		MEDIUM: Number(summary?.MEDIUM || 0),
		LOW: Number(summary?.LOW || 0),
	};
}

/**
 * Build the download link section.
 *
 * If report_url_expires_at is present, show when the link expires and wire up
 * a "Refresh link" button that re-calls GET /status to get a fresh URL.
 * This prevents students from getting a silent 403 when they return after
 * the 1-hour presigned URL TTL.
 */
function buildDownloadSection(report) {
	if (!report?.report_url_expires_at) return "";

	const expiresAt = report.report_url_expires_at;
	const scanId    = report.scan_id;

	const expiresDate  = new Date(expiresAt);
	const expiresLocal = expiresDate.toLocaleString();
	const isExpired    = Date.now() >= expiresDate.getTime();

	const expiryHtml = isExpired
		? `<p class="url-expired">Download link has expired. Click "Refresh link" to get a new one.</p>`
		: `<p class="url-expiry">Download link expires at: <strong>${escapeHtml(expiresLocal)}</strong></p>`;

	let refreshHtml = "";
	if (scanId && typeof window.pollStatus === "function") {
		refreshHtml = `<button id="refresh-url-btn" type="button">Refresh link</button>`;
	}

	return `
		<section class="result-download">
			${expiryHtml}
			${refreshHtml}
		</section>
	`;
}

/**
 * Attach the "Refresh link" button handler after the DOM has been updated.
 * Calls window.pollStatus to get a fresh presigned URL, then re-renders.
 */
function attachRefreshHandler(report, container) {
	const btn = container.querySelector("#refresh-url-btn");
	if (!btn) return;

	btn.addEventListener("click", async () => {
		btn.disabled    = true;
		btn.textContent = "Refreshing...";
		try {
			const fresh = await window.pollStatus(report.scan_id);
			if (!fresh.report_url) throw new Error("No download URL in refreshed status.");
			const reportRes  = await fetch(fresh.report_url);
			if (!reportRes.ok) throw new Error(`Fetching report failed (${reportRes.status}).`);
			const freshReport = await reportRes.json();
			// Carry the new presigned URL into the report so the download section renders.
			freshReport.report_url            = fresh.report_url;
			freshReport.report_url_expires_at = fresh.report_url_expires_at;
			renderScanResults(freshReport, container);
		} catch (err) {
			btn.disabled    = false;
			btn.textContent = "Refresh link";
			alert("Could not refresh link: " + err.message);
		}
	});
}

function renderScanResults(report, target = "results") {
	const container = typeof target === "string" ? document.getElementById(target) : target;
	if (!container) return;

	const findings  = Array.isArray(report?.findings) ? report.findings : [];
	const summary   = normalizeSummary(report?.summary || {});
	const vulnCount = Number(report?.vuln_count ?? findings.length);
	const scanId    = escapeHtml(report?.scan_id || "-");
	const TOOL_DISPLAY_NAMES = { teacher_scanner: "JS/TS" };
	const rawTool   = report?.tool || "-";
	const tool      = escapeHtml(TOOL_DISPLAY_NAMES[rawTool] || rawTool);
	const language  = escapeHtml(report?.language || "-");

	const rows = findings
		.map((finding) => {
			const severity   = String(finding?.severity || "LOW").toUpperCase();
			const confidence = escapeHtml(finding?.confidence || "UNKNOWN");
			const issue      = escapeHtml(finding?.issue || "Unknown issue");
			const line       = Number(finding?.line ?? 0);
			const snippet    = escapeHtml(finding?.code_snippet || "");
			const ruleId     = escapeHtml(finding?.rule_id || "UNKNOWN");

			const lineDisplay = line
				? `<span class="line-link" onclick="jumpToLine(${line})" title="Jump to line ${line} in Scanner">${line}</span>`
				: "—";

			return `
				<tr>
					<td><span class="${severityBadgeClass(severity)}">${severity}</span></td>
					<td>${confidence}</td>
					<td>${lineDisplay}</td>
					<td>${ruleId}</td>
					<td>${issue}</td>
					<td><pre>${snippet}</pre></td>
				</tr>
			`;
		})
		.join("");

	container.innerHTML = `
		<section class="result-meta">
			<p><strong>Scan ID:</strong> ${scanId}</p>
			<p><strong>Language:</strong> ${language}</p>
			<p><strong>Tool:</strong> ${tool}</p>
			<p><strong>Total:</strong> ${vulnCount}</p>
		</section>

		<section class="result-summary">
			<span class="${severityBadgeClass("HIGH")}">HIGH ${summary.HIGH}</span>
			<span class="${severityBadgeClass("MEDIUM")}">MEDIUM ${summary.MEDIUM}</span>
			<span class="${severityBadgeClass("LOW")}">LOW ${summary.LOW}</span>
		</section>

		${buildDownloadSection(report)}

		<section class="result-findings">
			<table>
				<thead>
					<tr>
						<th>Severity</th>
						<th>Confidence</th>
						<th>Line</th>
						<th>Rule</th>
						<th>Issue</th>
						<th>Code Snippet</th>
					</tr>
				</thead>
				<tbody>
					${rows || '<tr><td colspan="6">No findings.</td></tr>'}
				</tbody>
			</table>
		</section>
	`;

	attachRefreshHandler(report, container);
}

window.renderScanResults = renderScanResults;

