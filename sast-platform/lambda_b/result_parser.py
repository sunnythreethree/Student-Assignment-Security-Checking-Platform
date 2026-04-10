from __future__ import annotations

from typing import Any, Dict, List


SEVERITY_LEVELS = ("HIGH", "MEDIUM", "LOW")


def _normalize_severity(value: Any) -> str:
	if value is None:
		return "LOW"

	if isinstance(value, int):
		if value >= 8:
			return "HIGH"
		if value >= 5:
			return "MEDIUM"
		return "LOW"

	normalized = str(value).strip().upper()
	if normalized in {"ERROR", "CRITICAL", "HIGH"}:
		return "HIGH"
	if normalized in {"WARNING", "WARN", "MEDIUM"}:
		return "MEDIUM"
	return "LOW"


def _normalize_confidence(value: Any) -> str:
	if value is None:
		return "UNKNOWN"

	normalized = str(value).strip().upper()
	if normalized in {"HIGH", "MEDIUM", "LOW"}:
		return normalized
	return "UNKNOWN"


def _safe_int(value: Any, default: int = 0) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _summary(findings: List[Dict[str, Any]]) -> Dict[str, int]:
	counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
	for finding in findings:
		severity = finding.get("severity", "LOW")
		counts[severity] = counts.get(severity, 0) + 1
	return counts


def parse_bandit_output(raw_output: Dict[str, Any], scan_id: str, language: str = "python") -> Dict[str, Any]:
	findings: List[Dict[str, Any]] = []

	for item in raw_output.get("results", []):
		findings.append(
			{
				"line": _safe_int(item.get("line_number"), 0),
				"severity": _normalize_severity(item.get("issue_severity")),
				"confidence": _normalize_confidence(item.get("issue_confidence")),
				"issue": str(item.get("issue_text") or "Unknown issue"),
				"code_snippet": str(item.get("code") or "").strip(),
				"rule_id": str(item.get("test_id") or "UNKNOWN"),
			}
		)

	findings.sort(key=lambda finding: (SEVERITY_LEVELS.index(finding["severity"]), finding["line"]))
	summary = _summary(findings)
	return {
		"scan_id": scan_id,
		"language": language,
		"tool": "bandit",
		"findings": findings,
		"summary": summary,
		"vuln_count": len(findings),
	}


def parse_semgrep_output(raw_output: Dict[str, Any], scan_id: str, language: str) -> Dict[str, Any]:
	findings: List[Dict[str, Any]] = []

	for item in raw_output.get("results", []):
		extra = item.get("extra", {})
		metadata = extra.get("metadata", {})
		start = item.get("start", {})

		findings.append(
			{
				"line": _safe_int(start.get("line"), 0),
				"severity": _normalize_severity(extra.get("severity")),
				"confidence": _normalize_confidence(metadata.get("confidence")),
				"issue": str(extra.get("message") or item.get("check_id") or "Unknown issue"),
				"code_snippet": str(extra.get("lines") or "").strip(),
				"rule_id": str(item.get("check_id") or "UNKNOWN"),
			}
		)

	findings.sort(key=lambda finding: (SEVERITY_LEVELS.index(finding["severity"]), finding["line"]))
	summary = _summary(findings)
	return {
		"scan_id": scan_id,
		"language": language,
		"tool": "semgrep",
		"findings": findings,
		"summary": summary,
		"vuln_count": len(findings),
	}


def parse_pattern_output(raw_output: Dict[str, Any], scan_id: str, language: str) -> Dict[str, Any]:
	findings: List[Dict[str, Any]] = raw_output.get("findings", [])
	summary: Dict[str, int] = raw_output.get("summary", {"HIGH": 0, "MEDIUM": 0, "LOW": 0})
	return {
		"scan_id": scan_id,
		"language": language,
		"tool": "pattern",
		"findings": findings,
		"summary": summary,
		"vuln_count": len(findings),
	}


def normalize_result(tool: str, raw_output: Dict[str, Any], scan_id: str, language: str) -> Dict[str, Any]:
	tool_name = (tool or "").strip().lower()

	if tool_name == "bandit":
		return parse_bandit_output(raw_output or {}, scan_id=scan_id, language=language)
	if tool_name == "semgrep":
		return parse_semgrep_output(raw_output or {}, scan_id=scan_id, language=language)
	if tool_name == "pattern":
		return parse_pattern_output(raw_output or {}, scan_id=scan_id, language=language)

	raise ValueError(f"Unsupported tool: {tool}")

