"""
validator.py — Lambda A
Jingsi Zhang | CS6620 Group 9

Input validation for POST /scan requests.
All validation logic is isolated here so it can be unit tested independently.
"""

import os

SUPPORTED_LANGUAGES = ["python", "java", "javascript", "typescript", "go", "ruby", "c", "cpp"]
MAX_CODE_BYTES = int(os.environ.get("MAX_CODE_BYTES", str(1 * 1024 * 1024)))  # default 1 MB


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

    return True, ""


def normalize(body: dict) -> dict:
    """
    Return a clean copy of the body with normalized field values.
    Call this only after validate_scan_request passes.
    Note: student_id is resolved from X-Student-Key header, not from body.
    """
    return {
        "code":     body["code"].strip(),
        "language": body["language"].strip().lower(),
    }
