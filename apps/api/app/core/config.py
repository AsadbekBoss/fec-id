"""Typed application settings loaded from environment variables / .env.

Secrets have NO defaults here: the app refuses to start if they are missing.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "AI Employee Attendance API"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "attendance"
    postgres_user: str = "attendance"
    postgres_password: SecretStr  # required, no default
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_connect_timeout_seconds: int = 5
    db_statement_timeout_ms: int = 30_000

    # --- Encryption (ARCHITECTURE §8) ---
    # Keyring of 32-byte master keys: "1:<base64url>,2:<base64url>". Required, no default.
    data_encryption_keys: SecretStr
    # Key id used for NEW ciphertexts; older ids remain for decryption (rotation).
    data_encryption_active_key_id: int = 1

    # --- Authentication (ARCHITECTURE §7) ---
    # Ed25519 signing keys for access tokens: "1:<base64url 32-byte seed>[,2:...]". Required.
    jwt_signing_keys: SecretStr
    jwt_active_key_id: int = 1
    jwt_issuer: str = "attendance-api"
    jwt_audience: str = "attendance-web"
    jwt_leeway_seconds: int = Field(default=10, ge=0, le=60)
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    session_days: int = Field(default=7, ge=1, le=30)  # absolute session / refresh lifetime
    refresh_cookie_name: str = "attendance_refresh"
    refresh_cookie_secure: bool = True  # browsers accept Secure cookies on http://localhost
    refresh_cookie_path: str = "/api/v1/auth"

    # Login protection
    login_max_failures: int = Field(default=5, ge=1)  # per account, then temporary lock
    login_lockout_minutes: int = Field(default=15, ge=1)
    login_ip_max_failures: int = Field(default=20, ge=1)  # per client IP per window (DB-backed)
    login_ip_window_minutes: int = Field(default=15, ge=1)
    login_rate_per_minute: int = Field(default=10, ge=1)  # per IP, in-memory, before hashing
    trusted_proxies: list[str] = Field(default_factory=list)  # IPs allowed to set X-Forwarded-For

    # --- File storage (encrypted at rest) ---
    storage_dir: str = "storage"  # relative to the API working directory; git-ignored
    max_photo_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    max_document_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)

    # --- Camera service (apps/camera, Phase 10). Unset = camera operations return 503. ---
    camera_service_url: str | None = None  # e.g. http://127.0.0.1:8200 (internal network only)
    # Shared secret for BOTH directions (Core API <-> camera service). >= 24 chars.
    camera_service_token: SecretStr | None = None
    camera_service_timeout_seconds: float = Field(default=30.0, ge=1, le=120)
    camera_status_stale_seconds: int = Field(default=60, ge=10)  # no report -> shown as stale

    # --- Recognition service (apps/recognition). Unset = enrolment / live view return 503. ---
    recognition_service_url: str | None = None  # e.g. http://127.0.0.1:8100
    # Token the Core API presents TO the recognition service (= its SERVICE_TOKEN). The camera
    # service holds the same value to send frames, so it must NOT grant gallery access.
    recognition_service_token: SecretStr | None = None
    # Separate token the recognition service presents to read the encrypted gallery
    # (= CORE_API_TOKEN of the recognition service). Only the recognition service knows it.
    recognition_gallery_token: SecretStr | None = None
    max_face_templates: int = Field(default=5, ge=1, le=20)  # per employee

    # Argon2id parameters (OWASP-aligned defaults). Lower only in tests.
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_kib: int = Field(default=65536, ge=1024)
    argon2_parallelism: int = Field(default=4, ge=1)

    @field_validator(
        "camera_service_token", "recognition_service_token", "recognition_gallery_token"
    )
    @classmethod
    def _strong_service_token(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and v.get_secret_value() and len(v.get_secret_value()) < 24:
            raise ValueError("service tokens must be at least 24 characters")
        return v if v is not None and v.get_secret_value() else None

    @property
    def database_url(self) -> URL:
        """SQLAlchemy URL built from parts. Plain property (not serialised) so the
        password never appears in model_dump()/repr."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment / .env
