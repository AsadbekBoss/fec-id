"""Camera service settings (env / .env). Secrets have no defaults."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Shared secret with the Core API (= CAMERA_SERVICE_TOKEN there). Used in both directions.
    service_token: SecretStr

    # Core API for configuration and status. Empty = standalone (on-demand operations only).
    core_api_url: str | None = None
    sync_interval_seconds: float = Field(default=10.0, ge=1, le=300)

    # Optional: recognition service, used ONLY by the quality test to measure faces.
    recognition_url: str | None = None
    recognition_token: SecretStr | None = None

    # Folder for TEST cameras that replay files (images or video). Kept out of git (data/).
    test_media_dir: Path = REPO_ROOT / "data" / "test-media"

    # Stream handling
    open_timeout_ms: int = Field(default=8000, ge=500, le=60000)
    read_timeout_ms: int = Field(default=8000, ge=500, le=60000)
    stall_timeout_seconds: float = Field(default=10.0, ge=1, le=120)  # no frame -> reconnect
    reconnect_initial_seconds: float = Field(default=1.0, ge=0.01, le=60)
    reconnect_max_seconds: float = Field(default=60.0, ge=0.05, le=600)
    live_recognition_fps: float = Field(default=2.0, ge=0.2, le=10)  # frames/s sent to recognition
    max_concurrent_operations: int = Field(default=4, ge=1, le=32)  # probe/snapshot/quality

    # Snapshots are produced on demand, held only in memory and never written to disk.
    snapshot_max_side: int = Field(default=1280, ge=160, le=3840)
    jpeg_quality: int = Field(default=85, ge=30, le=95)

    @field_validator("service_token")
    @classmethod
    def _strong(cls, v: SecretStr) -> SecretStr:
        t = v.get_secret_value()
        if len(t) < 24 or t in {"generate-me", "change-me"}:
            raise ValueError("SERVICE_TOKEN must be a random secret of at least 24 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # secrets come from the environment
