"""Discord transport: access checks, bounded output, and persisted service calls.

The application service owns transactions. This adapter additionally isolates
projects by Discord server/channel. No message-content intent is needed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from .models import AgentMessage, DailyReport, Meeting, Project, Task, TaskStatus

if TYPE_CHECKING:
    from .service import TeamService

logger = logging.getLogger(__name__)


class CommandAccessError(app_commands.CheckFailure):
    """An access denial that contains no project data."""


def require_guild(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None or interaction.channel_id is None:
        raise CommandAccessError("서버의 프로젝트 채널에서 사용해주세요.")


def require_admin(service: TeamService, interaction: discord.Interaction) -> None:
    require_guild(interaction)
    if interaction.user.id not in service.settings.admins:
        raise CommandAccessError("ADMIN_USER_IDS에 등록된 관리자만 실행할 수 있습니다.")


def parent_channel_id(interaction: discord.Interaction) -> int | None:
    channel = interaction.channel
    return channel.parent_id if isinstance(channel, discord.Thread) else interaction.channel_id


def require_project_scope(service: TeamService, interaction: discord.Interaction, project_id: str) -> Project:
    require_guild(interaction)
    project = service.get_project(project_id)
    if project.guild_id != interaction.guild_id or project.channel_id != parent_channel_id(interaction):
        raise CommandAccessError("이 서버와 채널에서 접근할 수 있는 프로젝트가 아닙니다.")
    return project


def chunks(text: str, limit: int = 1900) -> list[str]:
    """Escape before splitting: mention escaping increases string length."""
    if not 1 <= limit <= 1900:
        raise ValueError("Discord chunk limit must be between 1 and 1900.")
    escaped = discord.utils.escape_mentions(text)
    return [escaped[i:i + limit] for i in range(0, len(escaped), limit)] or ["내용이 없습니다."]


async def send_text(send: Callable, text: str, *, ephemeral: bool | None = None) -> None:
    for chunk in chunks(text):
        kwargs = {"content": chunk, "allowed_mentions": discord.AllowedMentions.none(), "suppress_embeds": True}
        if ephemeral is not None:
            kwargs["ephemeral"] = ephemeral
        await send(**kwargs)


def format_agent_message(message: AgentMessage) -> str:
    review = f"\n검토한 발언: {', '.join(map(str, message.output.review_of))}" if message.output.review_of else ""
    concerns = "\n검토 의견: " + " / ".join(message.output.concerns) if message.output.concerns else ""
    return (f"[{message.role.value} · 발언 {message.sequence} · {message.stage}]\n"
            f"모델의 검토 의견 / 실제 테스트 실행 없음\n{message.output.content}{review}{concerns}")


def format_meeting(meeting: Meeting) -> str:
    lines = [f"회의 ID: {meeting.id}", f"주제: {meeting.topic}", f"상태: {meeting.status}",
             f"기준 계획 버전: {meeting.base_plan_version}"]
    summary = meeting.summary
    if summary is None:
        lines.append("완성된 회의록이 없습니다. 저장된 발언은 내부 DB에 보존됩니다.")
        return "\n".join(lines)
    lines.append(f"회의 목적: {summary.purpose}")
    for label, values in [
        ("주요 의견", summary.main_points), ("합의안", summary.agreements),
        ("반대 의견", summary.dissenting_opinions), ("미해결 문제", summary.unresolved_issues),
        ("다음 작업", summary.next_steps), ("인간 승인 필요", summary.approval_required),
    ]:
        lines.append(f"\n{label}:\n" + "\n".join(f"- {value}" for value in values or ["기록 없음"]))
    if meeting.status == "Pending Approval":
        lines.append("\n현재 내용은 제안입니다. /task_list로 완료 기준을 확인한 후 /plan_approve로 승인하거나 보류해주세요.")
    return "\n".join(lines)


def format_tasks(tasks: list[Task]) -> str:
    if not tasks:
        return "등록된 작업이 없습니다."
    lines = []
    for task in tasks:
        lines.append(f"[{task.status.value}] {task.title}\n작업 ID: {task.id}\n"
                     f"담당: {task.assignee.value} / 우선순위: {task.priority}\n설명: {task.description}\n"
                     "완료 기준: " + " / ".join(task.acceptance_criteria))
        if task.blocker:
            lines.append(f"장애 요인: {task.blocker}")
        if task.evidence:
            lines.append("확인 근거: " + ", ".join(str(item.get("url", "기록 있음")) for item in task.evidence))
        if task.related_links:
            lines.append("관련 링크: " + ", ".join(task.related_links))
        lines.append("")
    return "\n".join(lines).strip()


def format_status(status: dict) -> str:
    """Status is a navigation summary, not a dump of the latest full snapshot."""
    project = status["project"]
    lines = [f"프로젝트: {project['name']}", f"프로젝트 ID: {project['id']}",
             f"목표: {project['goal']}", f"상태: {project['status']}",
             f"승인 계획 버전: {project['plan_version']}",
             f"GitHub: {project['github_repository']}",
             f"자동 보고: {project.get('report_time') or '비활성화'} (Asia/Seoul)"]
    lines.append("작업 수: " + (", ".join(f"{state} {count}건" for state, count in status['task_counts'].items())
                              or "등록된 작업 없음"))
    meeting = status.get("latest_meeting")
    if meeting:
        lines.append(f"최근 회의: {meeting['id']} / {meeting['status']} — /meeting_summary로 확인")
    report = status.get("latest_report")
    if report:
        lines.append(f"최근 보고: {report['report_date']} / {report['id']} — /daily_report로 오늘 보고 확인")
    lines.append(f"Notion 연결: {'설정됨' if status.get('notion_enabled') else '미설정'}")
    lines.append("동기화 큐: " + json.dumps(status.get("sync_queue", {}), ensure_ascii=False))
    return "\n".join(lines)


def format_report(report: DailyReport) -> str:
    """Render persisted facts directly, without an LLM rewrite."""
    lines = [f"[PM · 일일 보고] {report.report_date} (Asia/Seoul)", f"보고서 ID: {report.id}",
             f"조회 기준: {report.since or '첫 보고'} → {report.created_at}"]
    for label, tasks in [("지난 보고 이후 완료", report.completed_tasks), ("진행 중", report.in_progress_tasks)]:
        lines.append(f"\n{label}:")
        for task in tasks:
            lines.append(f"- {task.get('title', task.get('id', '작업'))} / 담당: {task.get('assignee', '미지정')}")
            if label == "지난 보고 이후 완료":
                evidence = task.get("evidence", [])
                for item in evidence[:3]:
                    lines.append(f"  GitHub 확인 근거: {str(item.get('url', 'URL 기록 없음'))[:500]}")
                if len(evidence) > 3:
                    lines.append(f"  추가 근거 {len(evidence) - 3}건은 DB에 보존되었습니다.")
                note = task.get("verification_note")
                if note:
                    lines.append(f"  인간의 완료 기준 검토 기록: {str(note)[:500]}")
        if not tasks:
            lines.append("- DB에 해당 작업 없음")
    for label, items in [("장애 요인", report.blockers), ("인간 결정 필요", report.decisions_needed),
                         ("다음 작업 계획", report.next_steps), ("추정·제안 (확인된 사실 아님)", report.suggestions)]:
        lines.append(f"\n{label}:\n" + "\n".join(f"- {item}" for item in items or ["기록 없음"]))
    lines.append("\nGitHub 조회 결과 (API에서 확인된 기록 및 조회 한계):\n"
                 + format_github_changes(report.github_changes))
    lines.append("\nGitHub 변경 기록이나 작업 상태만으로 테스트 통과를 의미하지 않습니다.")
    return "\n".join(lines)


def format_github_changes(snapshot: dict) -> str:
    """Bound the Discord view while keeping the complete snapshot in SQLite."""
    lines = []
    if snapshot.get("repository"):
        lines.append(f"저장소: {str(snapshot['repository'])[:150]}")
    if snapshot.get("fetched_at"):
        lines.append(f"조회 시각: {str(snapshot['fetched_at'])[:50]}")
    if snapshot.get("available") is False:
        lines.append("GitHub 연결 또는 조회를 확인하지 못했습니다.")
    errors = snapshot.get("errors", {})
    if snapshot.get("error"):
        lines.append(f"조회 오류: {str(snapshot['error'])[:500]}")
    if isinstance(errors, dict):
        for source, error in list(errors.items())[:10]:
            lines.append(f"{str(source)[:80]} 조회 오류: {str(error)[:500]}")
    if "files" in snapshot:
        if "files" not in errors:
            lines.append(f"기본 브랜치의 현재 트리 항목: {len(snapshot['files'])}개 (변경 파일 수 아님)")
    for key, label in [("commits", "커밋"), ("pull_requests", "PR"), ("issues", "Issue")]:
        if key not in snapshot or key in errors:
            continue
        records = snapshot[key]
        lines.append(f"조회된 {label}: {len(records)}건")
        for record in records[:10]:
            identifier = str(record.get("sha", ""))[:8] if key == "commits" else f"#{record.get('number', '?')}"
            title = str(record.get("message" if key == "commits" else "title", ""))[:200].replace("\n", " ")
            state = f" [{record['state']}]" if record.get("state") else ""
            lines.append(f"- {identifier}{state} {title}\n  {str(record.get('url', 'URL 기록 없음'))[:500]}")
        if len(records) > 10:
            lines.append(f"추가 {label} {len(records) - 10}건은 DB의 전체 보고서에 보존되었습니다.")
    for limitation in snapshot.get("limitations", [])[:10]:
        lines.append(f"조회 한계: {str(limitation)[:500]}")
    return "\n".join(lines) or "GitHub 조회 기록이 없습니다."


async def meeting_target(interaction: discord.Interaction, topic: str) -> tuple[Any, bool]:
    """Missing thread permission changes delivery, not the persisted meeting."""
    channel = interaction.channel
    if isinstance(channel, discord.Thread):
        return channel, False
    if isinstance(channel, discord.TextChannel):
        try:
            thread = await channel.create_thread(
                name=("AI 회의 · " + discord.utils.escape_mentions(topic))[:100],
                type=discord.ChannelType.public_thread,
                auto_archive_duration=1440,
                reason="인간 사용자가 요청한 AI 팀 회의",
            )
            return thread, False
        except discord.HTTPException:
            logger.warning("Discord thread creation unavailable; using project channel")
    if channel is None or not hasattr(channel, "send"):
        raise CommandAccessError("메시지를 보낼 수 있는 서버 채널에서 사용해주세요.")
    return channel, True


class TeamBot(discord.Client):
    def __init__(self, service: TeamService):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.service = service
        self.tree = app_commands.CommandTree(self)
        self.scheduler = None
        self._register_commands()
        self.tree.on_error = self.on_command_error

    async def setup_hook(self) -> None:
        if self.service.settings.discord_guild_id:
            guild = discord.Object(id=self.service.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        from .scheduler import build_scheduler

        self.scheduler = build_scheduler(self.service, self.send_report)
        self.scheduler.start()

    async def close(self) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        await super().close()

    async def send_report(self, project: Project, report: DailyReport) -> None:
        """Scheduler marks delivery only after this method returns successfully."""
        if project.guild_id is None or project.channel_id is None:
            raise ValueError("자동 보고를 보낼 Discord 프로젝트 채널이 없습니다.")
        if report.project_id != project.id:
            raise ValueError("보고서와 프로젝트가 일치하지 않습니다.")
        await self.wait_until_ready()
        channel = self.get_channel(project.channel_id) or await self.fetch_channel(project.channel_id)
        guild = getattr(channel, "guild", None)
        if guild is None or guild.id != project.guild_id:
            raise ValueError("보고 대상 서버/채널 정보가 일치하지 않습니다.")
        await send_text(channel.send, format_report(report))

    async def on_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, CommandAccessError):
            text = str(original)
        elif isinstance(original, PermissionError):
            text = "이 작업을 실행할 권한이 없습니다."
        elif isinstance(original, (ValueError, KeyError)):
            text = "입력값 또는 현재 상태와 맞지 않는 요청입니다. 프로젝트·회의·작업 ID와 상태를 확인해주세요."
        else:
            text = "요청 처리에 실패했습니다. 저장된 상태는 /project_status 또는 /meeting_summary에서 확인해주세요."
        # Exception bodies can contain secrets or untrusted remote text.
        logger.warning("Discord command failed: %s", type(original).__name__)
        try:
            if interaction.response.is_done():
                await send_text(interaction.followup.send, text, ephemeral=True)
            else:
                await send_text(interaction.response.send_message, text, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Could not deliver Discord command error")

    async def _sync_notice(self) -> str:
        try:
            result = await self.service.sync_pending()
        except Exception as exc:
            logger.warning("Notion sync deferred: %s", type(exc).__name__)
            return "\nNotion 동기화를 완료하지 못했습니다. 내부 DB 기록은 유지되며 다시 시도할 수 있습니다."
        if result.get("enabled") is False:
            return "\nNotion 미연결 상태입니다. 내부 DB에 기록했습니다."
        queue = result.get("queue", {})
        if any(queue.get(state) for state in ("failed", "conflict", "pending", "syncing")):
            return "\nNotion 동기화 대기 또는 충돌 항목이 있습니다. 내부 DB 기록은 저장되었습니다."
        return ""

    def _register_commands(self) -> None:
        service = self.service

        @self.tree.command(name="project_create", description="관리자가 새 프로젝트 목표와 설명을 등록합니다.")
        @app_commands.guild_only()
        async def project_create(
            interaction: discord.Interaction, name: str, goal: str, description: str = "",
            repository: str = "https://github.com/itsyoojong/rfp-summary",
        ) -> None:
            require_admin(service, interaction)
            await interaction.response.defer(thinking=True)
            project = service.create_project(
                name=name, goal=goal, description=description, repository=repository,
                actor_id=interaction.user.id, guild_id=interaction.guild_id,
                channel_id=parent_channel_id(interaction),
            )
            if self.scheduler is not None and project.report_time:
                from .scheduler import refresh_scheduler

                refresh_scheduler(self.scheduler, service, self.send_report)
            notice = await self._sync_notice()
            await send_text(interaction.followup.send, f"프로젝트를 생성했습니다: {project.name}\n"
                            f"프로젝트 ID: {project.id}\n목표: {project.goal}{notice}")

        @self.tree.command(name="meeting_start", description="주제를 지정해 AI 팀 회의를 시작합니다.")
        @app_commands.guild_only()
        async def meeting_start(interaction: discord.Interaction, project_id: str, topic: str) -> None:
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            target, fallback = await meeting_target(interaction, topic)
            if fallback:
                await send_text(interaction.followup.send, "스레드를 만들 수 없어 현재 채널에 회의를 기록합니다.")
            else:
                await send_text(interaction.followup.send, f"회의를 시작합니다. 기록 위치: <#{target.id}>")
            failed_delivery = False

            async def publish(message: AgentMessage) -> None:
                nonlocal failed_delivery
                try:
                    await send_text(target.send, format_agent_message(message))
                except Exception as exc:
                    failed_delivery = True
                    logger.warning("Meeting message delivery failed: %s", type(exc).__name__)

            meeting = await service.meeting_start(project_id, topic, interaction.user.id, on_message=publish)
            notice = await self._sync_notice()
            try:
                await send_text(target.send, format_meeting(meeting))
                tasks = [task for task in service.list_tasks(project_id) if task.meeting_id == meeting.id]
                await send_text(target.send, "[PM · 제안된 작업]\n" + format_tasks(tasks))
            except Exception as exc:
                failed_delivery = True
                logger.warning("Meeting summary delivery failed: %s", type(exc).__name__)
            if failed_delivery:
                notice += "\n일부 Discord 발언 전송에 실패했습니다. DB 기록과 /meeting_summary를 확인해주세요."
            await send_text(interaction.followup.send,
                            f"회의를 저장했습니다. 회의 ID: {meeting.id}\n상태: {meeting.status}{notice}")

        @self.tree.command(name="meeting_summary", description="최근 회의의 결정안과 회의록을 확인합니다.")
        @app_commands.guild_only()
        async def meeting_summary(interaction: discord.Interaction, project_id: str) -> None:
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            meeting = service.latest_meeting(project_id)
            await send_text(interaction.followup.send, format_meeting(meeting) if meeting else "저장된 회의가 없습니다.")

        @self.tree.command(name="project_status", description="현재 프로젝트 상태와 진행 상황을 확인합니다.")
        @app_commands.guild_only()
        async def project_status(interaction: discord.Interaction, project_id: str) -> None:
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            await send_text(interaction.followup.send, format_status(service.status(project_id)))

        @self.tree.command(name="task_list", description="작업별 상태, 담당자와 완료 기준을 확인합니다.")
        @app_commands.guild_only()
        async def task_list(interaction: discord.Interaction, project_id: str) -> None:
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            await send_text(interaction.followup.send, format_tasks(service.list_tasks(project_id)))

        @self.tree.command(name="plan_approve", description="관리자가 특정 회의 계획을 승인하거나 보류합니다.")
        @app_commands.guild_only()
        async def plan_approve(
            interaction: discord.Interaction, project_id: str, meeting_id: str, approve: bool = True,
        ) -> None:
            require_admin(service, interaction)
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            meeting = service.plan_approve(project_id, meeting_id, interaction.user.id, approve=approve)
            notice = await self._sync_notice()
            await send_text(interaction.followup.send, f"회의 {meeting.id}의 계획 처리 결과: {meeting.status}{notice}")

        @self.tree.command(name="daily_report", description="오늘의 사실 기반 일일 보고서를 생성하거나 조회합니다.")
        @app_commands.guild_only()
        async def daily_report(interaction: discord.Interaction, project_id: str) -> None:
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            report = await service.daily_report(project_id)
            notice = await self._sync_notice()
            await send_text(interaction.followup.send, format_report(report) + notice)

        @self.tree.command(name="report_schedule", description="관리자가 서울 시간 HH:MM 또는 off로 자동 보고를 설정합니다.")
        @app_commands.guild_only()
        async def report_schedule(interaction: discord.Interaction, project_id: str, time: str) -> None:
            require_admin(service, interaction)
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            project = service.set_report_time(project_id, None if time.lower() == "off" else time, interaction.user.id)
            if self.scheduler is not None:
                from .scheduler import refresh_scheduler

                refresh_scheduler(self.scheduler, service, self.send_report)
            notice = await self._sync_notice()
            await send_text(interaction.followup.send,
                            f"자동 보고: {project.report_time or '비활성화'} (Asia/Seoul){notice}")

        @self.tree.command(name="task_update", description="관리자가 작업 상태와 확인 근거를 기록합니다.")
        @app_commands.guild_only()
        @app_commands.choices(status=[app_commands.Choice(name=item.value, value=item.value) for item in TaskStatus])
        async def task_update(
            interaction: discord.Interaction, project_id: str, task_id: str, status: str,
            evidence_url: str | None = None, note: str | None = None, blocker: str | None = None,
        ) -> None:
            require_admin(service, interaction)
            require_project_scope(service, interaction, project_id)
            await interaction.response.defer(thinking=True)
            task = await service.update_task(project_id, task_id, TaskStatus(status), interaction.user.id,
                                             evidence_url=evidence_url, note=note, blocker=blocker)
            notice = await self._sync_notice()
            await send_text(interaction.followup.send, format_tasks([task]) + notice)

        @self.tree.command(name="help", description="사용 가능한 AI Virtual Team 명령어를 안내합니다.")
        @app_commands.guild_only()
        async def help_command(interaction: discord.Interaction) -> None:
            require_guild(interaction)
            text = (
                "AI Virtual Team 명령어\n"
                "/project_create name goal [description] [repository] — 관리자 프로젝트 생성\n"
                "/meeting_start project_id topic — 수동 회의 시작\n"
                "/meeting_summary project_id — 최근 회의록\n"
                "/project_status project_id — DB 기준 진행 상황\n"
                "/task_list project_id — 담당자와 완료 기준\n"
                "/plan_approve project_id meeting_id [approve] — 관리자 승인, false는 보류\n"
                "/daily_report project_id — 오늘 보고서 생성/조회\n"
                "/report_schedule project_id time — 관리자 HH:MM(서울) 또는 off\n"
                "/task_update project_id task_id status [evidence_url] [note] [blocker] — 관리자 상태 갱신\n"
                "/help — 명령어 안내\n\n"
                "프로젝트를 만든 서버와 채널(또는 그 스레드)에서 사용합니다. "
                "계획은 승인 전까지 제안이며, Done 처리에는 확인 가능한 근거가 필요합니다. "
                "보고 시각을 설정하지 않으면 자동 보고하지 않습니다."
            )
            await send_text(interaction.response.send_message, text, ephemeral=True)


def create_bot(service: TeamService) -> TeamBot:
    """Construct commands without login, so the adapter is testable offline."""
    return TeamBot(service)


def run_bot(service: TeamService) -> None:
    token = service.settings.discord_bot_token
    if token is None or not token.get_secret_value().strip():
        raise ValueError("Discord 봇 실행에는 DISCORD_BOT_TOKEN이 필요합니다.")
    create_bot(service).run(token.get_secret_value(), log_handler=None)
