from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    embed_model: str = "voyage-4-lite"
    voyage_api_key: str | None = None
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    auth_token: str | None = None  # X cookie
    ct0: str | None = None  # X cookie
    slack_webhook_url: str | None = None
    qdrant_url: str = "http://localhost:6333"
    tokres_data_dir: Path = Field(default=ROOT / "data")
    tokres_tz: str = "Asia/Seoul"
    http_user_agent: str = "tokres-research-agent (contact: unknown)"
    triage_model: str = "claude-haiku-4-5"
    enrich_model: str = "claude-opus-5"
    answer_model: str = "claude-opus-5"
    enrich_daily_max: int = 25

    @property
    def data_dir(self) -> Path:
        p = self.tokres_data_dir if self.tokres_data_dir.is_absolute() else ROOT / self.tokres_data_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def config_dir(self) -> Path:
        return ROOT / "config"

    @property
    def reports_dir(self) -> Path:
        p = ROOT / "reports"
        p.mkdir(exist_ok=True)
        return p

    @property
    def dossiers_dir(self) -> Path:
        p = ROOT / "dossiers"
        p.mkdir(exist_ok=True)
        return p

    @property
    def prompts_dir(self) -> Path:
        return Path(__file__).resolve().parent / "llm" / "prompts"

    @property
    def has_llm(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_slack(self) -> bool:
        return bool(self.slack_webhook_url)

    @property
    def has_naver(self) -> bool:
        return bool(self.naver_client_id and self.naver_client_secret)

    @property
    def has_x(self) -> bool:
        return bool(self.auth_token and self.ct0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
