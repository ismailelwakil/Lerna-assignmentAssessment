"""Security: PBKDF2 password hashing (stdlib), signed session tokens,
upload validation (magic bytes), untrusted-content wrapping."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid

from .config import get_settings
from .exceptions import AuthError, ValidationError

# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    if not password or len(password) < 6:
        raise ValidationError("Password must be at least 6 characters.")
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return hmac.compare_digest(candidate, digest)


# ------------------------------------------------------------------ tokens
def _sign(payload_b64: str) -> str:
    return hmac.new(get_settings().secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def create_session_token(user_id: str, role: str, ttl_hours: int = 72) -> str:
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": user_id, "role": role,
        "exp": int(time.time()) + ttl_hours * 3600,
    }).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def decode_session_token(token: str) -> dict:
    import base64
    import json
    if not token or "." not in token:
        raise AuthError("Invalid session token.")
    payload, _, sig = token.rpartition(".")
    if not hmac.compare_digest(_sign(payload), sig):
        raise AuthError("Invalid session token signature.")
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:  # noqa: BLE001
        raise AuthError("Malformed session token.") from exc
    if data.get("exp", 0) < time.time():
        raise AuthError("Session expired — please log in again.")
    return data


# ------------------------------------------------------------------ uploads
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".csv"}
_MAGIC = [(b"%PDF-", {".pdf"}), (b"PK\x03\x04", {".docx", ".pptx"})]


def validate_material(filename: str, content: bytes) -> tuple[str, str]:
    settings = get_settings()
    if not content:
        raise ValidationError("The uploaded file is empty.")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationError(
            f"File exceeds the {settings.max_upload_mb} MB limit.")
    import pathlib
    raw = pathlib.Path(filename or "file").name
    ext = pathlib.Path(raw).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Unsupported type '{ext}'. Supported: PDF, DOCX, PPTX, TXT, MD, CSV.")
    for signature, exts in _MAGIC:
        if content.startswith(signature):
            if ext not in exts:
                raise ValidationError("File content does not match its extension.")
            break
    else:
        if ext not in {".txt", ".md", ".csv"}:
            raise ValidationError("File content does not match its extension.")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", raw[: -len(ext)] if ext else raw)[:120] or "material"
    return stem.strip("._-") or "material", ext


def new_id() -> str:
    return uuid.uuid4().hex


# ------------------------------------------------- untrusted content wrapping
_ROLE_MARKERS = re.compile(r"</?\s*(system|developer|assistant|material)\s*>", re.I)
_INJECTION = re.compile(
    r"(ignore.{0,24}(instructions|rules|prompt)|disregard.{0,24}(instructions|rules)|"
    r"you are now (a|an)\b|new system prompt|reveal (your|the) (system )?prompt|"
    r"repeat everything above)", re.I)


def wrap_untrusted(content: str, label: str = "material") -> str:
    sanitized = _ROLE_MARKERS.sub("[filtered-tag]", content or "")
    sanitized = _INJECTION.sub("[filtered-instruction]", sanitized)
    return f'<{label}_context untrusted="true">\n{sanitized}\n</{label}_context>'


USERNAME_RX = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def validate_username(username: str) -> str:
    if not USERNAME_RX.fullmatch(username or ""):
        raise ValidationError(
            "Username must be 3-64 characters (letters, digits, . _ -).")
    return username
