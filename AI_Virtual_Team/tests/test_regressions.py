"""Cross-boundary regressions: clocks, failed intervals, and creation uncertainty."""

from datetime import UTC, datetime, timedelta

import pytest

from ai_virtual_team.config import Settings
from ai_virtual_team.database import Store
from ai_virtual_team.llm import MockLLM
from ai_virtual_team.models import TaskStatus
from ai_virtual_team.orchestrator import Orchestrator
from ai_virtual_team.service import TeamService


@pytest.fixture
def timed_service(tmp_path):
    current = [datetime(2026, 10, 23, 2, tzinfo=UTC)]
    settings = Settings(_env_file=None, database_path=tmp_path / "regression.sqlite3", admin_user_ids="101")
    service = TeamService(settings, Store(settings.database_path), Orchestrator(MockLLM()), clock=lambda: current[0])
    project = service.create_project(name="Regression", goal="검증 가능한 상태 관리", actor_id=101)
    yield service, project, current
    service.store.close()


async def test_task_completed_during_github_fetch_is_not_repeated_next_day(timed_service):
    service, project, current = timed_service
    meeting = await service.meeting_start(project.id, "완료 기준 검토", 101)
    service.plan_approve(project.id, meeting.id, 101)
    task = service.list_tasks(project.id)[0]
    await service.update_task(project.id, task.id, TaskStatus.IN_PROGRESS, 101)
    await service.update_task(project.id, task.id, TaskStatus.IN_REVIEW, 101)
    start = current[0]

    class CompletingGitHub:
        completed = False

        async def verify_evidence(self, repository, url):
            return {"url": url, "status": "merged", "tested": False}

        async def snapshot(self, repository, since=None):
            if not self.completed:
                self.completed = True
                current[0] = start + timedelta(seconds=1)
                await service.update_task(
                    project.id, task.id, TaskStatus.DONE, 101,
                    evidence_url="https://github.com/itsyoojong/rfp-summary/pull/1",
                    note="인간이 완료 기준 문서를 검토함. 테스트 실행 여부는 미확인.",
                )
                current[0] = start + timedelta(seconds=2)
            return {"fetched_at": current[0].isoformat(), "commits": [], "errors": {}}

    service.github = CompletingGitHub()
    first = await service.daily_report(project.id)
    current[0] = start + timedelta(days=1)
    second = await service.daily_report(project.id)
    # A report's cutoff must cover everything it includes. A completion observed
    # during awaited I/O must not fall after that report's persisted watermark.
    assert [item["id"] for item in first.completed_tasks] == [task.id]
    assert service.store.get("task", task.id).completed_at <= first.created_at
    assert second.completed_tasks == []


async def test_github_failed_interval_uses_last_successful_report_watermark(timed_service):
    service, project, current = timed_service
    since_values = []

    class IntermittentGitHub:
        async def snapshot(self, repository, since=None):
            since_values.append(since)
            if len(since_values) == 2:
                raise TimeoutError("read failed")
            return {"fetched_at": current[0].isoformat(), "commits": [], "errors": {}}

    service.github = IntermittentGitHub()
    first = await service.daily_report(project.id)
    current[0] += timedelta(days=1)
    failed = await service.daily_report(project.id)
    assert failed.github_changes["available"] is False
    current[0] += timedelta(days=1)
    await service.daily_report(project.id)
    assert since_values == [None, first.created_at, first.created_at]


async def test_github_initial_failure_does_not_advance_watermark(timed_service):
    service, project, current = timed_service
    since_values = []

    class InitiallyFailingGitHub:
        async def snapshot(self, repository, since=None):
            since_values.append(since)
            if len(since_values) == 1:
                raise TimeoutError("read failed")
            return {"fetched_at": current[0].isoformat(), "commits": [], "errors": {}}

    service.github = InitiallyFailingGitHub()
    await service.daily_report(project.id)
    current[0] += timedelta(days=1)
    await service.daily_report(project.id)
    assert since_values == [None, None]


async def test_github_watermark_covers_snapshot_to_report_finalization_gap(timed_service):
    service, project, current = timed_service
    start = current[0]
    since_values = []

    class SlowSnapshot:
        async def snapshot(self, repository, since=None):
            since_values.append(since)
            fetched_at = current[0].isoformat()
            # The commits query may finish before a slow PR query. A commit
            # created during that gap must remain eligible for the next report.
            current[0] += timedelta(seconds=10)
            return {"fetched_at": fetched_at, "commits": [], "errors": {}}

    service.github = SlowSnapshot()
    first = await service.daily_report(project.id)
    assert first.github_changes["fetched_at"] < first.created_at
    current[0] = start + timedelta(days=1)
    await service.daily_report(project.id)
    assert since_values == [None, first.github_changes["fetched_at"]]


async def test_known_notion_prewrite_failure_can_retry_first_create(timed_service):
    from ai_virtual_team.integrations.notion import NotionNoWriteError

    service, project, current = timed_service

    class QueryFailure:
        priors = []

        async def upsert(self, kind, entity_id, revision, fields, prior=None):
            self.priors.append(dict(prior or {}))
            if len(self.priors) == 1:
                raise NotionNoWriteError("data source query failed before create")
            return {"page_id": "confirmed-page", "revision": revision}

    notion = QueryFailure()
    service.notion = notion
    await service.sync_pending()
    service.store.retry_sync()
    await service.sync_pending()
    assert len(notion.priors) == 2
    assert not notion.priors[1].get("ambiguous_create")
    assert service.store.sync_summary() == {}


async def test_prewrite_failure_does_not_clear_earlier_ambiguous_create(timed_service):
    from ai_virtual_team.integrations.notion import NotionAmbiguousCreate, NotionNoWriteError

    service, project, current = timed_service

    class MixedFailure:
        priors = []

        async def upsert(self, kind, entity_id, revision, fields, prior=None):
            self.priors.append(dict(prior or {}))
            if len(self.priors) == 1:
                raise NotionAmbiguousCreate("create acknowledgement missing")
            raise NotionNoWriteError("next query failed before any new write")

    notion = MixedFailure()
    service.notion = notion
    await service.sync_pending()
    for _ in range(2):
        service.store.retry_sync()
        await service.sync_pending()
    assert len(notion.priors) == 3
    assert notion.priors[1].get("ambiguous_create") is True
    assert notion.priors[2].get("ambiguous_create") is True
