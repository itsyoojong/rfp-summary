import asyncio
from datetime import UTC, datetime

import pytest

from ai_virtual_team.config import Settings
from ai_virtual_team.database import Store
from ai_virtual_team.llm import MockLLM
from ai_virtual_team.models import AgentMessage, AgentOutput, Meeting, Project, Role, TaskStatus
from ai_virtual_team.orchestrator import Orchestrator
from ai_virtual_team.scheduler import catch_up_today, run_scheduled_report
from ai_virtual_team.service import TeamService


@pytest.fixture
def service(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "team.sqlite3", admin_user_ids="101")
    app = TeamService(settings, Store(settings.database_path), Orchestrator(MockLLM()))
    yield app
    app.store.close()


def create(service):
    return service.create_project(name="RFP Insight", goal="근거가 확인되는 RFP 분석", actor_id=101)


def test_phase1_persistence_and_admin(service):
    with pytest.raises(PermissionError):
        service.create_project(name="x", goal="x", actor_id=999)
    project = create(service)
    reopened = Store(service.settings.database_path)
    assert reopened.get("project", project.id) == project
    assert reopened.sync_summary() == {"pending": 1}
    reopened.close()


def test_phase1_transaction_rolls_back_outbox(service):
    with pytest.raises(RuntimeError):
        with service.store.transaction():
            service.store.put("project", Project(name="x", goal="x", created_by=101))
            raise RuntimeError("rollback")
    assert service.store.list("project") == []
    assert service.store.sync_summary() == {}


def test_model_cannot_claim_executed_tests_in_structured_result():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentOutput(content="통과라고 주장하는 모델 출력", test_execution="passed")


async def test_meeting_approval_restart_and_report(service):
    project = create(service)
    meeting = await service.meeting_start(project.id, "킥오프", 101)
    assert len(service.meeting_messages(meeting.id)) == 5
    assert {t.status for t in service.list_tasks(project.id)} == {TaskStatus.PROPOSED}
    with pytest.raises(PermissionError):
        service.plan_approve(project.id, meeting.id, 999)
    service.plan_approve(project.id, meeting.id, 101, approve=False)
    assert {t.status for t in service.list_tasks(project.id)} == {TaskStatus.PROPOSED}
    approved = service.plan_approve(project.id, meeting.id, 101)
    assert approved.status == "Approved"
    service.plan_approve(project.id, meeting.id, 101)
    assert service.get_project(project.id).plan_version == 1
    assert {t.status for t in service.list_tasks(project.id)} == {TaskStatus.APPROVED}
    report = await service.daily_report(project.id)
    assert not report.completed_tasks
    assert report.github_changes["available"] is False
    reopened = Store(service.settings.database_path)
    assert reopened.get("meeting", meeting.id).status == "Approved"
    assert reopened.report_on(project.id, report.report_date).id == report.id
    assert len(reopened.messages(meeting.id)) == 5
    reopened.close()


async def test_stale_plan_and_wrong_project_rejected(service):
    project = create(service)
    first = await service.meeting_start(project.id, "계획 1", 101)
    second = await service.meeting_start(project.id, "계획 2", 101)
    other = create(service)
    with pytest.raises(ValueError, match="일치"):
        service.plan_approve(other.id, first.id, 101)
    service.plan_approve(project.id, first.id, 101)
    with pytest.raises(ValueError, match="오래된"):
        service.plan_approve(project.id, second.id, 101)
    assert service.get_project(project.id).goal == project.goal


async def test_approval_rolls_back_every_change_when_task_write_fails(service, monkeypatch):
    project = create(service)
    meeting = await service.meeting_start(project.id, "원자적 승인", 101)
    original_put = service.store.put
    task_writes = 0

    def failing_put(kind, entity, **kwargs):
        nonlocal task_writes
        if kind == "task":
            task_writes += 1
            if task_writes == 2:
                raise OSError("disk write failure")
        return original_put(kind, entity, **kwargs)

    monkeypatch.setattr(service.store, "put", failing_put)
    with pytest.raises(OSError):
        service.plan_approve(project.id, meeting.id, 101)
    assert service.latest_meeting(project.id).status == "Pending Approval"
    assert service.get_project(project.id).plan_version == 0
    assert all(t.status == TaskStatus.PROPOSED for t in service.list_tasks(project.id))
    assert service.store.connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


async def test_simultaneous_meeting_cannot_duplicate_project_workflow(service):
    entered = asyncio.Event()
    release = asyncio.Event()

    class PausingLLM(MockLLM):
        async def generate(self, request):
            if request.stage == "agenda":
                entered.set()
                await release.wait()
            return await super().generate(request)

    service.orchestrator = Orchestrator(PausingLLM())
    project = create(service)
    running = asyncio.create_task(service.meeting_start(project.id, "첫 회의", 101))
    await entered.wait()
    try:
        with pytest.raises(ValueError, match="이미 진행"):
            await service.meeting_start(project.id, "중복 회의", 101)
    finally:
        release.set()
    meeting = await running
    assert len(service.store.list("meeting", project.id)) == 1
    assert len(service.meeting_messages(meeting.id)) == 5


async def test_partial_meeting_and_delivery_failure_preserved(service):
    class FailingLLM(MockLLM):
        async def generate(self, request):
            if request.stage == "review":
                raise RuntimeError("secret should not be saved")
            return await super().generate(request)

    service.orchestrator = Orchestrator(FailingLLM())
    project = create(service)

    async def broken_discord(message):
        raise OSError("disconnect")

    with pytest.raises(RuntimeError):
        await service.meeting_start(project.id, "중단", 101, on_message=broken_discord)
    meeting = service.latest_meeting(project.id)
    assert meeting.status == "Failed"
    assert "secret" not in meeting.error
    assert len(service.meeting_messages(meeting.id)) == 2
    assert service.list_tasks(project.id) == []


async def test_task_done_requires_external_evidence_and_human_review(service):
    project = create(service)
    meeting = await service.meeting_start(project.id, "킥오프", 101)
    task = service.list_tasks(project.id)[0]
    with pytest.raises(ValueError):
        await service.update_task(project.id, task.id, TaskStatus.IN_PROGRESS, 101)
    service.plan_approve(project.id, meeting.id, 101)
    await service.update_task(project.id, task.id, TaskStatus.IN_PROGRESS, 101)
    await service.update_task(project.id, task.id, TaskStatus.IN_REVIEW, 101)
    with pytest.raises(ValueError, match="근거"):
        await service.update_task(project.id, task.id, TaskStatus.DONE, 101)

    class EvidenceGitHub:
        async def verify_evidence(self, repository, url):
            return {"url": url, "status": "merged", "tested": False}

        async def snapshot(self, repository, since=None):
            return {"commits": [], "limitations": ["테스트 실행 여부는 미확인"]}

    service.github = EvidenceGitHub()
    completed = await service.update_task(project.id, task.id, TaskStatus.DONE, 101,
                                          evidence_url="https://github.com/itsyoojong/rfp-summary/pull/1",
                                          note="완료 기준 문서를 사람이 검토함. 테스트 실행은 미확인.")
    assert completed.completed_at
    report = await service.daily_report(project.id)
    assert len(report.completed_tasks) == 1
    assert report.completed_tasks[0]["evidence"][0]["tested"] is False


async def test_daily_date_unique_under_concurrent_requests(service):
    project = create(service)
    service.clock = lambda: datetime(2026, 10, 26, 15, 1, tzinfo=UTC)

    class SlowGitHub:
        async def snapshot(self, repository, since=None):
            await asyncio.sleep(0)
            return {"commits": []}

    service.github = SlowGitHub()
    reports = await asyncio.gather(*(service.daily_report(project.id) for _ in range(3)))
    assert len({r.id for r in reports}) == 1
    assert reports[0].report_date == "2026-10-27"
    assert len(service.store.list("report")) == 1


def test_interrupted_restart_preserves_messages(service):
    project = create(service)
    meeting = Meeting(project_id=project.id, topic="진행 중", requested_by=101, base_plan_version=0)
    with service.store.transaction():
        service.store.put("meeting", meeting, enqueue=False)
        service.store.add_message(AgentMessage(meeting_id=meeting.id, sequence=1, role=Role.PM,
                                               stage="agenda", output=AgentOutput(content="의제")))
    service.recover_interrupted()
    assert service.latest_meeting(project.id).status == "Interrupted"
    assert len(service.meeting_messages(meeting.id)) == 1


async def test_notion_failure_keeps_state_and_protects_uncertain_create(service):
    class BrokenNotion:
        priors = []

        async def upsert(self, kind, entity_id, revision, fields, prior=None):
            self.priors.append(prior)
            raise OSError("lost response")

    project = create(service)
    service.notion = BrokenNotion()
    await service.sync_pending()
    assert service.get_project(project.id).name == project.name
    assert service.store.sync_summary() == {"failed": 1}
    service.store.retry_sync()
    await service.sync_pending()
    assert service.notion.priors[1]["ambiguous_create"] is True


async def test_disabled_schedule_and_delivery_dedup(service):
    project = create(service)
    sent = []

    async def send(project, report):
        sent.append(report.id)

    assert await run_scheduled_report(service, project.id, send) is None
    assert service.store.list("report") == []
    service.set_report_time(project.id, "00:00", 101)
    await run_scheduled_report(service, project.id, send)
    await run_scheduled_report(service, project.id, send)
    await catch_up_today(service, send)
    assert len(sent) == 1


async def test_uncertain_discord_send_is_not_blindly_retried(service):
    project = create(service)
    service.set_report_time(project.id, "00:00", 101)
    calls = []

    async def uncertain(project, report):
        calls.append(report.id)
        raise TimeoutError("ack missing")

    with pytest.raises(TimeoutError):
        await run_scheduled_report(service, project.id, uncertain)
    await run_scheduled_report(service, project.id, uncertain)
    assert len(calls) == 1
    assert service.store.delivery_status(calls[0]) == "uncertain"


async def test_real_apscheduler_job_registration_and_execution(service):
    pytest.importorskip("apscheduler", reason="APScheduler is unavailable in this offline execution environment")
    from ai_virtual_team.scheduler import build_scheduler, refresh_scheduler

    delivered = asyncio.Event()

    async def send(project, report):
        delivered.set()

    project = create(service)
    scheduler = build_scheduler(service, send)
    assert scheduler.get_jobs() == []
    service.set_report_time(project.id, "09:00", 101)
    refresh_scheduler(scheduler, service, send)
    refresh_scheduler(scheduler, service, send)
    assert len(scheduler.get_jobs()) == 1
    scheduler.add_job(run_scheduled_report, "date", args=[service, project.id, send])
    scheduler.start()
    try:
        await asyncio.wait_for(delivered.wait(), timeout=2)
        assert len(service.store.list("report", project.id)) == 1
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)


async def test_notion_timeout_does_not_block_forever(service):
    class SlowNotion:
        async def upsert(self, *args, **kwargs):
            await asyncio.Event().wait()

    create(service)
    service.notion = SlowNotion()
    await asyncio.wait_for(service.sync_pending(budget_seconds=0.01), timeout=1)
    assert service.store.sync_summary() == {"failed": 1}
