"""Runtime configuration read from environment variables (never from the DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader: KEY=VALUE lines, no dependency. Existing env wins."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(slots=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./data/statement_splitter.db")
    )
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.0-flash")
    ai_timeout_seconds: float = field(default_factory=lambda: float(os.environ.get("AI_TIMEOUT_SECONDS", "15")))
    ai_min_confidence: float = field(default_factory=lambda: float(os.environ.get("AI_MIN_CONFIDENCE", "0.6")))
    max_upload_mb: int = field(default_factory=lambda: int(os.environ.get("MAX_UPLOAD_MB", "10")))

    @property
    def ai_configured(self) -> bool:
        return bool(self.gemini_api_key)


_load_dotenv(Path(".env"))
settings = Settings()
