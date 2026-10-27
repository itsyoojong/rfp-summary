"""Future execution ports, deliberately disconnected from the MVP LLM.

An implementation can be added behind a human authorization boundary later.
The current application never instantiates or calls repository write tools.
"""

from typing import Protocol


class DevelopmentTools(Protocol):
    async def apply_patch(self, repository: str, patch: str) -> dict: ...

    async def run_tests(self, repository: str, command: list[str]) -> dict: ...

    async def create_pull_request(self, repository: str, title: str, body: str) -> dict: ...


class DisabledDevelopmentTools:
    async def apply_patch(self, repository: str, patch: str) -> dict:
        raise PermissionError("MVP에서는 외부 저장소를 수정하지 않습니다.")

    async def run_tests(self, repository: str, command: list[str]) -> dict:
        raise PermissionError("MVP에서는 외부 저장소의 테스트를 실행하지 않습니다.")

    async def create_pull_request(self, repository: str, title: str, body: str) -> dict:
        raise PermissionError("MVP에서는 Pull Request를 생성하지 않습니다.")
