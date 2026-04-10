"""
this module runs security scans on code
python -> bandit
java/javascript/typescript/go/ruby/c/cpp -> semgrep
"""
import json
import os
import subprocess
import sys
import tempfile
import logging

import pattern_scanner as _pattern_scanner

logger = logging.getLogger(__name__)


def _semgrep_available() -> bool:
    """Return True if semgrep can be invoked via the current Python executable."""
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'semgrep', '--version'],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False

# Prefer locally-bundled rules (written into the image at build time) so the
# container does not need outbound internet access at scan time.  Fall back to
# the published p/owasp-top-ten ruleset if the local file is absent (e.g. local
# development outside Docker).  Override with the SEMGREP_CONFIG env var.
_LOCAL_RULES_DIR = "/semgrep-rules"
_LOCAL_RULES_FILE = "/semgrep-rules/rules.yaml"
# Prefer the rules directory (javascript + owasp + secrets).
# Fall back to a single rules.yaml, then to the online registry.
if os.environ.get("SEMGREP_CONFIG"):
    _SEMGREP_CONFIG = os.environ["SEMGREP_CONFIG"]
elif os.path.isdir(_LOCAL_RULES_DIR) and any(
    f.endswith(".yaml") for f in os.listdir(_LOCAL_RULES_DIR)
):
    _SEMGREP_CONFIG = _LOCAL_RULES_DIR
elif os.path.exists(_LOCAL_RULES_FILE):
    _SEMGREP_CONFIG = _LOCAL_RULES_FILE
else:
    _SEMGREP_CONFIG = "p/owasp-top-ten"
    logger.warning("Local semgrep rules not found; falling back to %s (requires internet)", _SEMGREP_CONFIG)


class SecurityScanner:
    """
    wrapper class to handle different scanners
    """

    def __init__(self):
        self.temp_dir = None

    def scan_code(self, code: str, language: str, scan_id: str, timeout: int = 300):
        """
        main entry
        decide which tool to use based on language
        """
        try:
            # create a temp folder to store code file；will be cleaned up after scan
            with tempfile.TemporaryDirectory() as temp_dir:
                self.temp_dir = temp_dir

                # pick scanner based on language
                if language.lower() == 'python':
                    return self._scan_with_bandit(code, scan_id, timeout)
                elif language.lower() in ['java', 'javascript', 'js', 'typescript', 'go', 'ruby', 'c', 'cpp']:
                    if _semgrep_available():
                        return self._scan_with_semgrep(code, language, scan_id, timeout)
                    logger.info(
                        "semgrep not available — using built-in pattern scanner for %s (scan_id: %s)",
                        language, scan_id,
                    )
                    result = _pattern_scanner.scan(code, language, scan_id)
                    return {'scan_id': scan_id, 'language': language, 'tool': 'pattern', 'raw_output': result}
                else:
                    raise ValueError(f"Unsupported language type: {language}")

        except Exception as e:
            logger.error(f"Scan failed - scan_id: {scan_id}, error: {str(e)}")
            return {
                'scan_id': scan_id,
                'language': language,
                'tool': 'error',
                'error': str(e),
                'findings': [],
                'summary': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
            }

    def _scan_with_bandit(self, code: str, scan_id: str, timeout: int = 300):
        """
        Use Bandit to scan Python code
        """
        logger.info("starting bandit scan %s", scan_id)

        # write code in temp python file
        python_file = os.path.join(self.temp_dir, f"code_{scan_id}.py")
        with open(python_file, 'w', encoding='utf-8') as f:
            f.write(code)

        try:
            # Use python -m bandit to avoid shebang path issues in zip deployments
            import sys
            cmd = [
                sys.executable, '-m', 'bandit',
                '-r', python_file,
                '-f', 'json',
                '--silent'
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.temp_dir
            )

            # bandit return code:
            # 0 -> no issue
            # 1 -> issues found
            # >=2 -> error
            if result.returncode >= 2:
                raise RuntimeError(f"Bandit execution failed: {result.stderr}")

            # gets JSON output
            if result.stdout.strip():
                bandit_output = json.loads(result.stdout)
            else:
                bandit_output = {
                    "results": [],
                    "metrics": {
                        "CONFIDENCE.HIGH": 0,
                        "CONFIDENCE.MEDIUM": 0,
                        "CONFIDENCE.LOW": 0,
                        "SEVERITY.HIGH": 0,
                        "SEVERITY.MEDIUM": 0,
                        "SEVERITY.LOW": 0
                    }
                }

            logger.info("bandit done, issues: %d", len(bandit_output.get('results', [])))

            return {
                'scan_id': scan_id,
                'language': 'python',
                'tool': 'bandit',
                'raw_output': bandit_output,
                'findings': bandit_output.get('results', []),
                'metrics': bandit_output.get('metrics', {})
            }

        except subprocess.TimeoutExpired:
            raise RuntimeError("Bandit scan timeout")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Bandit output failed: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Bandit scan exception: {str(e)}")

    def _scan_with_semgrep(self, code: str, language: str, scan_id: str, timeout: int = 300):
        """
        use semgrep for java / js / typescript / go / ruby / c / cpp
        """
        logger.info("starting semgrep scan %s (config: %s)", scan_id, _SEMGREP_CONFIG)

        # Decide file extension based on language
        ext_map = {
            'java': '.java',
            'javascript': '.js',
            'js': '.js',
            'typescript': '.ts',
            'go': '.go',
            'ruby': '.rb',
            'c': '.c',
            'cpp': '.cpp'
        }

        file_ext = ext_map[language.lower()]
        code_file = os.path.join(self.temp_dir, f"code_{scan_id}{file_ext}")

        # Write code to temp file
        with open(code_file, 'w', encoding='utf-8') as f:
            f.write(code)

        try:
            # Run Semgrep using locally-bundled rules (see _SEMGREP_CONFIG above)
            cmd = [
                sys.executable, '-m', 'semgrep',
                f'--config={_SEMGREP_CONFIG}',
                '--json',          # JSON output
                '--quiet',         # Reduce output noise
                '--no-git-ignore', # Ignore .gitignore
                code_file
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.temp_dir
            )

            # Semgrep return codes: 0=no findings, 1=findings found, >=2=error
            if result.returncode >= 2:
                raise RuntimeError(f"Semgrep execution failed (rc={result.returncode}): {result.stderr}")

            # gets JSON output
            if result.stdout.strip():
                semgrep_output = json.loads(result.stdout)
            else:
                # Empty stdout with rc<2 means either no findings or semgrep crashed
                # Log stderr to help diagnose silent failures
                if result.stderr.strip():
                    logger.warning("Semgrep stderr: %s", result.stderr[:500])
                semgrep_output = {"results": []}

            results = semgrep_output.get('results', [])
            logger.info("semgrep done, issues: %d", len(results))

            return {
                'scan_id': scan_id,
                'language': language,
                'tool': 'semgrep',
                'raw_output': semgrep_output,
                'findings': results
            }

        except subprocess.TimeoutExpired:
            raise RuntimeError("Semgrep scan timed out")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Semgrep output parsing failed: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Semgrep scan exception: {str(e)}")


def scan_code_with_timeout(code: str, language: str, scan_id: str, timeout: int = 300):
    """
    wrapper for scanner with enforced timeout passed through to subprocess calls
    """
    scanner = SecurityScanner()
    return scanner.scan_code(code, language, scan_id, timeout)
