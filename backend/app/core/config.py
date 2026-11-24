from pathlib import Path
from functools import lru_cache

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
