"""HTTP adapter tests never connect to live services."""

import base64
import copy
import json

import httpx
import pytest

from ai_virtual_team.integrations.github import GitHubError, GitHubService, parse_repository
from ai_virtual_team.integrations.notion import (
    SCHEMAS,
    NotionAmbiguousCreate,
    NotionConflict,
    NotionError,
    NotionNoWriteError,
    NotionService,
)

SOURCE_ID = "d9824bdc-8445-4327-be8b-5b47500af6ce"
PAGE_ID = "11111111-2222-4333-8444-555555555555"
PROJECT_FIELDS = {
    "name": "RFP Insight", "goal": "근거를 검증한다", "description": "RFP 분석 도구",
    "status": "Planning", "github_repository": "https://github.com/itsyoojong/rfp-summary",
    "created_at": "2026-10-26T03:00:00.000000+00:00",
}


class NotionMemory:
    """Server simulation: no token, no actual Notion writes."""

    def __init__(self):
        self.pages = []
        self.requests = []
        self.creates = 0
        self.patches = 0
        self.lose_create_response = False

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "api.notion.com"
        assert request.headers["Notion-Version"] == "2026-03-11"
        path = request.url.path
        payload = json.loads(request.content) if request.content else None
        if path == f"/v1/data_sources/{SOURCE_ID}":
            return httpx.Response(200, json={"properties": {
                name: {"type": kind} for name, kind in SCHEMAS["project"].items()
            }})
        if path.endswith("/query"):
            assert payload["filter"]["property"] == "External ID"
            return httpx.Response(200, json={"results": self.pages, "has_more": False})
        if path == "/v1/pages" and request.method == "POST":
            self.creates += 1
            assert payload["parent"] == {"type": "data_source_id", "data_source_id": SOURCE_ID}
            page = {
                "id": PAGE_ID, "last_edited_time": "2026-10-26T03:00:00.000Z",
                "parent": payload["parent"], "properties": payload["properties"], "in_trash": False,
            }
            if "Created" in page["properties"]:
                page["properties"]["Created"]["date"]["start"] = "2026-10-26T03:00:00.000Z"
            self.pages.append(page)
            if self.lose_create_response:
                raise httpx.ReadTimeout("response lost after remote commit", request=request)
            return httpx.Response(200, json=page)
        if path == f"/v1/pages/{PAGE_ID}":
            if request.method == "PATCH":
                self.patches += 1
                self.pages[0]["properties"] = payload["properties"]
                self.pages[0]["last_edited_time"] = f"2026-10-26T03:{self.patches:02d}:00.000Z"
            return httpx.Response(200, json=self.pages[0])
        raise AssertionError(f"Unexpected request: {request.method} {path}")


async def test_notion_current_data_source_schema_and_idempotent_create():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        assert (await service.validate_schema())["project"]["valid"] is True
        first = await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        second = await service.upsert("project", "p1", 1, PROJECT_FIELDS, first)
        recovered = await service.upsert("project", "p1", 1, PROJECT_FIELDS)
    assert first == second == recovered
    assert memory.creates == 1
    assert memory.patches == 0
    assert first["page_id"] == PAGE_ID
    assert first["revision"] == 1
    assert len(first["remote_fingerprint"]) == 64
    assert all("/databases/" not in str(request.url) for request in memory.requests)


async def test_notion_new_revision_updates_existing_page():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        first = await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        second = await service.upsert("project", "p1", 2, PROJECT_FIELDS | {"status": "Active"}, first)
    assert second["revision"] == 2
    assert memory.creates == 1 and memory.patches == 1
    assert first["remote_fingerprint"] != second["remote_fingerprint"]


async def test_notion_human_property_edit_is_not_overwritten():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        first = await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        memory.pages[0]["properties"]["Goal"]["rich_text"][0]["text"]["content"] = "human edited"
        with pytest.raises(NotionConflict, match="관리 속성"):
            await service.upsert("project", "p1", 2, PROJECT_FIELDS | {"status": "Active"}, first)
    assert memory.patches == 0


async def test_notion_page_edit_timestamp_conflicts_before_new_revision():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        first = await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        memory.pages[0]["last_edited_time"] = "2026-10-26T04:00:00Z"
        with pytest.raises(NotionConflict, match="수정 시각"):
            await service.upsert("project", "p1", 2, PROJECT_FIELDS, first)
    assert memory.patches == 0


async def test_notion_lost_create_response_reconciles_without_duplicate():
    memory = NotionMemory()
    memory.lose_create_response = True
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        with pytest.raises(NotionAmbiguousCreate):
            await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        recovered = await service.upsert("project", "p1", 1, PROJECT_FIELDS, {"ambiguous_create": True})
    assert recovered["page_id"] == PAGE_ID
    assert memory.creates == 1


async def test_notion_ambiguous_create_absence_never_blindly_reposts():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        with pytest.raises(NotionAmbiguousCreate, match="자동 생성을 보류"):
            await service.upsert("project", "p1", 1, PROJECT_FIELDS, {"ambiguous_create": True})
    assert memory.creates == 0


async def test_notion_lost_update_result_reconciles_after_restart():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        first = await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        await service.upsert("project", "p1", 2, PROJECT_FIELDS | {"status": "Active"}, first)
        recovered = await service.upsert("project", "p1", 2, PROJECT_FIELDS | {"status": "Active"}, first)
    assert recovered["revision"] == 2
    assert memory.patches == 1


async def test_notion_duplicate_external_id_stops_sync():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        first = await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        memory.pages.append(copy.deepcopy(memory.pages[0]))
        with pytest.raises(NotionConflict, match="여러 개"):
            await service.upsert("project", "p1", 2, PROJECT_FIELDS, first)
    assert memory.patches == 0


async def test_notion_existing_different_content_without_baseline_is_not_adopted():
    memory = NotionMemory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        service = NotionService("test-token", {"project": SOURCE_ID}, client=client)
        await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        with pytest.raises(NotionConflict, match="기준이 없어"):
            await service.upsert("project", "p1", 2, PROJECT_FIELDS | {"status": "Active"})
    assert memory.patches == 0


async def test_notion_schema_validation_reports_missing_and_wrong_types_without_writes():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"properties": {"Name": {"type": "rich_text"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NotionService("token", {"task": SOURCE_ID}, client=client).validate_schema()
    assert result["task"]["valid"] is False
    assert "External ID" in result["task"]["missing"]
    assert result["task"]["mismatched"]["Name"]["expected"] == "title"


async def test_notion_long_rich_text_is_preserved_in_chunks():
    memory = NotionMemory()
    fields = PROJECT_FIELDS | {"description": "가" * 5000}
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as client:
        await NotionService("token", {"project": SOURCE_ID}, client=client).upsert("project", "p1", 1, fields)
    chunks = memory.pages[0]["properties"]["Description"]["rich_text"]
    assert len(chunks) == 3
    assert "".join(chunk["text"]["content"] for chunk in chunks) == fields["description"]


async def test_notion_create_503_is_ambiguous_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        return httpx.Response(503, json={"message": "secret body"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NotionAmbiguousCreate, match="HTTP 503"):
            await NotionService("token", {"project": SOURCE_ID}, client=client).upsert("project", "p1", 1, PROJECT_FIELDS)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
async def test_notion_rejected_create_is_known_no_write(status):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NotionNoWriteError, match=f"HTTP {status}"):
            await NotionService("token", {"project": SOURCE_ID}, client=client).upsert("project", "p1", 1, PROJECT_FIELDS)
    assert len(calls) == 2


async def test_notion_query_failure_can_retry_create_but_cannot_clear_old_uncertainty():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path.endswith("/query")
        raise httpx.ReadTimeout("query response lost", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotionService("token", {"project": SOURCE_ID}, client=client, max_retries=0)
        with pytest.raises(NotionNoWriteError):
            await service.upsert("project", "p1", 1, PROJECT_FIELDS)
        with pytest.raises(NotionAmbiguousCreate):
            await service.upsert("project", "p1", 1, PROJECT_FIELDS, {"ambiguous_create": True})
    assert len(calls) == 2


async def test_notion_invalid_projection_fails_before_network_without_create_uncertainty():
    def handler(request):
        raise AssertionError("oversized local projection should not reach HTTP")

    fields = PROJECT_FIELDS | {"description": "x" * 200_000}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = NotionService("token", {"project": SOURCE_ID}, client=client)
        with pytest.raises(NotionNoWriteError):
            await service.upsert("project", "p1", 1, fields)
        with pytest.raises(NotionAmbiguousCreate):
            await service.upsert("project", "p1", 1, fields, {"ambiguous_create": True})


async def test_notion_successful_create_with_bad_metadata_remains_ambiguous():
    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        return httpx.Response(200, json={"object": "page", "id": PAGE_ID})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NotionAmbiguousCreate):
            await NotionService("token", {"project": SOURCE_ID}, client=client).upsert("project", "p1", 1, PROJECT_FIELDS)


async def test_notion_redirect_never_exposes_token_to_other_host():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(NotionError, match="HTTP 302"):
            await NotionService("secret", {"project": SOURCE_ID}, client=client).validate_schema()
    assert len(calls) == 1


async def test_notion_query_retries_bounded_and_respects_long_retry_after(monkeypatch):
    calls = 0

    async def no_sleep(delay):
        pass

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    monkeypatch.setattr("ai_virtual_team.integrations.notion.asyncio.sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NotionError, match="HTTP 503"):
            await NotionService("token", {"project": SOURCE_ID}, client=client, max_retries=1).validate_schema()
    assert calls == 2

    def rate_limited(request):
        return httpx.Response(429, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(rate_limited)) as client:
        with pytest.raises(NotionError, match="다음 실행으로 보류"):
            await NotionService("token", {"project": SOURCE_ID}, client=client).validate_schema()


@pytest.mark.parametrize("value", ["owner/repo", "https://github.com/owner/repo", "https://github.com/owner/repo.git/"])
def test_github_repository_normalization(value):
    assert parse_repository(value) == "owner/repo"


@pytest.mark.parametrize(
    "value",
    ["https://evil.example/o/r", "https://github.com@evil.example/o/r", "http://github.com/o/r",
     "https://github.com/o/r?token=a", "o/../r", "o/r/extra", "https://github.com:443/o/r", "o/.."],
)
def test_github_rejects_non_repository_urls(value):
    with pytest.raises(ValueError):
        parse_repository(value)


async def test_github_snapshot_uses_only_get_and_reports_bounded_data():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        path = request.url.path
        if path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        if "/git/trees/" in path:
            return httpx.Response(200, json={"tree": [{"path": "src/a.py", "type": "blob", "sha": "abc"}], "truncated": True})
        if path.endswith("/commits"):
            assert request.url.params["since"] == "2026-01-01T00:00:00+00:00"
            return httpx.Response(200, json=[{"sha": "abc", "commit": {"message": "Actual commit", "committer": {"date": "2026-01-02T00:00:00Z"}}, "html_url": "https://github.com/o/r/commit/abc"}], headers={"Link": '<https://evil.example>; rel="next"'})
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[{"number": 1, "title": "old", "updated_at": "2025-12-01T00:00:00Z"}, {"number": 2, "title": "new", "updated_at": "2026-01-02T00:00:00Z"}])
        return httpx.Response(200, json=[{"number": 2, "pull_request": {}}, {"number": 3, "title": "issue", "state": "open"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = GitHubService(client=client, max_pages=1)
        result = await service.snapshot("o/r", "2026-01-01T00:00:00Z")
    assert len(requests) == 5
    assert result["commits"][0]["message"] == "Actual commit"
    assert [item["number"] for item in result["pull_requests"]] == [2]
    assert [item["number"] for item in result["issues"]] == [3]
    assert any("잘렸" in item for item in result["limitations"])
    assert any("누락" in item for item in result["limitations"])
    assert result["errors"] == {}


async def test_github_partial_failure_is_not_no_changes():
    def handler(request):
        if request.url.path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"tree": [], "truncated": False})
        if request.url.path.endswith("/commits"):
            return httpx.Response(403, json={"message": "do not disclose this body"})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GitHubService(client=client).snapshot("o/r")
    assert result["errors"]["commits"] == "GitHub GET 실패 (HTTP 403)"
    assert any("변경 사항 없음으로 해석하면 안" in item for item in result["limitations"])


async def test_github_metadata_failure_is_sanitized_and_redirect_not_followed():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(GitHubError, match="HTTP 302"):
            await GitHubService(token="secret", client=client).snapshot("o/r")
    assert len(requests) == 1


@pytest.mark.parametrize("url", ["https://evil.example/o/r/pull/1", "https://github.com/o/other/pull/1", "https://github.com/o/r/blob/main/test.py", "https://github.com/o/r/pull/1?x=1"])
async def test_github_evidence_rejects_wrong_scope_without_network(url):
    def handler(request):
        raise AssertionError("invalid evidence must not make a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await GitHubService(client=client).verify_evidence("o/r", url)


@pytest.mark.parametrize("kind,payload,status", [
    ("pull", {"merged": True, "merged_at": "2026-01-01T00:00:00Z", "title": "merge"}, "merged"),
    ("issues", {"state": "closed", "closed_at": "2026-01-01T00:00:00Z", "title": "done"}, "closed"),
])
async def test_github_verified_evidence_does_not_invent_test_execution(kind, payload, status):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GitHubService(client=client).verify_evidence("o/r", f"https://github.com/o/r/{kind}/1")
    assert result["status"] == status
    assert result["tested"] is False


@pytest.mark.parametrize("kind,payload", [
    ("pull", {"merged": False, "state": "closed", "merged_at": None}),
    ("issues", {"state": "open"}),
    ("issues", {"state": "closed", "closed_at": "2026-01-01T00:00:00Z", "pull_request": {}}),
])
async def test_github_unfinished_or_wrong_type_evidence_is_rejected(kind, payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ValueError):
            await GitHubService(client=client).verify_evidence("o/r", f"https://github.com/o/r/{kind}/1")


async def test_github_transport_retries_are_bounded(monkeypatch):
    calls = 0

    async def no_sleep(delay):
        pass

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("secret", request=request)

    monkeypatch.setattr("ai_virtual_team.integrations.github.asyncio.sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubError, match="時間|시간"):
            await GitHubService(client=client, max_attempts=2).snapshot("o/r")
    assert calls == 2


async def test_github_long_retry_after_is_not_ignored():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubError, match="조회를 보류"):
            await GitHubService(client=client).snapshot("o/r")
    assert len(requests) == 1


@pytest.mark.parametrize("path", [
    "", "/etc/passwd", "../secret", "src/../secret", "src/./file", "src//file",
    "https://evil.example/file", "C:\\secret", "src/%2e%2e/file", "file\x00name", "folder/",
])
async def test_github_read_file_rejects_unsafe_paths_before_network(path):
    def handler(request):
        raise AssertionError("invalid paths must not reach HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await GitHubService(client=client).read_file("o/r", path)


def file_server(content=b"print('hello')\n", *, mode="100644", reported_size=None, content_data=None):
    calls = []
    commit_sha, root_sha, blob_sha = "a" * 40, "b" * 40, "c" * 40

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        if request.url.path == "/repos/o/r/commits/main":
            return httpx.Response(200, json={"sha": commit_sha, "commit": {"tree": {"sha": root_sha}}})
        if request.url.path == f"/repos/o/r/git/trees/{root_sha}":
            return httpx.Response(200, json={"tree": [{
                "path": "app.py", "type": "blob" if mode != "040000" else "tree", "mode": mode,
                "sha": blob_sha, "size": len(content) if reported_size is None else reported_size,
            }]})
        assert request.url.path == "/repos/o/r/contents/app.py"
        assert request.url.params["ref"] == commit_sha
        return httpx.Response(200, json=content_data or {
            "type": "file", "encoding": "base64", "path": "app.py", "sha": blob_sha,
            "size": len(content) if reported_size is None else reported_size,
            "content": base64.b64encode(content).decode("ascii"),
            "download_url": "https://evil.example/not-followed",
        })

    return handler, calls


async def test_github_read_file_pins_commit_and_decodes_text_without_following_download_url():
    handler, calls = file_server("print('안녕하세요')\n".encode())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GitHubService(client=client).read_file("o/r", "app.py", "main")
    assert result["text"] == "print('안녕하세요')\n"
    assert result["ref"] == "a" * 40
    assert result["untrusted"] is True
    assert len(calls) == 3


@pytest.mark.parametrize("mode", ["120000", "160000", "040000"])
async def test_github_read_file_rejects_symlink_submodule_directory_from_tree(mode):
    handler, calls = file_server(mode=mode)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="일반 파일"):
            await GitHubService(client=client).read_file("o/r", "app.py", "main")
    assert len(calls) == 2


async def test_github_read_file_checks_nested_tree_entries():
    def handler(request):
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "a" * 40, "commit": {"tree": {"sha": "b" * 40}}})
        if request.url.path.endswith("b" * 40):
            return httpx.Response(200, json={"tree": [{"path": "src", "mode": "040000", "type": "tree", "sha": "d" * 40}]})
        if request.url.path.endswith("d" * 40):
            return httpx.Response(200, json={"tree": [{"path": "app.py", "mode": "100755", "type": "blob", "sha": "c" * 40, "size": 1}]})
        assert request.url.path.endswith("/contents/src/app.py")
        return httpx.Response(200, json={"type": "file", "encoding": "base64", "path": "src/app.py", "sha": "c" * 40, "size": 1, "content": "eA=="})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GitHubService(client=client).read_file("o/r", "src/app.py")
    assert result["text"] == "x"


async def test_github_read_file_rejects_oversized_before_content_download():
    handler, calls = file_server(reported_size=65537)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="64 KiB"):
            await GitHubService(client=client).read_file("o/r", "app.py", "main")
    assert len(calls) == 2


@pytest.mark.parametrize("content,reported_size", [(b"a" * 65537, 2), (b"\xff\xfe", 2), (b"\x00x", 2)])
async def test_github_read_file_rejects_false_size_or_binary(content, reported_size):
    handler, _ = file_server(content, reported_size=reported_size)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubError, match="UTF-8"):
            await GitHubService(client=client).read_file("o/r", "app.py", "main")


async def test_github_read_file_accepts_exact_64kib_boundary():
    handler, _ = file_server(b"x" * 65536)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GitHubService(client=client).read_file("o/r", "app.py", "main")
    assert result["size"] == len(result["text"]) == 65536


@pytest.mark.parametrize("sha", ["main", "abcdef", "a" * 41, "g" * 40, "../main", "https://evil.example"])
async def test_github_commit_details_rejects_non_sha_without_network(sha):
    def handler(request):
        raise AssertionError("invalid SHA must not reach HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await GitHubService(client=client).commit_details("o/r", sha)


async def test_github_commit_details_bounds_files_and_patch_budget():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.params["per_page"] == "50"
        return httpx.Response(200, json={
            "sha": "a" * 40, "commit": {"message": "actual commit"},
            "files": [{"filename": f"file{i}.py", "status": "modified", "patch": "가" * 20000} for i in range(60)],
        }, headers={"Link": '<https://evil.example/not-followed>; rel="next"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GitHubService(client=client).commit_details("o/r", "a" * 7)
    assert len(calls) == 1
    assert result["files_truncated"] is True
    assert len(result["files"]) == 50
    assert sum(len(item["patch"].encode()) for item in result["files"]) <= 65536
    assert all(len(item["patch"].encode()) <= 8192 and item["patch_truncated"] for item in result["files"])
    assert result["url"] == f"https://github.com/o/r/commit/{'a' * 40}"


async def test_github_commit_details_reports_missing_patch_honestly():
    def handler(request):
        return httpx.Response(200, json={
            "sha": "a" * 40, "commit": {"message": "image"},
            "files": [{"filename": "image.png", "status": "added"}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GitHubService(client=client).commit_details("o/r", "a" * 40)
    assert result["files"][0]["patch"] is None
    assert result["files"][0]["patch_missing"] is True
    assert result["files_truncated"] is False
