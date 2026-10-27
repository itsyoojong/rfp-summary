"""Explicit five-step meeting workflow, independent of Discord and persistence.

The callback is awaited immediately after each validated speech. This makes
failure recovery possible without coupling an LLM call to a DB transaction.
"""

from collections.abc import Awaitable, Callable

from .llm import LLMProvider
from .models import AgentInput, AgentMessage, AgentOutput, Meeting, Project, Role


class WorkflowError(ValueError):
    pass


class Orchestrator:
    STEPS = (
        (Role.PM, "agenda"),
        (Role.DEVELOPER, "technical"),
        (Role.QA, "review"),
        (Role.DEVELOPER, "response"),
        (Role.PM, "synthesis"),
    )

    def __init__(self, provider: LLMProvider, max_turns: int = 5):
        if not 5 <= max_turns <= 12:
            raise ValueError("회의 발언 상한은 5~12이며 MVP의 실제 흐름은 5회입니다.")
        self.provider = provider
        self.max_turns = max_turns

    @staticmethod
    def _validate_stage(output: AgentOutput, sequence: int, stage: str) -> None:
        if any(ref < 1 or ref >= sequence for ref in output.review_of):
            raise WorkflowError("검토 참조는 앞선 발언 번호만 허용됩니다.")
        required_refs = {"technical": {1}, "review": {2}, "response": {3}, "synthesis": {3, 4}}
        if not required_refs.get(stage, set()).issubset(output.review_of):
            raise WorkflowError(f"{stage} 단계에 필요한 상호 검토 참조가 누락되었습니다.")
        if stage != "synthesis" and (output.tasks or output.summary is not None):
            raise WorkflowError("최종 PM 종합 단계만 작업 초안과 회의록을 제안할 수 있습니다.")
        if stage == "synthesis" and output.summary is None:
            raise WorkflowError("PM의 최종 출력에는 구조화된 회의록이 필요합니다.")
        if stage == "synthesis" and output.tasks and not output.summary.approval_required:
            raise WorkflowError("작업 제안에는 인간 승인 필요 항목이 포함되어야 합니다.")

    async def run(
        self,
        project: Project,
        meeting: Meeting,
        github_evidence: dict,
        on_message: Callable[[AgentMessage], Awaitable[None]],
        *,
        project_state: dict | None = None,
    ) -> AgentOutput:
        project = Project.model_validate(project.model_dump())
        meeting = Meeting.model_validate(meeting.model_dump())
        if meeting.project_id != project.id:
            raise WorkflowError("회의와 프로젝트가 일치하지 않습니다.")
        if meeting.status != "Running":
            raise WorkflowError("실행 중인 회의만 오케스트레이션할 수 있습니다.")
        shared: list[AgentMessage] = []
        private: dict[Role, list[AgentMessage]] = {role: [] for role in Role}
        final: AgentOutput | None = None
        for sequence, (role, stage) in enumerate(self.STEPS, start=1):
            if sequence > self.max_turns:
                raise WorkflowError("회의 발언 상한에 도달했습니다.")
            request = AgentInput(
                meeting_id=meeting.id, project=project, topic=meeting.topic,
                role=role, stage=stage, shared_messages=shared,
                private_context=private[role], github_evidence=github_evidence,
                project_state=project_state or {},
            )
            # Revalidate even if a custom provider returns a dict. No provider
            # can skip this boundary by returning a model constructed unsafely.
            raw = await self.provider.generate(request)
            output = AgentOutput.model_validate(raw.model_dump() if isinstance(raw, AgentOutput) else raw)
            self._validate_stage(output, sequence, stage)
            message = AgentMessage(
                meeting_id=meeting.id, sequence=sequence, role=role, stage=stage, output=output,
            )
            await on_message(message)
            shared.append(message)
            private[role].append(message)
            final = output
        if final is None:
            raise WorkflowError("회의 출력이 없습니다.")
        return final
