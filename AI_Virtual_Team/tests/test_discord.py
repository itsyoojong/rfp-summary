"""Offline checks of command routing, access boundaries and delivery failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from ai_virtual_team.config import Settings
from ai_virtual_team.discord_bot import (
    CommandAccessError,
    chunks,
    create_bot,
    format_agent_message,
    format_report,
    format_status,
    meeting_target,
    require_admin,
    require_project_scope,
    run_bot,
    send_text,
)
from ai_virtual_team.models import AgentMessage, AgentOutput, DailyReport, Meeting, Project, Role


@pytest.fixture
def service():
    project = Project(id="project-1", name="RFP", goal="근거 확인", created_by=123,
                      guild_id=10, channel_id=20)
    return SimpleNamespace(
        settings=Settings(_env_file=None, admin_user_ids="123"),
        get_project=MagicMock(return_value=project),
        create_project=MagicMock(return_value=project),
        list_tasks=MagicMock(return_value=[]),
        status=MagicMock(return_value={"state": "Planning"}),
        latest_meeting=MagicMock(return_value=None),
        plan_approve=MagicMock(),
        meeting_start=AsyncMock(),
        daily_report=AsyncMock(),
        set_report_time=MagicMock(return_value=project),
        update_task=AsyncMock(),
        sync_pending=AsyncMock(return_value={}),
    )


def interaction(*, user_id=123, guild_id=10, channel_id=20, channel=None):
    if channel is None:
        channel = SimpleNamespace(id=channel_id, send=AsyncMock())
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), guild_id=guild_id, channel_id=channel_id, channel=channel,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=MagicMock(return_value=False)),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_registers_required_commands_without_login(service):
    bot = create_bot(service)
    names = {command.name for command in bot.tree.get_commands()}
    assert names == {
        "project_create", "meeting_start", "meeting_summary", "project_status", "task_list",
        "plan_approve", "daily_report", "help", "report_schedule", "task_update",
    }
    assert all(command.guild_only for command in bot.tree.get_commands())
    assert bot.intents.guilds
    assert not bot.intents.message_content
    assert bot.scheduler is None


@pytest.mark.parametrize("guild_id,channel_id", [(None, None), (11, 20), (10, 21)])
def test_read_scope_denies_dm_other_guild_and_other_channel(service, guild_id, channel_id):
    with pytest.raises(CommandAccessError):
        require_project_scope(service, interaction(guild_id=guild_id, channel_id=channel_id), "project-1")


def test_scope_allows_project_parent_thread(service):
    thread = MagicMock(spec=discord.Thread)
    thread.parent_id = 20
    assert require_project_scope(service, interaction(channel_id=30, channel=thread), "project-1").id == "project-1"
    thread.parent_id = 21
    with pytest.raises(CommandAccessError):
        require_project_scope(service, interaction(channel_id=30, channel=thread), "project-1")


def test_no_implicit_admin_when_admin_list_empty(service):
    service.settings = Settings(_env_file=None, admin_user_ids="")
    with pytest.raises(CommandAccessError):
        require_admin(service, interaction())


async def test_project_create_denies_nonadmin_before_mutation(service):
    bot = create_bot(service)
    request = interaction(user_id=999)
    with pytest.raises(CommandAccessError):
        await bot.tree.get_command("project_create").callback(request, "name", "goal")
    service.create_project.assert_not_called()
    request.response.defer.assert_not_awaited()


@pytest.mark.parametrize("name,arguments", [
    ("meeting_start", ["project-1", "kickoff"]),
    ("meeting_summary", ["project-1"]),
    ("project_status", ["project-1"]),
    ("task_list", ["project-1"]),
    ("plan_approve", ["project-1", "meeting-1"]),
    ("daily_report", ["project-1"]),
    ("report_schedule", ["project-1", "09:00"]),
    ("task_update", ["project-1", "task-1", "In Progress"]),
])
async def test_every_project_command_enforces_scope(service, name, arguments):
    request = interaction(guild_id=11)
    with pytest.raises(CommandAccessError):
        await create_bot(service).tree.get_command(name).callback(request, *arguments)
    request.response.defer.assert_not_awaited()
    request.followup.send.assert_not_awaited()


async def test_thread_permission_failure_falls_back_to_current_channel():
    channel = MagicMock(spec=discord.TextChannel)
    channel.create_thread = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing permission"))
    target, fallback = await meeting_target(interaction(channel=channel), "@everyone kickoff")
    assert target is channel
    assert fallback is True
    assert channel.create_thread.call_args.kwargs["type"] == discord.ChannelType.public_thread


async def test_delivery_failure_does_not_abort_meeting(service):
    request = interaction()
    request.channel.send.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing permission")
    meeting = Meeting(id="meeting-1", project_id="project-1", topic="kickoff", requested_by=123,
                      base_plan_version=0, status="Pending Approval")
    message = AgentMessage(meeting_id=meeting.id, sequence=1, role=Role.PM, stage="agenda",
                           output=AgentOutput(content="목표를 확인합니다."))

    async def start(project_id, topic, actor_id, on_message):
        # This simulates the service's persisted-before-publish contract.
        await on_message(message)
        return meeting

    service.meeting_start.side_effect = start
    await create_bot(service).tree.get_command("meeting_start").callback(request, "project-1", "kickoff")
    service.meeting_start.assert_awaited_once()
    service.sync_pending.assert_awaited_once()
    output = " ".join(call.kwargs["content"] for call in request.followup.send.await_args_list)
    assert "meeting-1" in output
    assert "일부 Discord 발언 전송에 실패" in output


async def test_output_chunks_disable_mentions_and_embeds():
    text = "@everyone <@123456789012345678> @here " * 150
    send = AsyncMock()
    await send_text(send, text, ephemeral=True)
    assert len(send.await_args_list) > 1
    for call in send.await_args_list:
        kwargs = call.kwargs
        assert len(kwargs["content"]) <= 1900
        assert "@everyone" not in kwargs["content"]
        assert not kwargs["allowed_mentions"].everyone
        assert not kwargs["allowed_mentions"].users
        assert not kwargs["allowed_mentions"].roles
        assert kwargs["suppress_embeds"] is True
        assert kwargs["ephemeral"] is True
    assert "".join(chunks(text)) == discord.utils.escape_mentions(text)


async def test_errors_do_not_echo_exception_secrets(service):
    request = interaction()
    secret = "groq-secret-do-not-print"
    error = app_commands.CommandInvokeError(create_bot(service).tree.get_command("help"), RuntimeError(secret))
    await create_bot(service).on_command_error(request, error)
    rendered = request.response.send_message.call_args.kwargs["content"]
    assert secret not in rendered
    assert "실패" in rendered


def test_report_labels_facts_and_proposals_and_agent_roles():
    report = DailyReport(project_id="project-1", report_date="2026-10-26", since=None,
                         github_changes={"error": "연결 미검증"}, suggestions=["추가 검토를 제안합니다."])
    result = format_report(report)
    assert "추정·제안 (확인된 사실 아님)" in result
    assert "연결 미검증" in result
    assert "테스트 통과를 의미하지 않습니다" in result
    message = AgentMessage(meeting_id="meeting-1", sequence=3, role=Role.QA, stage="review",
                           output=AgentOutput(content="검증이 필요합니다.", review_of=[2]))
    assert "[QA · 발언 3" in format_agent_message(message)
    assert "검토한 발언: 2" in format_agent_message(message)


def test_run_bot_requires_real_token(service):
    with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
        run_bot(service)


@pytest.mark.parametrize("result,expected", [
    ({"enabled": False, "queue": {"pending": 1}}, "Notion 미연결"),
    ({"enabled": True, "queue": {"conflict": 1}}, "충돌"),
    ({"enabled": True, "queue": {"failed": 1}}, "대기"),
    ({"enabled": True, "queue": {}}, ""),
])
async def test_sync_notice_understands_real_service_result(service, result, expected):
    service.sync_pending.return_value = result
    notice = await create_bot(service)._sync_notice()
    assert expected in notice
    if not expected:
        assert notice == ""


async def test_send_report_waits_until_ready_and_checks_destination(service):
    bot = create_bot(service)
    order = []
    bot.wait_until_ready = AsyncMock(side_effect=lambda: order.append("ready"))
    channel = SimpleNamespace(guild=SimpleNamespace(id=10), send=AsyncMock())

    def cached_channel(channel_id):
        assert order == ["ready"]
        return channel

    bot.get_channel = MagicMock(side_effect=cached_channel)
    report = DailyReport(project_id="project-1", report_date="2026-10-26", since=None)
    await bot.send_report(service.get_project("project-1"), report)
    channel.send.assert_awaited()
    channel.send.reset_mock()
    order.clear()
    channel.guild.id = 999
    with pytest.raises(ValueError, match="서버/채널"):
        await bot.send_report(service.get_project("project-1"), report)
    channel.send.assert_not_awaited()


def test_report_bounds_github_output_and_includes_completion_evidence():
    commits = [{"sha": f"sha-{i}", "message": f"commit-message-{i}",
                "url": f"https://github.com/owner/repo/commit/{i}"} for i in range(25)]
    report = DailyReport(
        project_id="project-1", report_date="2026-10-26", since=None,
        completed_tasks=[{"title": "근거 검증", "assignee": "Developer",
                          "evidence": [{"url": "https://github.com/owner/repo/pull/1"}],
                          "verification_note": "완료 기준을 원문과 대조했습니다."}],
        github_changes={"files": [{"path": "do-not-dump-this-file"}] * 5000,
                        "commits": commits, "pull_requests": [], "issues": [],
                        "errors": {"issues": "조회 실패"}, "limitations": ["조회 상한이 있습니다."]},
    )
    output = format_report(report)
    assert "do-not-dump-this-file" not in output
    assert "트리 항목: 5000개" in output
    assert "조회된 커밋: 25건" in output
    assert output.count("https://github.com/owner/repo/commit/") == 10
    assert "추가 커밋 15건" in output
    assert "issues 조회 오류: 조회 실패" in output
    assert "조회된 Issue: 0건" not in output
    assert "조회 상한이 있습니다." in output
    assert "https://github.com/owner/repo/pull/1" in output
    assert "인간의 완료 기준 검토 기록: 완료 기준을 원문과 대조했습니다." in output
    assert len(output) < 6000


def test_status_does_not_dump_full_meeting_or_report(service):
    output = format_status({
        "project": service.get_project("project-1").model_dump(mode="json"),
        "task_counts": {"Approved": 3}, "notion_enabled": False, "sync_queue": {"pending": 1},
        "latest_meeting": {"id": "meeting-1", "status": "Pending Approval", "summary": "do-not-dump"},
        "latest_report": {"id": "report-1", "report_date": "2026-10-26", "github_changes": "do-not-dump"},
    })
    assert "do-not-dump" not in output
    assert "Approved 3건" in output
    assert "meeting-1" in output
    assert "report-1" in output


async def test_real_service_command_flow_without_live_discord():
    from ai_virtual_team.database import Store
    from ai_virtual_team.llm import MockLLM
    from ai_virtual_team.orchestrator import Orchestrator
    from ai_virtual_team.service import TeamService

    settings = Settings(_env_file=None, admin_user_ids="123", daily_report_time="09:00")
    service = TeamService(settings, Store(":memory:"), Orchestrator(MockLLM()))
    bot = create_bot(service)
    try:
        request = interaction()
        await bot.tree.get_command("project_create").callback(request, "RFP", "원문 근거 확인")
        project = service.store.list("project")[0]
        assert project.report_time == "09:00"
        await bot.tree.get_command("meeting_start").callback(interaction(), project.id, "킥오프")
        meeting = service.latest_meeting(project.id)
        assert meeting.status == "Pending Approval"
        assert len(service.meeting_messages(meeting.id)) == 5
        await bot.tree.get_command("plan_approve").callback(interaction(), project.id, meeting.id)
        assert all(task.status.value == "Approved" for task in service.list_tasks(project.id))
        await bot.tree.get_command("daily_report").callback(interaction(), project.id)
        assert len(service.store.list("report", project.id)) == 1
        await bot.tree.get_command("report_schedule").callback(interaction(), project.id, "off")
        assert service.get_project(project.id).report_time is None
    finally:
        await service.aclose()


async def test_new_project_refreshes_existing_scheduler_for_default_time(service, monkeypatch):
    from ai_virtual_team import scheduler

    service.create_project.return_value.report_time = "09:00"
    refresh = MagicMock()
    monkeypatch.setattr(scheduler, "refresh_scheduler", refresh)
    bot = create_bot(service)
    bot.scheduler = SimpleNamespace()
    await bot.tree.get_command("project_create").callback(interaction(), "RFP", "근거 확인")
    refresh.assert_called_once_with(bot.scheduler, service, bot.send_report)
