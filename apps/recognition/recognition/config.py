"""Recognition service settings (environment / .env). Secrets have no defaults."""

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

    # --- models ---
    models_dir: Path = REPO_ROOT / "models"
    manifest_path: Path = REPO_ROOT / "models" / "manifest.yaml"
    detector: Literal["yunet", "scrfd_10g"] = "yunet"
    embedder: Literal["sface", "arcface_w600k_r50"] = "sface"
    # Non-commercial weights may ONLY be loaded for local research outside production.
    allow_research_models: bool = False
    onnx_threads: int = Field(default=0, ge=0)  # 0 = runtime default

    # --- detection ---
    detection_score_threshold: float = Field(default=0.6, ge=0, le=1)  # detector output filter
    max_image_side: int = Field(default=1920, ge=320)  # larger inputs are downscaled first
    max_upload_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)

    # --- quality gate (faces failing it are NEVER identified) ---
    min_detection_score: float = Field(default=0.85, ge=0, le=1)
    min_face_width_px: int = Field(default=80, ge=16)
    min_interocular_px: float = Field(default=28.0, ge=4)
    min_blur_variance: float = Field(default=40.0, ge=0)  # Laplacian variance of aligned 112x112
    min_brightness: float = Field(default=40.0, ge=0, le=255)
    max_brightness: float = Field(default=215.0, ge=0, le=255)
    max_abs_yaw_deg: float = Field(default=30.0, ge=0, le=90)
    max_abs_roll_deg: float = Field(default=20.0, ge=0, le=90)

    # --- matching (all must hold for MATCHED; otherwise UNKNOWN or AMBIGUOUS) ---
    match_threshold: float | None = None  # None = model reference_threshold from manifest
    min_margin: float = Field(default=0.05, ge=0, le=1)  # top1 - best other employee
    # Scores in [threshold, threshold + uncertain_band) are AMBIGUOUS, never auto-identified.
    uncertain_band: float = Field(default=0.03, ge=0, le=1)

    # --- security ---
    service_token: SecretStr  # Bearer token for /internal/* (required, no default)
    data_encryption_keys: SecretStr  # same keyring format as the Core API
    data_encryption_active_key_id: int = 1
    gallery_path: Path | None = None  # encrypted gallery file (evaluation only)
    # Core API: source of the enrolled-employee gallery (encrypted templates). Empty = no sync.
    core_api_url: str | None = None
    # Presented to the Core API (= RECOGNITION_GALLERY_TOKEN there). Deliberately NOT the
    # SERVICE_TOKEN: the camera service knows SERVICE_TOKEN and must not read the gallery.
    core_api_token: SecretStr | None = None
    gallery_sync_seconds: float = Field(default=30.0, ge=2, le=3600)

    @field_validator("service_token")
    @classmethod
    def _strong_token(cls, v: SecretStr) -> SecretStr:
        token = v.get_secret_value()
        if len(token) < 24 or token in {"generate-me", "change-me"}:
            raise ValueError("SERVICE_TOKEN must be a random secret of at least 24 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # secrets come from the environment
