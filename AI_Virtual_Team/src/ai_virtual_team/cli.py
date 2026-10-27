"""Local commands share exactly the same application service as Discord."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import Settings
from .database import Store
from .models import Model, TaskStatus
from .runtime import build_service, runtime_lock


def output(value):
    if isinstance(value, Model):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [x.model_dump(mode="json") if isinstance(x, Model) else x for x in value]
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ai-team", description="인간 승인 기반 AI Virtual Team")
    commands = root.add_subparsers(dest="command", required=True)

    def command(name, help_text):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--db", type=Path, help="SQLite 경로 (미지정 시 DATABASE_PATH)")
        return cmd

    command("demo", "외부 API 없이 생성→회의→승인→보고→재시작 검증")
    command("bot", "Discord 봇과 설정된 스케줄 실행")
    command("projects", "로컬 프로젝트 목록")
    create = command("project-create", "관리자 권한으로 프로젝트 생성")
    create.add_argument("--name", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--description", default="")
    create.add_argument("--repository", default="https://github.com/itsyoojong/rfp-summary")
    create.add_argument("--actor-id", type=int, required=True)
    meeting = command("meeting-start", "5단계 회의 실행")
    meeting.add_argument("project_id")
    meeting.add_argument("--topic", required=True)
    meeting.add_argument("--actor-id", type=int, required=True)
    for name in ("status", "tasks", "meeting-summary", "report"):
        cmd = command(name, "저장된 상태 조회 또는 일일 보고 생성")
        cmd.add_argument("project_id")
    approve = command("approve", "관리자 계획 승인 또는 보류")
    approve.add_argument("project_id")
    approve.add_argument("meeting_id")
    approve.add_argument("--actor-id", type=int, required=True)
    approve.add_argument("--hold", action="store_true")
    schedule = command("schedule", "Asia/Seoul 보고 시각 설정; off로 해제")
    schedule.add_argument("project_id")
    schedule.add_argument("--time", required=True)
    schedule.add_argument("--actor-id", type=int, required=True)
    update = command("task-update", "관리자 작업 상태 변경 (Done은 GitHub 근거 필요)")
    update.add_argument("project_id")
    update.add_argument("task_id")
    update.add_argument("--status", choices=[s.value for s in TaskStatus], required=True)
    update.add_argument("--actor-id", type=int, required=True)
    update.add_argument("--evidence-url")
    update.add_argument("--note")
    update.add_argument("--blocker")
    sync = command("sync", "미완료 Notion 동기화 재시도")
    sync.add_argument("--retry-conflicts", action="store_true",
                      help="원격 충돌을 수동 복구한 후 재검사 (덮어쓰기 승인 아님)")
    command("notion-schema", "필요한 Notion data source 속성 출력")
    command("notion-check", "Notion 기존 data source 스키마 읽기 전용 점검")
    commands.add_parser("codex-check", help="Codex 버전·ChatGPT 로그인 로컬 점검 (추론·DB 변경 없음)")
    commands.add_parser("codex-smoke", help="가상 안건으로 실제 PM 추론 1회 검사 (계정 사용량 발생, DB 변경 없음)")
    smoke = command("github-check", "GitHub 실제 GET 연결 확인")
    smoke.add_argument("--repository", default="https://github.com/itsyoojong/rfp-summary")
    return root


async def demo(service):
    # The numeric actor below is an explicit simulation identity, not a Discord credential.
    candidates = service.store.list("project")
    if any(p.name != "[Mock Demo] RFP Insight" or p.created_by != 1
           or p.guild_id is not None or p.channel_id is not None for p in candidates):
        raise ValueError("데모에는 별도의 빈 DB 또는 기존 데모 DB만 사용해주세요.")
    service.recover_interrupted()
    project = candidates[0] if candidates else service.create_project(
        name="[Mock Demo] RFP Insight", goal="RFP 분석 결과의 원문 근거와 완료 기준을 검토합니다.", actor_id=1)
    meeting = service.latest_meeting(project.id)
    if meeting is None or meeting.status in {"Failed", "Interrupted"}:
        async def print_message(message):
            print(f"[{message.role.value} #{message.sequence}] {message.output.content}")

        meeting = await service.meeting_start(project.id, "RFP Insight 읽기 전용 킥오프", 1,
                                              on_message=print_message)
    if meeting.status != "Approved":
        print("[DEMO] 시뮬레이션 리더가 제안된 작업을 승인합니다. 실제 Discord 승인과 무관합니다.")
        service.plan_approve(project.id, meeting.id, 1)
    report = await service.daily_report(project.id)
    again = await service.daily_report(project.id)
    reopened = Store(service.settings.database_path)
    try:
        assert reopened.get("project", project.id).plan_version >= 1
        assert reopened.get("meeting", meeting.id).status == "Approved"
        assert len(reopened.messages(meeting.id)) == 5
        assert report.id == again.id
    finally:
        reopened.close()
    output({"mode": "mock", "external_calls": 0, "project_id": project.id,
            "meeting_id": meeting.id, "messages": len(service.meeting_messages(meeting.id)),
            "tasks": [t.model_dump(mode="json") for t in service.list_tasks(project.id)],
            "report_id": report.id, "same_day_duplicate_prevented": report.id == again.id,
            "restart_persistence_verified": True, "automatic_reports": "disabled"})


async def dispatch(args, settings):
    service = build_service(settings)
    try:
        cmd = args.command
        # Demo must reject non-demo DBs before startup recovery can touch them.
        if cmd != "demo":
            service.recover_interrupted()
        if cmd == "demo":
            await demo(service)
        elif cmd == "bot":
            from .discord_bot import create_bot

            if not settings.discord_bot_token:
                raise ValueError("DISCORD_BOT_TOKEN이 필요합니다.")
            if not settings.admins:
                raise ValueError("ADMIN_USER_IDS를 먼저 설정해주세요.")
            async with create_bot(service) as bot:
                await bot.start(settings.discord_bot_token.get_secret_value())
        elif cmd == "projects":
            output(service.store.list("project"))
        elif cmd == "project-create":
            output(service.create_project(name=args.name, goal=args.goal, description=args.description,
                                           repository=args.repository, actor_id=args.actor_id))
            output(await service.sync_pending())
        elif cmd == "meeting-start":
            output(await service.meeting_start(args.project_id, args.topic, args.actor_id))
        elif cmd == "status":
            output(service.status(args.project_id))
        elif cmd == "tasks":
            output(service.list_tasks(args.project_id))
        elif cmd == "meeting-summary":
            output(service.latest_meeting(args.project_id))
        elif cmd == "approve":
            output(service.plan_approve(args.project_id, args.meeting_id, args.actor_id, approve=not args.hold))
            output(await service.sync_pending())
        elif cmd == "report":
            output(await service.daily_report(args.project_id))
        elif cmd == "schedule":
            output(service.set_report_time(args.project_id, None if args.time == "off" else args.time,
                                           args.actor_id))
            output(await service.sync_pending())
        elif cmd == "task-update":
            output(await service.update_task(args.project_id, args.task_id, TaskStatus(args.status),
                                              args.actor_id, evidence_url=args.evidence_url,
                                              note=args.note, blocker=args.blocker))
        elif cmd == "sync":
            service.store.retry_sync(conflicts=args.retry_conflicts)
            output(await service.sync_pending())
        elif cmd == "notion-check":
            if service.notion is None:
                raise ValueError("Notion token과 네 개의 data source ID가 필요합니다.")
            output(await service.notion.validate_schema())
        elif cmd == "github-check":
            from .integrations.github import GitHubService

            client = GitHubService(token=settings.github_token.get_secret_value() if settings.github_token else None)
            try:
                output(await client.snapshot(args.repository))
            finally:
                await client.aclose()
    finally:
        await service.aclose()


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "notion-schema":
            from .integrations.notion import SCHEMAS

            output(SCHEMAS)
            return 0
        if args.command in {"codex-check", "codex-smoke"}:
            from .codex_llm import CodexLLM
            from .llm import LLMError

            async def check_codex():
                provider = CodexLLM(Settings(llm_mode="codex", llm_max_retries=0))
                try:
                    if args.command == "codex-check":
                        output(await provider.check())
                    else:
                        from .models import AgentInput, Project, Role
                        from .orchestrator import Orchestrator

                        # Simulation identity only; no project is saved or approved.
                        request = AgentInput(
                            meeting_id="live-smoke-no-db",
                            project=Project(name="[Smoke] 가상 할 일 앱", created_by=1,
                                            goal="할 일 추가와 완료 표시의 요구사항 검토"),
                            topic="첫 회의 의제를 짧게 제안하세요. 구현과 테스트는 미실행입니다.",
                            role=Role.PM, stage="agenda",
                        )
                        result = await provider.generate(request)
                        Orchestrator._validate_stage(result, 1, "agenda")
                        output({"provider": "codex", "inference_verified": True,
                                "scope": "PM agenda only", "database_used": False,
                                "output": result.model_dump(mode="json")})
                finally:
                    await provider.aclose()

            try:
                asyncio.run(check_codex())
            except LLMError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            return 0
        if args.command == "demo":
            settings = Settings(_env_file=None, database_path=args.db or Path("runtime/demo.sqlite3"),
                                llm_mode="mock", groq_api_key=None, admin_user_ids="1",
                                github_enabled=False, github_token=None, notion_token=None,
                                discord_bot_token=None, daily_report_time=None)
        else:
            settings = Settings(**({"database_path": args.db} if args.db else {}))
        with runtime_lock(settings.database_path):
            asyncio.run(dispatch(args, settings))
        return 0
    except KeyboardInterrupt:
        print("종료했습니다. 저장된 상태는 다음 실행에서 이어집니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Pydantic and HTTP exceptions can echo secrets; do not print raw exception bodies.
        print(f"실행 실패 ({type(exc).__name__}). README의 환경변수·권한·의존성 설정을 확인해주세요.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
