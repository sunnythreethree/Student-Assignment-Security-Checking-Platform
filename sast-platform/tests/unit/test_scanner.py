import json
import os
import unittest
from unittest.mock import patch, MagicMock

from scanner import SecurityScanner, scan_code_with_timeout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_run(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _bandit_stdout(findings=None):
    return json.dumps({
        "results": findings or [],
        "metrics": {
            "CONFIDENCE.HIGH": 0, "CONFIDENCE.MEDIUM": 0, "CONFIDENCE.LOW": 0,
            "SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": 0, "SEVERITY.LOW": 0
        }
    })


def _semgrep_stdout(findings=None):
    return json.dumps({"results": findings or []})


# ---------------------------------------------------------------------------
# Routing — Python → Bandit
# ---------------------------------------------------------------------------

class TestPythonRouting(unittest.TestCase):

    def test_python_routes_to_bandit(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_bandit_stdout())) as mock_run:
            result = SecurityScanner().scan_code("x = 1", "python", "sid-1")
        self.assertEqual(result["tool"], "bandit")

    def test_python_file_has_py_extension(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_bandit_stdout())) as mock_run:
            SecurityScanner().scan_code("x = 1", "python", "sid-2")
        cmd = mock_run.call_args[0][0]
        self.assertTrue(any(arg.endswith(".py") for arg in cmd))

    def test_python_case_insensitive(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_bandit_stdout())):
            result = SecurityScanner().scan_code("x = 1", "Python", "sid-3")
        self.assertEqual(result["tool"], "bandit")


# ---------------------------------------------------------------------------
# Routing — Java / JavaScript → Semgrep
# ---------------------------------------------------------------------------

class TestSemgrepRouting(unittest.TestCase):

    def _scan(self, language):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_semgrep_stdout())) as mock_run:
            result = SecurityScanner().scan_code("// code", language, "sid")
        return result, mock_run

    def test_java_routes_to_semgrep(self):
        result, _ = self._scan("java")
        self.assertEqual(result["tool"], "semgrep")

    def test_javascript_routes_to_semgrep(self):
        result, _ = self._scan("javascript")
        self.assertEqual(result["tool"], "semgrep")

    def test_java_file_has_java_extension(self):
        _, mock_run = self._scan("java")
        cmd = mock_run.call_args[0][0]
        self.assertTrue(any(arg.endswith(".java") for arg in cmd))

    def test_javascript_file_has_js_extension(self):
        _, mock_run = self._scan("javascript")
        cmd = mock_run.call_args[0][0]
        self.assertTrue(any(arg.endswith(".js") for arg in cmd))


# ---------------------------------------------------------------------------
# Routing — 5 new languages → Semgrep
# ---------------------------------------------------------------------------

class TestNewLanguageRouting(unittest.TestCase):

    def _assert_semgrep_ext(self, language, expected_ext):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_semgrep_stdout())) as mock_run:
            result = SecurityScanner().scan_code("// code", language, "sid")
        self.assertEqual(result["tool"], "semgrep", f"{language} should route to semgrep")
        cmd = mock_run.call_args[0][0]
        self.assertTrue(any(arg.endswith(expected_ext) for arg in cmd),
                        f"{language} expected ext {expected_ext}")

    def test_typescript_routes_to_semgrep_with_ts_extension(self):
        self._assert_semgrep_ext("typescript", ".ts")

    def test_go_routes_to_semgrep_with_go_extension(self):
        self._assert_semgrep_ext("go", ".go")

    def test_ruby_routes_to_semgrep_with_rb_extension(self):
        self._assert_semgrep_ext("ruby", ".rb")

    def test_c_routes_to_semgrep_with_c_extension(self):
        self._assert_semgrep_ext("c", ".c")

    def test_cpp_routes_to_semgrep_with_cpp_extension(self):
        self._assert_semgrep_ext("cpp", ".cpp")

    def test_typescript_case_insensitive(self):
        self._assert_semgrep_ext("TypeScript", ".ts")


# ---------------------------------------------------------------------------
# Bandit exit codes
# ---------------------------------------------------------------------------

class TestBanditExitCodes(unittest.TestCase):

    def test_exit_code_0_clean_scan(self):
        """Exit code 0 means no issues — should return tool=bandit with empty findings."""
        with patch("scanner.subprocess.run", return_value=_mock_run(0, _bandit_stdout())):
            result = SecurityScanner().scan_code("x = 1", "python", "sid")
        self.assertEqual(result["tool"], "bandit")
        self.assertEqual(result["findings"], [])

    def test_exit_code_1_issues_found(self):
        """Exit code 1 means issues found — should still return findings normally."""
        finding = {"line_number": 1, "issue_severity": "HIGH", "issue_text": "eval"}
        with patch("scanner.subprocess.run", return_value=_mock_run(1, _bandit_stdout([finding]))):
            result = SecurityScanner().scan_code("eval(x)", "python", "sid")
        self.assertEqual(result["tool"], "bandit")
        self.assertEqual(len(result["findings"]), 1)

    def test_exit_code_2_raises_error(self):
        """Exit code >=2 is a Bandit failure — should return tool=error."""
        with patch("scanner.subprocess.run", return_value=_mock_run(2, stderr="crash")):
            result = SecurityScanner().scan_code("x = 1", "python", "sid")
        self.assertEqual(result["tool"], "error")

    def test_exit_code_3_also_treated_as_error(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(3, stderr="crash")):
            result = SecurityScanner().scan_code("x = 1", "python", "sid")
        self.assertEqual(result["tool"], "error")


# ---------------------------------------------------------------------------
# Semgrep output parsing
# ---------------------------------------------------------------------------

class TestSemgrepOutputParsing(unittest.TestCase):

    def test_findings_returned_correctly(self):
        finding = {"check_id": "rule.id", "start": {"line": 5}, "extra": {"message": "bad"}}
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_semgrep_stdout([finding]))):
            result = SecurityScanner().scan_code("// code", "javascript", "sid")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["check_id"], "rule.id")

    def test_empty_output_returns_empty_findings(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout="")):
            result = SecurityScanner().scan_code("// code", "java", "sid")
        self.assertEqual(result["tool"], "semgrep")
        self.assertEqual(result["findings"], [])

    def test_semgrep_exit_code_2_returns_error(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(2, stderr="crash")):
            result = SecurityScanner().scan_code("// code", "java", "sid")
        self.assertEqual(result["tool"], "error")


# ---------------------------------------------------------------------------
# Unsupported language
# ---------------------------------------------------------------------------

class TestUnsupportedLanguage(unittest.TestCase):

    def test_unsupported_language_returns_error(self):
        result = SecurityScanner().scan_code("code", "cobol", "sid")
        self.assertEqual(result["tool"], "error")
        self.assertIn("Unsupported", result["error"])

    def test_empty_language_returns_error(self):
        result = SecurityScanner().scan_code("code", "", "sid")
        self.assertEqual(result["tool"], "error")


# ---------------------------------------------------------------------------
# Temp file cleanup
# ---------------------------------------------------------------------------

class TestTempFileCleanup(unittest.TestCase):

    def test_temp_dir_deleted_after_scan(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_bandit_stdout())):
            scanner = SecurityScanner()
            scanner.scan_code("x = 1", "python", "sid")
        # temp_dir should have been cleaned up by the context manager
        self.assertIsNotNone(scanner.temp_dir)
        self.assertFalse(os.path.exists(scanner.temp_dir),
                         "Temp directory should be deleted after scan completes")


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------

class TestTimeoutEnforcement(unittest.TestCase):

    def test_custom_timeout_passed_to_subprocess(self):
        with patch("scanner.subprocess.run", return_value=_mock_run(stdout=_bandit_stdout())) as mock_run:
            scan_code_with_timeout("x = 1", "python", "sid", timeout=42)
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("timeout"), 42)

    def test_timeout_expiry_returns_error(self):
        import subprocess
        with patch("scanner.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="bandit", timeout=1)):
            result = SecurityScanner().scan_code("x = 1", "python", "sid")
        self.assertEqual(result["tool"], "error")


if __name__ == "__main__":
    unittest.main()
