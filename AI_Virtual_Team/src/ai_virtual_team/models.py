"""Validated boundaries. Models describe data; they never execute actions."""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return uuid4().hex


Text = Annotated[str, Field(min_length=1, max_length=6000)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(str, Enum):
    PM = "PM"
    DEVELOPER = "Developer"
    QA = "QA"


class TaskStatus(str, Enum):
    PROPOSED = "Proposed"
    APPROVED = "Approved"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    IN_REVIEW = "In Review"
    DONE = "Done"


class TaskProposal(Model):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: Text
    assignee: Role
    priority: Literal["High", "Medium", "Low"] = "Medium"
    acceptance_criteria: Annotated[list[Text], Field(min_length=1, max_length=20)]
    related_links: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("related_links")
    @classmethod
    def safe_links(cls, links: list[str]) -> list[str]:
        if any(not link.startswith("https://") or len(link) > 2000 for link in links):
            raise ValueError("관련 링크는 HTTPS URL이어야 합니다.")
        return links


class MeetingSummary(Model):
    purpose: Text
    main_points: list[Text] = Field(default_factory=list, max_length=20)
    agreements: list[Text] = Field(default_factory=list, max_length=20)
    dissenting_opinions: list[Text] = Field(default_factory=list, max_length=20)
    unresolved_issues: list[Text] = Field(default_factory=list, max_length=20)
    next_steps: list[Text] = Field(default_factory=list, max_length=20)
    approval_required: list[Text] = Field(default_factory=list, max_length=20)


class AgentOutput(Model):
    content: Text
    # No execution tool is connected in this MVP. A model cannot promote its
    # opinion to a successful test run through structured output.
    test_execution: Literal["not_run"] = "not_run"
    review_of: list[int] = Field(default_factory=list, max_length=12)
    concerns: list[Text] = Field(default_factory=list, max_length=20)
    tasks: list[TaskProposal] = Field(default_factory=list, max_length=20)
    summary: MeetingSummary | None = None


class AgentMessage(Model):
    id: str = Field(default_factory=new_id)
    meeting_id: str
    sequence: int
    role: Role
    stage: str
    output: AgentOutput
    created_at: str = Field(default_factory=now_iso)


class Project(Model):
    id: str = Field(default_factory=new_id)
    name: Annotated[str, Field(min_length=1, max_length=200)]
    goal: Text
    description: str = Field(default="", max_length=6000)
    github_repository: str = "https://github.com/itsyoojong/rfp-summary"
    status: str = "Planning"
    guild_id: int | None = None
    channel_id: int | None = None
    created_by: int
    created_at: str = Field(default_factory=now_iso)
    plan_version: int = 0
    revision: int = 1
    report_time: str | None = None


class AgentInput(Model):
    meeting_id: str
    project: Project
    topic: Text
    stage: Literal["agenda", "technical", "review", "response", "synthesis"]
    role: Role
    shared_messages: list[AgentMessage] = Field(default_factory=list)
    private_context: list[AgentMessage] = Field(default_factory=list)
    project_state: dict = Field(default_factory=dict)
    github_evidence: dict = Field(default_factory=dict)


class Meeting(Model):
    id: str = Field(default_factory=new_id)
    project_id: str
    topic: Text
    requested_by: int
    status: Literal["Running", "Pending Approval", "Approved", "Held", "Failed", "Interrupted"] = "Running"
    base_plan_version: int
    summary: MeetingSummary | None = None
    error: str | None = None
    created_at: str = Field(default_factory=now_iso)
    revision: int = 1


class Task(TaskProposal):
    id: str = Field(default_factory=new_id)
    project_id: str
    meeting_id: str
    status: TaskStatus = TaskStatus.PROPOSED
    evidence: list[dict] = Field(default_factory=list)
    verification_note: str | None = None
    blocker: str | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    completed_at: str | None = None
    revision: int = 1


class DailyReport(Model):
    id: str = Field(default_factory=new_id)
    project_id: str
    report_date: str
    since: str | None
    completed_tasks: list[dict] = Field(default_factory=list)
    in_progress_tasks: list[dict] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    github_changes: dict = Field(default_factory=dict)
    decisions_needed: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    revision: int = 1
