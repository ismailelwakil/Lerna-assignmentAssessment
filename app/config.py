"""Configuration — reuses the SAME provider environment variables as the
existing EDUnation projects (no new keys)."""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # setdefault would keep an EXISTING-but-EMPTY env var and mask the
            # .env value (key then appears "not configured"). Only a non-empty
            # existing environment value takes precedence.
            if os.environ.get(key):  # non-empty existing value wins
                continue
            os.environ[key] = value


_load_dotenv()


def _s(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    def __init__(self) -> None:
        # REUSED provider keys (same env var names as the existing projects)
        self.openrouter_api_key: str = _s("OPENROUTER_API_KEY", "")
        self.openrouter_base: str = _s("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.openrouter_model: str = _s("OPENROUTER_FAST_MODEL", "google/gemini-3.8-flash")
        self.groq_api_key: str = _s("GROQ_API_KEY", "")
        self.groq_model: str = _s("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.gemini_api_key: str = _s("GEMINI_API_KEY", "")
        self.gemini_base: str = _s("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
        self.qdrant_url: str = _s("QDRANT_URL", "").rstrip("/")
        self.qdrant_api_key: str = _s("QDRANT_API_KEY", "")

        # module-owned configuration
        self.secret: str = _s("ASSESS_SECRET", "dev-only-secret-change-me")
        self.db_url: str = _s("ASSESS_DB_URL", f"sqlite:///{(DATA_DIR / 'assessment.db').as_posix()}")
        self.llm_timeout: float = float(_s("ASSESS_LLM_TIMEOUT", "90"))
        self.max_upload_mb: int = _i("ASSESS_MAX_UPLOAD_MB", 25)
        self.embed_model: str = _s("ASSESS_EMBED_MODEL", "gemini-embedding-001")
        self.embed_dim: int = _i("ASSESS_EMBED_DIM", 768)
        self.allow_external_knowledge: bool = _s("ASSESS_ALLOW_EXTERNAL_KNOWLEDGE", "false").lower() in {"1", "true", "yes"}

        # evaluation policy
        self.weakness_threshold_pct: float = float(_s("ASSESS_WEAKNESS_PCT", "60"))
        self.strength_threshold_pct: float = float(_s("ASSESS_STRENGTH_PCT", "80"))
        self.evidence_top_k: int = _i("ASSESS_EVIDENCE_TOP_K", 4)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "storage").mkdir(parents=True, exist_ok=True)

    @property
    def llm_available(self) -> bool:
        return bool(self.openrouter_api_key or self.groq_api_key)

    @property
    def embeddings_available(self) -> bool:
        return bool(self.gemini_api_key)


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
