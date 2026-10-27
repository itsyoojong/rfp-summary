"""Trusted application transitions. LLM proposals cannot mutate state directly."""

import asyncio
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from .config import Settings
from .database import Store
from .models import AgentMessage, DailyReport, Meeting, Project, Task, TaskStatus, now_iso

SEOUL = ZoneInfo("Asia/Seoul")
TRANSITIONS = {
    TaskStatus.APPROVED: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED},
    TaskStatus.IN_PROGRESS: {TaskStatus.IN_REVIEW, TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.IN_PROGRESS},
    TaskStatus.IN_REVIEW: {TaskStatus.DONE, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED},
    TaskStatus.DONE: {TaskStatus.IN_REVIEW},
    TaskStatus.PROPOSED: set(),
}


class TeamService:
    def __init__(self, settings: Settings, store: Store, orchestrator, *, github=None, notion=None,
                 clock: Callable[[], datetime] | None = None):
        self.settings = settings
        self.store = store
        self.orchestrator = orchestrator
        self.github = github
        self.notion = notion
        self.clock = clock or (lambda: datetime.now(UTC))
        self._sync_lock = asyncio.Lock()

    def require_admin(self, actor_id: int):
        if actor_id not in self.settings.admins:
            raise PermissionError("지정된 관리자만 수행할 수 있습니다. ADMIN_USER_IDS를 확인하세요.")

    def create_project(self, *, name: str, goal: str, actor_id: int, description: str = "",
                       repository: str = "https://github.com/itsyoojong/rfp-summary",
                       guild_id: int | None = None, channel_id: int | None = None) -> Project:
        from .integrations.github import parse_repository

        self.require_admin(actor_id)
        slug = parse_repository(repository)
        project = Project(name=name, goal=goal, description=description,
                          github_repository=f"https://github.com/{slug}",
                          created_by=actor_id, guild_id=guild_id, channel_id=channel_id,
                          report_time=self.settings.daily_report_time)
        with self.store.transaction():
            self.store.put("project", project)
            self.store.activity(project.id, "project_created", actor_id, {"name": project.name})
        return project

    def get_project(self, project_id: str) -> Project:
        return self.store.get("project", project_id)

    def list_tasks(self, project_id: str) -> list[Task]:
        self.get_project(project_id)
        return self.store.list("task", project_id)

    def latest_meeting(self, project_id: str) -> Meeting | None:
        self.get_project(project_id)
        meetings = self.store.list("meeting", project_id)
        return meetings[-1] if meetings else None

    def meeting_messages(self, meeting_id: str) -> list[AgentMessage]:
        self.store.get("meeting", meeting_id)
        return self.store.messages(meeting_id)

    def status(self, project_id: str) -> dict:
        project = self.get_project(project_id)
        reports = self.store.list("report", project_id)
        latest = self.latest_meeting(project_id)
        return {
            "project": project.model_dump(mode="json"),
            "task_counts": dict(Counter(task.status.value for task in self.list_tasks(project_id))),
            "latest_meeting": latest.model_dump(mode="json") if latest else None,
            "latest_report": reports[-1].model_dump(mode="json") if reports else None,
            "notion_enabled": self.notion is not None,
            "sync_queue": self.store.sync_summary(),
        }

    async def github_snapshot(self, project: Project, since: str | None = None) -> dict:
        if self.github is None:
            return {"available": False, "limitations": ["GitHub 연결 미설정: 변경 사항 미확인"]}
        try:
            return await self.github.snapshot(project.github_repository, since=since)
        except Exception as exc:
            return {"available": False, "limitations": [f"GitHub 조회 실패 ({type(exc).__name__}); 변경 사항 미확인"]}

    async def meeting_start(self, project_id: str, topic: str, actor_id: int,
                            on_message: Callable[[AgentMessage], Awaitable[None]] | None = None) -> Meeting:
        project = self.get_project(project_id)
        meeting = Meeting(project_id=project_id, topic=topic, requested_by=actor_id,
                          base_plan_version=project.plan_version)
        try:
            with self.store.transaction():
                self.store.put("meeting", meeting, enqueue=False)
                self.store.activity(project_id, "meeting_started", actor_id, {"meeting_id": meeting.id})
        except sqlite3.IntegrityError as exc:
            raise ValueError("이 프로젝트에는 이미 진행 중인 회의가 있습니다.") from exc

        async def persist_message(message: AgentMessage):
            with self.store.transaction():
                self.store.add_message(message)
            if on_message:
                try:
                    await on_message(message)
                except Exception as exc:
                    self.store.activity(project_id, "discord_message_failed", None,
                                        {"message_id": message.id, "error": type(exc).__name__})

        try:
            evidence = await self.github_snapshot(project)
            shared_state = {
                "tasks": [t.model_dump(mode="json") for t in self.list_tasks(project_id)],
                "approved_meetings": [m.model_dump(mode="json") for m in self.store.list("meeting", project_id)
                                      if m.status == "Approved"][-5:],
                "policy": "확정된 목표와 작업을 변경하지 않고 추가 작업만 제안할 수 있습니다.",
            }
            final = await self.orchestrator.run(project, meeting, evidence, persist_message,
                                                project_state=shared_state)
            meeting.summary = final.summary
            meeting.status = "Pending Approval"
            meeting.revision += 1
            with self.store.transaction():
                self.store.put("meeting", meeting)
                for proposal in final.tasks:
                    task = Task(**proposal.model_dump(), project_id=project_id, meeting_id=meeting.id)
                    self.store.put("task", task)
                self.store.activity(project_id, "plan_proposed", actor_id,
                                    {"meeting_id": meeting.id, "tasks": len(final.tasks)})
        except BaseException as exc:
            meeting.status = "Interrupted" if isinstance(exc, asyncio.CancelledError) else "Failed"
            meeting.error = f"회의 중단: {type(exc).__name__}. 저장된 발언은 보존됩니다."
            meeting.revision += 1
            with self.store.transaction():
                self.store.put("meeting", meeting)
                self.store.activity(project_id, "meeting_failed", actor_id,
                                    {"meeting_id": meeting.id, "error": type(exc).__name__})
            raise
        await self.sync_pending()
        return meeting

    def plan_approve(self, project_id: str, meeting_id: str, actor_id: int, approve: bool = True) -> Meeting:
        self.require_admin(actor_id)
        with self.store.transaction():
            project = self.get_project(project_id)
            meeting = self.store.get("meeting", meeting_id)
            if meeting.project_id != project_id:
                raise ValueError("회의와 프로젝트가 일치하지 않습니다.")
            if meeting.status == "Approved" and approve:
                return meeting
            if meeting.status not in {"Pending Approval", "Held"}:
                raise ValueError("승인 가능한 완료된 제안이 아닙니다.")
            if approve and meeting.base_plan_version != project.plan_version:
                raise ValueError("기준 계획이 변경된 오래된 제안입니다. 최신 상태로 회의를 다시 진행하세요.")
            meeting.status = "Approved" if approve else "Held"
            meeting.revision += 1
            self.store.put("meeting", meeting)
            self.store.approval(project_id, meeting_id, actor_id, approve)
            if approve:
                for task in self.store.list("task", project_id):
                    if task.meeting_id == meeting_id and task.status == TaskStatus.PROPOSED:
                        task.status = TaskStatus.APPROVED
                        task.revision += 1
                        task.updated_at = now_iso()
                        self.store.put("task", task)
                project.plan_version += 1
                project.status = "Active"
                project.revision += 1
                self.store.put("project", project)
            self.store.activity(project_id, "plan_approved" if approve else "plan_held", actor_id,
                                {"meeting_id": meeting_id, "plan_version": project.plan_version})
        return meeting

    def set_report_time(self, project_id: str, time: str | None, actor_id: int) -> Project:
        self.require_admin(actor_id)
        time = Settings.valid_time(time)
        with self.store.transaction():
            project = self.get_project(project_id)
            project.report_time = time
            project.revision += 1
            self.store.put("project", project)
            self.store.activity(project_id, "report_schedule_changed", actor_id,
                                {"time": time, "timezone": "Asia/Seoul"})
        return project

    async def update_task(self, project_id: str, task_id: str, status: TaskStatus, actor_id: int,
                          evidence_url: str | None = None, note: str | None = None,
                          blocker: str | None = None) -> Task:
        self.require_admin(actor_id)
        status = TaskStatus(status)
        project = self.get_project(project_id)
        task = self.store.get("task", task_id)
        if task.project_id != project_id:
            raise ValueError("작업과 프로젝트가 일치하지 않습니다.")
        if status not in TRANSITIONS[task.status]:
            raise ValueError(f"허용되지 않는 상태 전환: {task.status.value} → {status.value}")
        if status == TaskStatus.BLOCKED and not (blocker and blocker.strip()):
            raise ValueError("Blocked 상태에는 장애 사유가 필요합니다.")
        evidence = None
        if status == TaskStatus.DONE:
            if not self.github or not evidence_url or not note or not note.strip():
                raise ValueError("Done에는 연결된 GitHub의 근거 URL과 인간의 완료 기준 검토 기록이 필요합니다.")
            evidence = await self.github.verify_evidence(project.github_repository, evidence_url)
        with self.store.transaction():
            current = self.store.get("task", task_id)
            if current.revision != task.revision:
                raise ValueError("작업이 변경되었습니다. 최신 상태를 확인한 후 다시 시도하세요.")
            task.status = status
            task.blocker = blocker[:6000] if status == TaskStatus.BLOCKED else None
            task.updated_at = self.clock().astimezone(UTC).isoformat()
            task.completed_at = task.updated_at if status == TaskStatus.DONE else None
            task.revision += 1
            if evidence:
                task.evidence.append(evidence)
                task.verification_note = note[:6000]
            self.store.put("task", task)
            self.store.activity(project_id, "task_status_changed", actor_id,
                                {"task_id": task_id, "status": status.value, "evidence": evidence})
        await self.sync_pending()
        return task

    async def daily_report(self, project_id: str) -> DailyReport:
        project = self.get_project(project_id)
        timestamp = self.clock()
        day = timestamp.astimezone(SEOUL).date().isoformat()
        existing = self.store.report_on(project_id, day)
        if existing:
            return existing
        reports = sorted(self.store.list("report", project_id),
                         key=lambda r: datetime.fromisoformat(r.created_at))
        since = reports[-1].created_at if reports else None
        successful = [r for r in reports if r.github_changes.get("fetched_at")
                      and r.github_changes.get("available") is not False and not r.github_changes.get("errors")]
        github_since = successful[-1].github_changes["fetched_at"] if successful else None
        github = await self.github_snapshot(project, since=github_since)
        # Finalize the cutoff after external I/O so a task completed during the
        # fetch is not counted again in tomorrow's report.
        timestamp = self.clock()
        day = timestamp.astimezone(SEOUL).date().isoformat()
        cutoff = timestamp.astimezone(UTC).isoformat()
        with self.store.transaction():
            existing = self.store.report_on(project_id, day)
            if existing:
                return existing
            project = self.get_project(project_id)
            reports = sorted(self.store.list("report", project_id),
                             key=lambda r: datetime.fromisoformat(r.created_at))
            since = reports[-1].created_at if reports else None
            tasks = self.list_tasks(project_id)
            meetings = self.store.list("meeting", project_id)
            completed = [t.model_dump(mode="json") for t in tasks if t.status == TaskStatus.DONE
                         and t.completed_at and t.completed_at <= cutoff
                         and (since is None or t.completed_at > since)]
            running = [t.model_dump(mode="json") for t in tasks
                       if t.status in {TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW, TaskStatus.BLOCKED}]
            pending = [m for m in meetings if m.status in {"Pending Approval", "Held"}
                       and m.base_plan_version == project.plan_version]
            report = DailyReport(
                project_id=project_id, report_date=day, since=since,
                completed_tasks=completed, in_progress_tasks=running,
                blockers=[f"{t.title}: {t.blocker}" for t in tasks if t.status == TaskStatus.BLOCKED],
                github_changes=github,
                decisions_needed=[f"회의 {m.id}: {m.topic} — {m.status}" for m in pending],
                next_steps=[f"{t.title} (담당: {t.assignee.value}, 상태: {t.status.value})"
                            for t in tasks if t.status == TaskStatus.APPROVED],
                suggestions=[f"미검증 검토 의견 ({m.id}): {issue}" for m in meetings[-1:]
                             if m.summary for issue in m.summary.unresolved_issues],
                created_at=cutoff,
            )
            self.store.put("report", report)
            self.store.activity(project_id, "daily_report_created", None, {"report_id": report.id})
        await self.sync_pending()
        return report

    async def sync_pending(self, *, max_items: int = 8, budget_seconds: float = 12) -> dict:
        if self.notion is None:
            return {"enabled": False, "queue": self.store.sync_summary()}
        from .integrations.notion import NotionConflict, NotionNoWriteError

        async with self._sync_lock:
            deadline = asyncio.get_running_loop().time() + budget_seconds
            for _ in range(max_items):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                item = self.store.claim_sync()
                if item is None:
                    break
                try:
                    async with asyncio.timeout(remaining):
                        metadata = await self.notion.upsert(item["kind"], item["entity_id"], item["revision"],
                                                           json.loads(item["payload"]), prior=item["prior"] or None)
                    self.store.sync_success(item, metadata)
                except Exception as exc:
                    self.store.sync_failure(item, conflict=isinstance(exc, NotionConflict),
                                            error=type(exc).__name__,
                                            definitely_not_created=isinstance(exc, NotionNoWriteError)
                                            and not item["prior"].get("ambiguous_create")
                                            and not item["prior"].get("page_id"))
            return {"enabled": True, "queue": self.store.sync_summary()}

    def recover_interrupted(self):
        """Startup only, after obtaining the exclusive runtime lock."""
        with self.store.transaction():
            for meeting in self.store.list("meeting"):
                if meeting.status == "Running":
                    meeting.status = "Interrupted"
                    meeting.error = "프로세스 종료로 회의가 중단되었습니다. 발언은 보존되었습니다."
                    meeting.revision += 1
                    self.store.put("meeting", meeting)
            self.store.connection.execute("UPDATE deliveries SET status='uncertain' WHERE status='sending'")
            self.store.connection.execute("UPDATE outbox SET status='pending' WHERE status='syncing'")

    async def aclose(self):
        for connector in (self.orchestrator.provider, self.github, self.notion):
            if connector is not None and hasattr(connector, "aclose"):
                await connector.aclose()
        self.store.close()
