"""Notion is a projection of SQLite, never the authoritative state.

The current API uses data sources, not database parents. Configure four existing
data sources with the exact property types in ``SCHEMAS``; ``validate_schema`` is
read-only. This adapter never creates databases or changes their schemas.

Every row has a stable ``kind:entity_id`` external key. The caller must serialize
sync for an entity and durably mark an attempt before calling ``upsert``. After an
uncertain create or an interrupted attempt, pass ``ambiguous_create=True`` in
``prior``. Reconciliation may find the row, but an absent row then requires human
inspection; it never triggers another blind POST. Notion has no uniqueness or
compare-and-swap primitive, so this is not a distributed exactly-once guarantee.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

API_VERSION = "2026-03-11"
COMMON_SCHEMA = {
    "Name": "title",
    "External ID": "rich_text",
    "Revision": "number",
    "Payload Hash": "rich_text",
}
SCHEMAS = {
    "project": COMMON_SCHEMA | {
        "Goal": "rich_text", "Description": "rich_text", "Status": "select",
        "GitHub": "url", "Created": "date",
    },
    "task": COMMON_SCHEMA | {
        "Description": "rich_text", "Agent": "select", "Priority": "select",
        "Status": "select", "Acceptance Criteria": "rich_text", "Links": "rich_text",
        "Project ID": "rich_text", "Evidence": "rich_text", "Verification": "rich_text",
    },
    "meeting": COMMON_SCHEMA | {
        "Date": "date", "Topic": "rich_text", "Participants": "multi_select",
        "Summary": "rich_text", "Decisions": "rich_text", "Follow-ups": "rich_text",
        "Project ID": "rich_text", "Status": "select",
    },
    "report": COMMON_SCHEMA | {
        "Date": "date", "Completed": "rich_text", "In Progress": "rich_text",
        "Blockers": "rich_text", "Decisions": "rich_text", "Next Steps": "rich_text",
        "GitHub": "rich_text", "Suggestions": "rich_text", "Project ID": "rich_text",
    },
}


class NotionError(RuntimeError):
    """A bounded remote operation failed; retain local state and outbox entry."""


class NotionNoWriteError(NotionError):
    """This attempt definitely did not create a page.

    A caller may clear its attempt marker ONLY if there was no earlier ambiguous
    attempt and no existing page mapping. Earlier uncertainty is always sticky.
    """


class NotionConflict(NotionError):
    """Remote modification or duplicate key requires human review."""


class NotionAmbiguousCreate(NotionError):
    """Persist ambiguous_create=True and reconcile before any subsequent create."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _rich_text(value: Any) -> list[dict]:
    text = _text(value)
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    if len(chunks) > 100:
        raise NotionError("Notion 속성 길이 제한 초과: 원본은 내부 DB에 보존됩니다.")
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _date_value(value: str | None) -> str | None:
    if not value or len(value) == 10:
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="milliseconds")


def _normal_properties(properties: dict, schema: dict) -> dict:
    """Ignore response-only ids/colors, but compare every managed value."""
    result = {}
    for name, kind in schema.items():
        prop = properties.get(name, {})
        value = prop.get(kind)
        if kind in {"title", "rich_text"}:
            result[name] = "".join(
                item.get("plain_text", item.get("text", {}).get("content", "")) for item in value or []
            )
        elif kind == "select":
            result[name] = value.get("name") if value else None
        elif kind == "multi_select":
            result[name] = sorted(item["name"] for item in value or [])
        elif kind == "date":
            result[name] = {
                "start": _date_value(value.get("start")), "end": _date_value(value.get("end"))
            } if value else None
        elif kind == "number":
            result[name] = float(value) if value is not None else None
        else:
            result[name] = value
    return result


def _properties(kind: str, entity_id: str, revision: int, fields: dict) -> dict:
    title = fields.get("name") or fields.get("title") or fields.get("topic")
    if kind == "report":
        title = f"Daily Report {fields.get('report_date', entity_id)}"
    values = {"Name": title or entity_id, "External ID": f"{kind}:{entity_id}", "Revision": revision}
    if kind == "project":
        repository = fields.get("github_repository") or None
        if repository and not repository.startswith("https://"):
            repository = "https://github.com/" + repository
        values |= {
            "Goal": fields.get("goal"), "Description": fields.get("description"),
            "Status": fields.get("status"), "GitHub": repository, "Created": fields.get("created_at"),
        }
    elif kind == "task":
        values |= {
            "Description": fields.get("description"), "Agent": fields.get("assignee"),
            "Priority": fields.get("priority"), "Status": fields.get("status"),
            "Acceptance Criteria": fields.get("acceptance_criteria", []),
            "Links": fields.get("related_links", []), "Project ID": fields.get("project_id"),
            "Evidence": fields.get("evidence", []), "Verification": fields.get("verification_note"),
        }
    elif kind == "meeting":
        summary = fields.get("summary") or {}
        values |= {
            "Date": fields.get("created_at"), "Topic": fields.get("topic"),
            "Participants": ["PM", "Developer", "QA"], "Summary": summary,
            "Decisions": summary.get("agreements", []), "Follow-ups": summary.get("next_steps", []),
            "Project ID": fields.get("project_id"), "Status": fields.get("status"),
        }
    elif kind == "report":
        values |= {
            "Date": fields.get("report_date"), "Completed": fields.get("completed_tasks", []),
            "In Progress": fields.get("in_progress_tasks", []), "Blockers": fields.get("blockers", []),
            "Decisions": fields.get("decisions_needed", []), "Next Steps": fields.get("next_steps", []),
            "GitHub": fields.get("github_changes", {}), "Suggestions": fields.get("suggestions", []),
            "Project ID": fields.get("project_id"),
        }
    props = {}
    for name, property_type in SCHEMAS[kind].items():
        if name == "Payload Hash":
            continue
        value = values.get(name)
        if property_type in {"title", "rich_text"}:
            props[name] = {property_type: _rich_text(value)}
        elif property_type == "select":
            props[name] = {property_type: {"name": _text(value)} if value else None}
        elif property_type == "multi_select":
            props[name] = {property_type: [{"name": str(item)} for item in value or []]}
        elif property_type == "date":
            props[name] = {property_type: {"start": str(value)} if value else None}
        else:
            props[name] = {property_type: value}
    props["Payload Hash"] = {"rich_text": _rich_text(_digest(_normal_properties(props, SCHEMAS[kind])))}
    if len(json.dumps(props, ensure_ascii=False).encode()) > 450_000:
        raise NotionError("Notion 요청 크기 제한 초과: 원본은 내부 DB에 보존됩니다.")
    return props


class NotionService:
    """Bounded, conflict-aware row synchronization for existing data sources."""

    def __init__(
        self, token: str, data_source_ids: dict[str, str], version: str = API_VERSION,
        client: httpx.AsyncClient | None = None, *, timeout: float = 20, max_retries: int = 2,
    ):
        if not token:
            raise ValueError("Notion token이 필요합니다.")
        if not 0 < timeout <= 120 or not 0 <= max_retries <= 5:
            raise ValueError("timeout/retry 제한이 올바르지 않습니다.")
        self.data_source_ids = {}
        for kind, identifier in data_source_ids.items():
            if kind not in SCHEMAS:
                raise ValueError(f"알 수 없는 Notion entity 종류: {kind}")
            self.data_source_ids[kind] = str(UUID(identifier))
        self._headers = {
            "Authorization": f"Bearer {token}", "Notion-Version": version,
            "Content-Type": "application/json",
        }
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, *, payload: dict | None = None,
                       safe: bool = False) -> dict:
        attempts = self._max_retries + 1 if safe else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, "https://api.notion.com/v1" + path, headers=self._headers,
                    json=payload, timeout=self._timeout, follow_redirects=False,
                )
            except httpx.TransportError as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(0.25 * 2 ** attempt, 2))
                    continue
                if method == "POST" and path == "/pages":
                    raise NotionAmbiguousCreate("Notion 생성 응답을 확인하지 못했습니다. 재조정이 필요합니다.") from exc
                raise NotionError("Notion 연결 실패: 내부 상태와 동기화 요청은 보존됩니다.") from exc
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < attempts:
                    try:
                        delay = max(float(response.headers.get("Retry-After", "0.5")), 0)
                    except ValueError:
                        delay = 0.5
                    if delay > 2:
                        raise NotionError("Notion 재시도 대기 시간이 길어 동기화를 다음 실행으로 보류합니다.")
                    await asyncio.sleep(delay)
                    continue
                if method == "POST" and path == "/pages" and response.status_code >= 500:
                    raise NotionAmbiguousCreate(f"Notion 생성 응답 HTTP {response.status_code}: 결과 확인이 필요합니다.")
            if not response.is_success:
                if method == "POST" and path == "/pages":
                    if response.status_code in {400, 401, 403, 404, 409, 422, 429}:
                        raise NotionNoWriteError(f"Notion 생성 거절 HTTP {response.status_code}")
                    raise NotionAmbiguousCreate(f"Notion 생성 응답 HTTP {response.status_code}: 결과 확인이 필요합니다.")
                raise NotionError(f"Notion HTTP {response.status_code} ({method} {path})")
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Expected object")
                return data
            except (ValueError, TypeError) as exc:
                if method == "POST" and path == "/pages":
                    raise NotionAmbiguousCreate("Notion 생성 응답을 파싱하지 못했습니다.") from exc
                raise NotionError("Notion 응답 형식을 확인할 수 없습니다.") from exc
        raise AssertionError("unreachable")

    async def validate_schema(self, kind: str | None = None) -> dict:
        """Read existing schemas only; never auto-create properties or data sources."""
        kinds = [kind] if kind else list(self.data_source_ids)
        results = {}
        for current in kinds:
            if current not in self.data_source_ids:
                raise ValueError(f"Notion data source 설정이 없습니다: {current}")
            data = await self._request("GET", f"/data_sources/{self.data_source_ids[current]}", safe=True)
            properties = data.get("properties", {})
            missing = [name for name in SCHEMAS[current] if name not in properties]
            mismatched = {
                name: {"expected": prop_type, "actual": properties[name].get("type")}
                for name, prop_type in SCHEMAS[current].items()
                if name in properties and properties[name].get("type") != prop_type
            }
            results[current] = {"valid": not missing and not mismatched,
                                "missing": missing, "mismatched": mismatched}
        return results

    async def _find(self, kind: str, external_id: str) -> dict | None:
        data = await self._request(
            "POST", f"/data_sources/{self.data_source_ids[kind]}/query", safe=True,
            payload={"filter": {"property": "External ID", "rich_text": {"equals": external_id}},
                     "page_size": 2},
        )
        pages = data.get("results")
        if not isinstance(pages, list):
            raise NotionError("Notion data source 조회 결과가 잘못되었습니다.")
        if len(pages) > 1 or data.get("has_more"):
            raise NotionConflict(f"동일한 External ID의 Notion 항목이 여러 개입니다: {external_id}")
        return pages[0] if pages else None

    @staticmethod
    def _metadata(page: dict, kind: str) -> dict:
        values = _normal_properties(page.get("properties", {}), SCHEMAS[kind])
        if not page.get("id") or not page.get("last_edited_time"):
            raise NotionError("Notion 페이지 식별/수정 시각을 확인할 수 없습니다.")
        return {
            "page_id": page["id"], "last_edited_time": page["last_edited_time"],
            "payload_hash": values["Payload Hash"], "revision": values["Revision"],
            "remote_fingerprint": _digest(values), "ambiguous_create": False,
        }

    async def upsert(self, kind: str, entity_id: str, revision: int, fields: dict,
                     prior: dict | None = None) -> dict:
        prior = prior or {}
        try:
            if kind not in self.data_source_ids:
                raise NotionError(f"Notion data source 설정이 없습니다: {kind}")
            if not entity_id or len(entity_id) > 200 or revision < 1:
                raise NotionError("entity ID와 revision을 확인하세요.")
            desired = _properties(kind, entity_id, revision, fields)
            desired_values = _normal_properties(desired, SCHEMAS[kind])
            page = await self._find(kind, f"{kind}:{entity_id}")
        except NotionConflict:
            raise
        except (NotionError, ValueError, TypeError) as exc:
            if prior.get("ambiguous_create"):
                # A failed read today cannot prove that yesterday's POST failed.
                raise NotionAmbiguousCreate("이전 생성 결과가 불확실하며 이번 조회/검증도 실패했습니다.") from exc
            raise NotionNoWriteError("Notion 쓰기 전 조회/검증 실패: 이번 시도에서는 페이지를 생성하지 않았습니다.") from exc
        if prior.get("page_id"):
            if page and page["id"] != prior["page_id"]:
                raise NotionConflict("기존 페이지와 External ID 조회 결과가 다릅니다.")
            page_id = str(UUID(prior["page_id"]))
            page = await self._request("GET", f"/pages/{page_id}", safe=True)
        if not page:
            if prior.get("ambiguous_create"):
                raise NotionAmbiguousCreate("이전 생성 결과가 불확실합니다. 중복 방지를 위해 자동 생성을 보류합니다.")
            page = await self._request(
                "POST", "/pages", payload={
                    "parent": {"type": "data_source_id", "data_source_id": self.data_source_ids[kind]},
                    "properties": desired,
                },
            )
            # A malformed successful response is also ambiguous: never blind-create again.
            try:
                result = self._metadata(page, kind)
                matches = _normal_properties(page.get("properties", {}), SCHEMAS[kind]) == desired_values
            except (NotionError, ValueError, TypeError, KeyError) as exc:
                raise NotionAmbiguousCreate("생성된 Notion 페이지 메타데이터를 확인할 수 없습니다.") from exc
            if not matches:
                raise NotionAmbiguousCreate("생성 결과 속성이 요청과 다릅니다. 원격 항목을 확인하세요.")
            return result
        parent = page.get("parent", {})
        if parent.get("data_source_id") and UUID(parent["data_source_id"]) != UUID(self.data_source_ids[kind]):
            raise NotionConflict("Notion 항목의 data source가 변경되었습니다.")
        if page.get("in_trash"):
            raise NotionConflict("Notion 항목이 휴지통으로 이동되었습니다.")
        current_values = _normal_properties(page.get("properties", {}), SCHEMAS[kind])
        if current_values == desired_values:
            # This also reconciles a successful PATCH/POST whose response was lost.
            return self._metadata(page, kind)
        if not prior.get("page_id") or not prior.get("remote_fingerprint"):
            raise NotionConflict("기존 Notion 항목의 로컬 동기화 기준이 없어 덮어쓰기를 보류합니다.")
        if current_values.get("External ID") != f"{kind}:{entity_id}":
            raise NotionConflict("Notion External ID가 변경되었습니다.")
        if prior.get("remote_fingerprint") != _digest(current_values):
            raise NotionConflict("Notion에서 관리 속성이 수정되었습니다. 내부 DB를 유지하고 동기화를 보류합니다.")
        if prior.get("last_edited_time") != page.get("last_edited_time"):
            raise NotionConflict("Notion 페이지 수정 시각이 변경되었습니다. 충돌 검토가 필요합니다.")
        if (current_values.get("Revision") or 0) >= revision:
            raise NotionConflict("Notion revision이 새 로컬 변경보다 같거나 높습니다.")
        page_id = str(UUID(page["id"]))
        updated = await self._request("PATCH", f"/pages/{page_id}", payload={"properties": desired})
        if _normal_properties(updated.get("properties", {}), SCHEMAS[kind]) != desired_values:
            raise NotionConflict("Notion 수정 결과가 요청과 다릅니다. 재조정이 필요합니다.")
        return self._metadata(updated, kind)
