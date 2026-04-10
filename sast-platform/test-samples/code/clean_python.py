"""
clean_python.py — clean Python with no security issues.
Expected: 0 findings (negative / false-positive check).
"""
import hashlib
import hmac
import os


def hash_password(password: str, salt: bytes) -> str:
    """Secure password hashing using SHA-256 with salt."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return dk.hex()


def verify_token(token: str, expected: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    return hmac.compare_digest(token, expected)


def generate_salt() -> bytes:
    return os.urandom(16)
