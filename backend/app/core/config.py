from pathlib import Path
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    db_host: str = "127.0.0.1"
    db_port: int = 2881
    db_name: str = "course_assistant"
    db_user: str = "course_app"
    db_password: str
    seekdb_root_password: str
    jwt_secret: str = Field(min_length=32)
    cookie_secure: bool = False
    allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000"
    demo_password: str
    storage_dir: Path = ROOT / "storage/uploads"
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    worker_lease_seconds: int = Field(default=30, ge=3, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    worker_retry_base_seconds: int = Field(default=5, ge=1, le=300)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    worker_task_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    dashscope_api_key: SecretStr | None = Field(default=None, repr=False)
    dashscope_workspace_id: str = ""
    dashscope_region: Literal["cn-beijing", "ap-southeast-1"] = "cn-beijing"
    embedding_model: Literal["qwen3.7-text-embedding"] = "qwen3.7-text-embedding"
    embedding_dimensions: int = 1024
    embedding_timeout_seconds: float = Field(default=20, ge=1, le=60)
    embedding_max_chunks_per_document: int = Field(default=32, ge=1, le=512)
    retrieval_requests_per_minute: int = Field(default=6, ge=1, le=60)
    retrieval_deadline_seconds: int = Field(default=60, ge=5, le=180)

    @field_validator("embedding_dimensions")
    @classmethod
    def supported_embedding_dimensions(cls, value: int) -> int:
        if value not in {256, 512, 768, 1024, 1536, 2048, 2560}:
            raise ValueError("Unsupported embedding dimensions")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
