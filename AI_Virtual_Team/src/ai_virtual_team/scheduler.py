"""Opt-in Seoul schedules rebuilt from SQLite on every process start.

The report's unique (project,date) key is the idempotency boundary, not an
in-memory scheduler job. Discord delivery has a separate durable ledger.
"""

from datetime import timedelta

from .service import SEOUL


async def run_scheduled_report(service, project_id: str, send_report):
    project = service.get_project(project_id)
    if not project.report_time:
        return None
    report = await service.daily_report(project_id)
    if not service.store.claim_delivery(report.id):
        return report
    try:
        await send_report(project, report)
    except BaseException as exc:
        # A transport failure may occur after Discord accepted the message.
        # Preserve the report; avoid a blind resend that could duplicate it.
        service.store.finish_delivery(report.id, error=type(exc).__name__)
        service.store.activity(project_id, "report_delivery_uncertain", None,
                               {"report_id": report.id, "error": type(exc).__name__})
        raise
    service.store.finish_delivery(report.id)
    return report


async def catch_up_today(service, send_report):
    """At startup, generate today's due report only; never backfill old days."""
    current = service.clock().astimezone(SEOUL)
    for project in service.store.list("project"):
        if project.report_time and current.strftime("%H:%M") >= project.report_time:
            try:
                await run_scheduled_report(service, project.id, send_report)
            except Exception as exc:
                service.store.activity(project.id, "scheduled_report_failed", None,
                                       {"error": type(exc).__name__})


async def retry_notion(service):
    service.store.retry_sync()
    await service.sync_pending()


def refresh_scheduler(scheduler, service, send_report):
    desired = {f"report:{p.id}": p for p in service.store.list("project") if p.report_time}
    for job in scheduler.get_jobs():
        if job.id.startswith("report:") and job.id not in desired:
            scheduler.remove_job(job.id)
    for job_id, project in desired.items():
        hour, minute = map(int, project.report_time.split(":"))
        scheduler.add_job(run_scheduled_report, "cron", id=job_id,
                          hour=hour, minute=minute, timezone=SEOUL,
                          args=[service, project.id, send_report],
                          replace_existing=True, coalesce=True, max_instances=1,
                          misfire_grace_time=3600)


def build_scheduler(service, send_report):
    # Lazy import keeps the offline CLI/demo usable if only this optional runtime
    # dependency is unavailable; bot startup fails clearly instead of silently skipping it.
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError as exc:
        raise RuntimeError("APScheduler가 필요합니다. pip install -e '.[dev]'를 실행해주세요.") from exc
    scheduler = AsyncIOScheduler(timezone=SEOUL)
    refresh_scheduler(scheduler, service, send_report)
    if any(p.report_time for p in service.store.list("project")):
        scheduler.add_job(catch_up_today, "date", id="catch-up-today",
                          run_date=service.clock() + timedelta(seconds=5), args=[service, send_report])
    if service.notion is not None:
        scheduler.add_job(retry_notion, "interval", id="notion-outbox", seconds=60,
                          args=[service], coalesce=True, max_instances=1)
    return scheduler
