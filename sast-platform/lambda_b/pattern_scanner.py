"""
Lightweight regex-based security scanner for JavaScript/TypeScript and other languages.
Used as a Lambda-native fallback when ECS/semgrep is not available.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


# Each rule: (rule_id, severity, confidence, pattern, description)
# Prefix "cs:" means case-sensitive match; otherwise re.IGNORECASE is used.
_RULES: List[Tuple[str, str, str, str, str]] = [
    # ── Hardcoded Secrets ──────────────────────────────────────────────────
    (
        "cs:secrets.stripe-api-key",
        "HIGH", "HIGH",
        r'sk_(live|test)_[A-Za-z0-9]{20,}',
        "Hardcoded Stripe API key detected",
    ),
    (
        "cs:secrets.aws-access-key",
        "HIGH", "HIGH",
        r'AKIA[0-9A-Z]{16}',
        "Hardcoded AWS access key ID detected",
    ),
    (
        "secrets.hardcoded-password",
        "HIGH", "MEDIUM",
        r'(password|passwd|pwd|secret|api_?key|auth_?token)\s*[=:]\s*["\'][^"\']{6,}["\']',
        "Hardcoded credential or secret value detected",
    ),
    (
        "secrets.connection-string-credentials",
        "HIGH", "HIGH",
        r'(mongodb|mysql|postgres|postgresql|mssql|redis|amqp)://[^@\s]+:[^@\s]+@',
        "Database connection string contains embedded credentials",
    ),
    (
        "secrets.github-token",
        "HIGH", "HIGH",
        r'gh[pousr]_[A-Za-z0-9]{36,}',
        "Hardcoded GitHub personal access token detected",
    ),
    (
        "secrets.generic-private-key",
        "HIGH", "HIGH",
        r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
        "Private key material found in source code",
    ),
    (
        "secrets.jwt-token",
        "MEDIUM", "MEDIUM",
        r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',
        "Hardcoded JWT token detected",
    ),

    # ── Dangerous Functions ────────────────────────────────────────────────
    (
        "injection.eval",
        "HIGH", "MEDIUM",
        r'\beval\s*\(',
        "Use of eval() can lead to code injection",
    ),
    (
        "injection.function-constructor",
        "HIGH", "MEDIUM",
        r'\bnew\s+Function\s*\(',
        "Use of new Function() is equivalent to eval() and can lead to code injection",
    ),
    (
        "injection.child-process-exec",
        "HIGH", "MEDIUM",
        r'\b(child_process\s*\.\s*exec|execSync\s*\(|spawnSync\s*\()',
        "Shell command execution — ensure input is sanitized",
    ),
    (
        "injection.sql-concatenation",
        "HIGH", "MEDIUM",
        r'(query|sql|execute)\s*\(\s*["\'].*\+|`[^`]*(SELECT|INSERT|UPDATE|DELETE|DROP)[^`]*\$\{',
        "SQL query built with string concatenation or template literal — SQL injection risk",
    ),

    # ── XSS ───────────────────────────────────────────────────────────────
    (
        "xss.innerhtml",
        "HIGH", "MEDIUM",
        r'\binnerHTML\s*=\s*(?!["\']["\'])',
        "Direct innerHTML assignment — potential XSS if content is user-controlled",
    ),
    (
        "xss.document-write",
        "MEDIUM", "MEDIUM",
        r'\bdocument\s*\.\s*write\s*\(',
        "document.write() can introduce XSS when used with unsanitized input",
    ),
    (
        "xss.dangerously-set-html",
        "HIGH", "HIGH",
        r'dangerouslySetInnerHTML',
        "dangerouslySetInnerHTML bypasses React XSS protection",
    ),

    # ── Insecure Configurations ────────────────────────────────────────────
    (
        "tls.reject-unauthorized-false",
        "HIGH", "HIGH",
        r'rejectUnauthorized\s*:\s*false',
        "TLS certificate verification disabled — vulnerable to MITM attacks",
    ),
    (
        "crypto.weak-hash-md5",
        "MEDIUM", "HIGH",
        r'\bcreateHash\s*\(\s*["\']md5["\']\)',
        "MD5 is a cryptographically broken hash function",
    ),
    (
        "crypto.weak-hash-sha1",
        "MEDIUM", "HIGH",
        r'\bcreateHash\s*\(\s*["\']sha1["\']\)',
        "SHA-1 is a deprecated and weak hash function",
    ),
    (
        "crypto.insecure-random",
        "MEDIUM", "MEDIUM",
        r'\bMath\s*\.\s*random\s*\(\)',
        "Math.random() is not cryptographically secure — use crypto.getRandomValues()",
    ),

    # ── Hardcoded IPs ─────────────────────────────────────────────────────
    (
        "config.hardcoded-ip",
        "LOW", "MEDIUM",
        r'["\'](192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})["\']',
        "Hardcoded private IP address detected",
    ),
]


def scan(code: str, language: str, scan_id: str) -> Dict[str, Any]:
    """
    Scan source code with regex rules.
    Returns a result dict compatible with result_parser.normalize_result output.
    """
    lines = code.splitlines()
    findings: List[Dict[str, Any]] = []
    seen: set = set()

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue

        for rule_id, severity, confidence, pattern, description in _RULES:
            flags = 0 if rule_id.startswith("cs:") else re.IGNORECASE
            clean_id = rule_id[3:] if rule_id.startswith("cs:") else rule_id

            if re.search(pattern, line, flags):
                key = (clean_id, lineno)
                if key in seen:
                    continue
                seen.add(key)

                snippet = line.rstrip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."

                findings.append({
                    "line":         lineno,
                    "severity":     severity,
                    "confidence":   confidence,
                    "issue":        description,
                    "code_snippet": snippet,
                    "rule_id":      clean_id,
                })

    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    findings.sort(key=lambda f: (severity_order.get(f["severity"], 3), f["line"]))

    summary = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        summary[f["severity"]] = summary.get(f["severity"], 0) + 1

    return {
        "scan_id":    scan_id,
        "language":   language,
        "tool":       "pattern",
        "findings":   findings,
        "summary":    summary,
        "vuln_count": len(findings),
    }
