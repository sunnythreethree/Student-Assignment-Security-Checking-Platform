"""
test_validator.py — Unit tests for lambda_a/validator.py
Jingsi Zhang | CS6620 Group 9

Run with:
    pytest tests/unit/test_validator.py -v
"""

import pytest

from validator import validate_scan_request, normalize, SUPPORTED_LANGUAGES, MAX_CODE_BYTES


# ── Helpers ────────────────────────────────────────────────────────────────────

def _valid_body(**overrides):
    base = {
        "code":       "print('hello')",
        "language":   "python",
        "student_id": "neu123456",
    }
    base.update(overrides)
    return base


# ── validate_scan_request: happy path ─────────────────────────────────────────

class TestValidateScanRequest:

    def test_valid_python(self):
        ok, msg = validate_scan_request(_valid_body())
        assert ok is True
        assert msg == ""

    def test_valid_java(self):
        ok, _ = validate_scan_request(_valid_body(code="System.out.println(1);", language="java"))
        assert ok is True

    def test_valid_javascript(self):
        ok, _ = validate_scan_request(_valid_body(code="console.log(1);", language="javascript"))
        assert ok is True

    def test_valid_all_supported_languages(self):
        for lang in SUPPORTED_LANGUAGES:
            ok, msg = validate_scan_request(_valid_body(language=lang))
            assert ok is True, f"Expected {lang} to be accepted, got: {msg}"

    def test_language_case_insensitive(self):
        """'Python' and 'JAVA' should be accepted — validator lowercases before checking."""
        for variant in ("Python", "JAVA", "JavaScript", "TypeScript"):
            ok, _ = validate_scan_request(_valid_body(language=variant))
            assert ok is True, f"Case variant '{variant}' should be valid"

    # ── code field ──────────────────────────────────────────────────────────

    def test_missing_code_field(self):
        body = {"language": "python", "student_id": "neu123"}
        ok, msg = validate_scan_request(body)
        assert ok is False
        assert "code" in msg.lower()

    def test_empty_code_string(self):
        ok, msg = validate_scan_request(_valid_body(code=""))
        assert ok is False
        assert "code" in msg.lower()

    def test_whitespace_only_code(self):
        ok, msg = validate_scan_request(_valid_body(code="   \n\t  "))
        assert ok is False
        assert "code" in msg.lower()

    def test_code_not_a_string(self):
        ok, msg = validate_scan_request(_valid_body(code=12345))
        assert ok is False
        assert "code" in msg.lower()

    def test_code_exceeds_1mb(self):
        big_code = "x" * (MAX_CODE_BYTES + 1)
        ok, msg = validate_scan_request(_valid_body(code=big_code))
        assert ok is False
        assert "1 MB" in msg or "size" in msg.lower()

    def test_code_exactly_1mb_is_valid(self):
        """Boundary: exactly MAX_CODE_BYTES bytes should pass."""
        boundary_code = "a" * MAX_CODE_BYTES
        ok, _ = validate_scan_request(_valid_body(code=boundary_code))
        assert ok is True

    def test_code_with_unicode_within_limit(self):
        """Multi-byte UTF-8 chars count towards the byte limit, not char count."""
        # Each Chinese character is 3 bytes in UTF-8;
        # 300_000 chars × 3 bytes = 900_000 bytes < 1_048_576 bytes
        unicode_code = "中" * 300_000
        ok, _ = validate_scan_request(_valid_body(code=unicode_code))
        assert ok is True

    # ── language field ──────────────────────────────────────────────────────

    def test_missing_language_field(self):
        body = {"code": "x=1", "student_id": "neu123"}
        ok, msg = validate_scan_request(body)
        assert ok is False
        assert "language" in msg.lower()

    def test_empty_language_string(self):
        ok, msg = validate_scan_request(_valid_body(language=""))
        assert ok is False
        assert "language" in msg.lower()

    def test_unsupported_language_cobol(self):
        ok, msg = validate_scan_request(_valid_body(language="cobol"))
        assert ok is False
        assert "language" in msg.lower()

    def test_unsupported_language_sql(self):
        ok, msg = validate_scan_request(_valid_body(language="sql"))
        assert ok is False

    def test_unsupported_language_lists_valid_options(self):
        """Error message should include the list of valid languages."""
        ok, msg = validate_scan_request(_valid_body(language="fortran"))
        assert ok is False
        assert "python" in msg.lower()

    # ── student_id field ────────────────────────────────────────────────────

    def test_missing_student_id_field(self):
        body = {"code": "x=1", "language": "python"}
        ok, msg = validate_scan_request(body)
        assert ok is False
        assert "student_id" in msg.lower()

    def test_empty_student_id_string(self):
        ok, msg = validate_scan_request(_valid_body(student_id=""))
        assert ok is False
        assert "student_id" in msg.lower()

    def test_whitespace_only_student_id(self):
        ok, msg = validate_scan_request(_valid_body(student_id="   "))
        assert ok is False

    def test_student_id_not_a_string(self):
        ok, msg = validate_scan_request(_valid_body(student_id=99999))
        assert ok is False

    # ── edge cases ──────────────────────────────────────────────────────────

    def test_empty_body(self):
        ok, msg = validate_scan_request({})
        assert ok is False

    def test_none_values(self):
        ok, _ = validate_scan_request({"code": None, "language": "python", "student_id": "neu123"})
        assert ok is False

    def test_extra_fields_are_ignored(self):
        """Unknown extra fields should not cause validation to fail."""
        body = _valid_body(extra_field="ignored", another=123)
        ok, _ = validate_scan_request(body)
        assert ok is True


# ── normalize ──────────────────────────────────────────────────────────────────

class TestNormalize:

    def test_strips_whitespace_from_code(self):
        result = normalize(_valid_body(code="  print(1)  "))
        assert result["code"] == "print(1)"

    def test_lowercases_language(self):
        result = normalize(_valid_body(language="Python"))
        assert result["language"] == "python"

    def test_strips_whitespace_from_student_id(self):
        result = normalize(_valid_body(student_id="  neu123  "))
        assert result["student_id"] == "neu123"

    def test_combined_normalization(self):
        result = normalize({
            "code":       "  x = 1  ",
            "language":   "JAVASCRIPT",
            "student_id": " abc ",
        })
        assert result["code"] == "x = 1"
        assert result["language"] == "javascript"
        assert result["student_id"] == "abc"

    def test_returns_only_known_fields(self):
        """normalize() should return exactly the three fields, no extras."""
        result = normalize(_valid_body())
        assert set(result.keys()) == {"code", "language", "student_id"}
