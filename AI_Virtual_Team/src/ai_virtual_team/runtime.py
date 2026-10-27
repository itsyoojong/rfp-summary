"""Composition root and a Mac/Linux single-process DB lock."""

import fcntl
from contextlib import contextmanager
from pathlib import Path

from .codex_llm import CodexLLM
from .config import Settings
from .database import Store
from .llm import GroqLLM, MockLLM
from .orchestrator import Orchestrator
from .service import TeamService


@contextmanager
def runtime_lock(path: str | Path):
    target = Path(str(path) + ".lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("같은 DB를 사용하는 프로세스가 실행 중입니다. 해당 프로세스에서 명령을 실행하세요.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def build_service(settings: Settings) -> TeamService:
    from .integrations.github import GitHubService
    from .integrations.notion import NotionService

    if settings.notion_token and len(settings.notion_sources) != 4:
        raise ValueError("Notion 사용 시 네 개의 NOTION_*_DATA_SOURCE_ID를 모두 설정해야 합니다.")
    provider = {"mock": MockLLM, "codex": lambda: CodexLLM(settings),
                "groq": lambda: GroqLLM(settings)}[settings.llm_mode]()
    github = GitHubService(token=settings.github_token.get_secret_value() if settings.github_token else None) \
        if settings.github_enabled else None
    notion = NotionService(settings.notion_token.get_secret_value(), settings.notion_sources,
                           version=settings.notion_version) if settings.notion_token else None
    return TeamService(settings, Store(settings.database_path),
                       Orchestrator(provider, settings.meeting_max_turns), github=github, notion=notion)
