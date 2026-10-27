"""Secrets stay in environment/.env; no connector is activated by fake credentials."""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_path: Path = Path("runtime/team.sqlite3")
    llm_mode: Literal["mock", "codex", "groq"] = "mock"
    codex_binary: str = Field(default="codex", min_length=1)
    codex_model: str | None = None
    codex_timeout_seconds: float = Field(default=120, gt=0, le=600)
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_calls_per_minute: int = Field(default=20, ge=1, le=300)
    llm_max_output_tokens: int = Field(default=4000, ge=256, le=16000)
    meeting_max_turns: int = Field(default=5, ge=5, le=12)
    discord_bot_token: SecretStr | None = None
    discord_guild_id: int | None = None
    admin_user_ids: str = ""
    github_token: SecretStr | None = None
    github_enabled: bool = False
    notion_token: SecretStr | None = None
    notion_version: str = "2026-03-11"
    notion_projects_data_source_id: str | None = None
    notion_tasks_data_source_id: str | None = None
    notion_meetings_data_source_id: str | None = None
    notion_reports_data_source_id: str | None = None
    daily_report_time: str | None = None

    @field_validator("daily_report_time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value == "" or value is None:
            return None
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("DAILY_REPORT_TIME은 HH:MM 형식이어야 합니다.")
        return value

    @field_validator("admin_user_ids")
    @classmethod
    def valid_admins(cls, value: str) -> str:
        if any(not x.strip().isdigit() or int(x.strip()) <= 0 for x in value.split(",") if x.strip()):
            raise ValueError("ADMIN_USER_IDS는 쉼표로 구분한 Discord 사용자 ID입니다.")
        return value

    @model_validator(mode="after")
    def credentials(self):
        if self.llm_mode == "groq" and not self.groq_api_key:
            raise ValueError("Groq 모드에는 GROQ_API_KEY가 필요합니다.")
        return self

    @property
    def admins(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_user_ids.split(",") if x.strip()}

    @property
    def notion_sources(self) -> dict[str, str]:
        return {k: v for k, v in {
            "project": self.notion_projects_data_source_id,
            "task": self.notion_tasks_data_source_id,
            "meeting": self.notion_meetings_data_source_id,
            "report": self.notion_reports_data_source_id,
        }.items() if v}
