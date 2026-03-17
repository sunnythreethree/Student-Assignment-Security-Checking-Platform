"""
validator.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Input validation for POST /scan requests.
All validation logic is isolated here so it can be unit tested independently.
"""

SUPPORTED_LANGUAGES = ["python", "java", "javascript"]
MAX_CODE_BYTES = 1 * 1024 * 1024  # 1 MB


def validate_scan_request(body: dict) -> tuple[bool, str]:
    """
    Validate the incoming scan request body.

    Returns:
        (True, "")           if valid
        (False, error_msg)   if invalid
    """

    # --- code field ---
    code = body.get("code", "")
    if not isinstance(code, str) or not code.strip():
        return False, "Field 'code' is required and must be a non-empty string."

    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        return False, "Field 'code' exceeds the 1 MB size limit."

    # --- language field ---
    language = body.get("language", "")
    if not isinstance(language, str) or not language.strip():
        return False, "Field 'language' is required."

    if language.lower() not in SUPPORTED_LANGUAGES:
        return False, (
            f"Field 'language' must be one of: {', '.join(SUPPORTED_LANGUAGES)}. "
            f"Got: '{language}'."
        )

    # --- student_id field ---
    student_id = body.get("student_id", "")
    if not isinstance(student_id, str) or not student_id.strip():
        return False, "Field 'student_id' is required and must be a non-empty string."

    return True, ""


def normalize(body: dict) -> dict:
    """
    Return a clean copy of the body with normalized field values.
    Call this only after validate_scan_request passes.
    """
    return {
        "code":       body["code"].strip(),
        "language":   body["language"].strip().lower(),
        "student_id": body["student_id"].strip(),
    }
