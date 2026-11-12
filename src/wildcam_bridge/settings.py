"""Runtime configuration, read from environment variables and the (git-ignored) ``.env`` file.

Every secret (ERDA sharelink, Label Studio token, API key hashes) lives ONLY in ``.env``.
See ``.env.example`` for a documented template.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ------------------------------------------------------------------ bridge HTTP API
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8090
    # Public path prefix under which nginx exposes the API (https://<host>/bridge/...).
    bridge_root_path: str = "/bridge"
    # Comma-separated list of "sha256:<hex>" hashes of the accepted API keys.
    # Generate with: `wildcam-bridge gen-api-key`.
    bridge_api_key_hashes: SecretStr = SecretStr("")
    # Reject any request that did not arrive over TLS (checked via X-Forwarded-Proto set by nginx).
    bridge_require_https: bool = True
    bridge_enable_docs: bool = True
    # Job database, uploaded CSVs and per-job logs.
    bridge_state_dir: Path = Path("var")
    bridge_max_csv_bytes: int = Field(default=20_000_000, gt=0)
    bridge_max_rows: int = Field(default=20_000, gt=0)

    # ------------------------------------------------------------------ AU ERDA (SFTP)
    erda_host: str = "io.erda.au.dk"
    erda_port: int = 2222
    # ERDA sharelink ID. A sharelink is used as BOTH username and password, so access
    # is not bound to a personal ERDA account.
    erda_sharelink: SecretStr = SecretStr("")
    # Pinned host key(s). The connection is refused if ERDA presents another key.
    erda_known_hosts: Path = Path("config/erda_known_hosts")
    # Folder inside the share in which the CSV paths are rooted ("" = share root).
    erda_remote_root: str = ""
    # Prefix removed from the CSV `file` column before resolving it on ERDA.
    # The sharelink root IS the ERDA "wildcams" folder, while the SQL DB stores
    # "wildcams/data/<site>/...", so the default strips "wildcams/".
    erda_strip_prefix: str = "wildcams/"
    erda_concurrency: int = Field(default=8, ge=1, le=64)
    erda_retries: int = Field(default=3, ge=0, le=10)
    erda_connect_timeout: float = 30.0

    # ------------------------------------------------------------------ local media storage
    # Must equal LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT of the Label Studio service.
    ls_data_root: Path = Path("/home/wildcam/labelstudio-data")
    # Each job downloads into <ls_data_root>/<ls_data_subdir>/<job_id>/.
    ls_data_subdir: str = "bridge"
    # Free space that must remain on disk AFTER a download (GB, 1e9 bytes).
    disk_reserve_gb: float = Field(default=10.0, ge=0)

    # ------------------------------------------------------------------ Label Studio
    ls_url: str = "http://127.0.0.1:8081"
    ls_public_url: str = "https://wildcam.ecoscience.dk"
    # Personal Access Token (JWT refresh token) or legacy token of the bridge service account.
    ls_api_token: SecretStr = SecretStr("")
    ls_label_config: Path = Path("config/label_config.xml")
    # Name of the <Choices> control the model prediction is mapped onto (pre-annotation).
    ls_prediction_from_name: str = "species_label"
    ls_timeout: float = 300.0

    @field_validator("bridge_root_path")
    @classmethod
    def _normalise_root_path(cls, value: str) -> str:
        value = value.strip()
        if not value or value == "/":
            return ""
        return "/" + value.strip("/")

    @field_validator("ls_url", "ls_public_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("ls_data_subdir")
    @classmethod
    def _safe_subdir(cls, value: str) -> str:
        value = value.strip("/")
        if not value or ".." in value.split("/"):
            raise ValueError("ls_data_subdir must be a non-empty relative path without '..'")
        return value

    # Convenience ---------------------------------------------------------------------
    @property
    def api_key_hashes(self) -> list[str]:
        raw = self.bridge_api_key_hashes.get_secret_value()
        return [h.strip() for h in raw.split(",") if h.strip()]

    @property
    def disk_reserve_bytes(self) -> int:
        return int(self.disk_reserve_gb * 1e9)

    @property
    def jobs_media_root(self) -> Path:
        return self.ls_data_root / self.ls_data_subdir

    @property
    def jobs_state_root(self) -> Path:
        return self.bridge_state_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.bridge_state_dir / "bridge.sqlite3"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
