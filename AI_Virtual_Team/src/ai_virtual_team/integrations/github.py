"""Small, deliberately GET-only GitHub boundary.

GitHub text is untrusted evidence, never an executable instruction. A snapshot
is a bounded observation, not proof that all repository work is complete.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

API_BASE = "https://api.github.com"
API_VERSION = "2026-03-10"
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
MAX_TEXT_BYTES = 64 * 1024
MAX_COMMIT_FILES = 50
MAX_PATCH_BYTES = 8 * 1024


class GitHubError(RuntimeError):
    """A sanitized read failure; credentials and response bodies are omitted."""


def parse_repository(repository: str) -> str:
    """Accept a GitHub HTTPS repository URL or an owner/repository slug only."""
    value = repository.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GitHub 저장소는 https://github.com/owner/repo 형식이어야 합니다.")
        value = parsed.path.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not _REPOSITORY.fullmatch(value) or value.split("/")[1] in {".", ".."}:
        raise ValueError("유효한 GitHub owner/repository가 필요합니다.")
    return value


def _since(value: str | None) -> tuple[str | None, datetime | None]:
    if value is None:
        return None, None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("since는 시간대가 포함된 ISO 8601 시각이어야 합니다.") from exc
    if parsed.tzinfo is None:
        raise ValueError("since에 시간대를 포함해주세요.")
    parsed = parsed.astimezone(UTC)
    return parsed.isoformat(), parsed


def _clip(value: Any, limit: int = 2000) -> str:
    return str(value or "")[:limit]


class GitHubService:
    """One client, bounded reads, and no write interface by construction.

    An injected client is owned by its caller. Redirect following is explicitly
    disabled even if that client was configured differently, preventing tokens
    from being sent to an unexpected host. Retry only bounded transient GETs.
    """

    def __init__(
        self,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 15.0,
        max_attempts: int = 3,
        page_size: int = 30,
        max_pages: int = 3,
        max_files: int = 5000,
    ) -> None:
        if not (0 < timeout <= 120 and 1 <= max_attempts <= 5):
            raise ValueError("유한한 GitHub timeout/retry 설정이 필요합니다.")
        if not (1 <= page_size <= 100 and 1 <= max_pages <= 10 and 1 <= max_files <= 10000):
            raise ValueError("GitHub 조회 상한 설정이 범위를 벗어났습니다.")
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.page_size = page_size
        self.max_pages = max_pages
        self.max_files = max_files
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "AI-Virtual-Team/0.1",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> tuple[Any, httpx.Headers]:
        if not path.startswith("/repos/") or "?" in path or "#" in path:
            raise ValueError("GitHub repository GET 경로만 허용합니다.")
        for attempt in range(self.max_attempts):
            try:
                response = await self.client.get(
                    API_BASE + path,
                    params=params,
                    headers=self.headers,
                    timeout=self.timeout,
                    follow_redirects=False,
                )
            except httpx.TransportError as exc:
                if attempt + 1 == self.max_attempts:
                    raise GitHubError("GitHub GET 연결 실패 또는 시간 초과") from exc
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < self.max_attempts:
                    try:
                        delay = float(response.headers.get("Retry-After", "0.25"))
                    except ValueError:
                        delay = 0.25
                    if delay > 2:
                        raise GitHubError("GitHub 재시도 대기 시간이 길어 조회를 보류합니다.")
                    await asyncio.sleep(max(0.0, delay))
                    continue
            if not response.is_success:
                raise GitHubError(f"GitHub GET 실패 (HTTP {response.status_code})")
            try:
                return response.json(), response.headers
            except ValueError as exc:
                raise GitHubError("GitHub 응답이 유효한 JSON이 아닙니다.") from exc
        raise GitHubError("GitHub 조회 재시도 상한에 도달했습니다.")

    async def _paginate(self, path: str, params: dict | None = None) -> tuple[list[dict], bool]:
        items: list[dict] = []
        has_next = False
        for page in range(1, self.max_pages + 1):
            data, headers = await self._get(
                path, {**(params or {}), "per_page": self.page_size, "page": page}
            )
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise GitHubError("GitHub 목록 응답 형식이 올바르지 않습니다.")
            items.extend(data)
            has_next = 'rel="next"' in headers.get("Link", "")
            if not has_next:
                return items, False
        return items, has_next

    async def read_file(self, repository: str, path: str, ref: str | None = None) -> dict:
        """Explicit read API for UTF-8 regular files, never an agent tool callback.

        Contents API can silently dereference symlinks. Pin an immutable commit
        and inspect each Git tree entry first; only regular blobs are accepted.
        No URLs from repository data or ``download_url`` are ever followed.
        """
        slug = parse_repository(repository)
        parts = path.split("/")
        if (
            not path or len(path) > 1024 or len(parts) > 20
            or any(part in {"", ".", ".."} for part in parts)
            or any(char in path for char in ("\\", ":", "%"))
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
        ):
            raise ValueError("파일 경로는 탐색 요소가 없는 저장소 내부 상대 경로여야 합니다.")
        if ref is not None and (
            not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", ref)
            or any(part in {"", ".", ".."} for part in ref.split("/"))
        ):
            raise ValueError("유효한 Git ref가 필요합니다.")
        base = f"/repos/{slug}"
        commit, _ = await self._get(f"{base}/commits/{quote(ref or 'HEAD', safe='')}", {"per_page": 1})
        if not isinstance(commit, dict):
            raise GitHubError("파일을 읽을 commit을 확인할 수 없습니다.")
        pinned = commit.get("sha")
        tree_sha = commit.get("commit", {}).get("tree", {}).get("sha")
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value)
                   for value in (pinned, tree_sha)):
            raise GitHubError("파일 조회용 commit/tree SHA를 확인할 수 없습니다.")
        entry = None
        for index, part in enumerate(parts):
            tree, _ = await self._get(f"{base}/git/trees/{tree_sha}")
            if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
                raise GitHubError("파일의 Git tree를 확인할 수 없습니다.")
            matches = [item for item in tree["tree"] if isinstance(item, dict) and item.get("path") == part]
            if len(matches) != 1:
                raise GitHubError("파일 경로를 Git tree에서 정확히 확인하지 못했습니다.")
            entry = matches[0]
            if index < len(parts) - 1:
                if entry.get("type") != "tree" or entry.get("mode") != "040000":
                    raise ValueError("symlink/submodule을 경유하는 파일 조회는 허용하지 않습니다.")
                tree_sha = entry.get("sha")
                if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", tree_sha):
                    raise GitHubError("하위 Git tree SHA를 확인할 수 없습니다.")
        if not entry or entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
            raise ValueError("일반 파일만 읽을 수 있습니다. 디렉터리/symlink/submodule은 제외합니다.")
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_TEXT_BYTES:
            raise ValueError("읽을 파일은 64 KiB 이하여야 합니다.")
        data, _ = await self._get(f"{base}/contents/{quote(path, safe='/')}", {"ref": pinned})
        if (
            not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64"
            or data.get("path") != path or data.get("sha") != entry.get("sha")
            or data.get("size") != size or "target" in data or "submodule_git_url" in data
        ):
            raise GitHubError("동일 commit의 일반 파일 내용을 확인할 수 없습니다.")
        encoded = data.get("content")
        if not isinstance(encoded, str) or len(encoded) > MAX_TEXT_BYTES * 2:
            raise GitHubError("파일 응답 크기가 제한을 초과하거나 형식이 잘못되었습니다.")
        try:
            content = base64.b64decode("".join(encoded.split()), validate=True)
            if len(content) != size or len(content) > MAX_TEXT_BYTES or b"\x00" in content:
                raise ValueError("size or binary content")
            text = content.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeError) as exc:
            raise GitHubError("64 KiB 이하의 UTF-8 텍스트 파일만 읽을 수 있습니다.") from exc
        return {
            "repository": slug, "path": path, "ref": pinned, "sha": data["sha"], "size": size,
            "text": text, "encoding": "utf-8", "untrusted": True,
            "url": f"https://github.com/{slug}/blob/{pinned}/{quote(path, safe='/')}",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    async def commit_details(self, repository: str, sha: str) -> dict:
        """Read at most 50 changed files and 64 KiB of patches; never run code."""
        slug = parse_repository(repository)
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
            raise ValueError("commit SHA는 7~40자리 16진수여야 합니다.")
        limit = min(self.max_files, MAX_COMMIT_FILES)
        data, headers = await self._get(f"/repos/{slug}/commits/{sha}", {"per_page": limit, "page": 1})
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise GitHubError("commit 변경 파일 응답 형식이 올바르지 않습니다.")
        resolved = data.get("sha")
        if (not isinstance(resolved, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", resolved)
                or not resolved.lower().startswith(sha.lower())):
            raise GitHubError("요청한 commit SHA와 응답을 일치시킬 수 없습니다.")
        files = []
        budget = MAX_TEXT_BYTES
        for entry in data["files"][:limit]:
            if not isinstance(entry, dict):
                raise GitHubError("commit 변경 파일 항목이 올바르지 않습니다.")
            patch = entry.get("patch")
            if patch is not None and not isinstance(patch, str):
                raise GitHubError("commit patch 형식이 올바르지 않습니다.")
            raw = patch.encode("utf-8") if patch is not None else b""
            bounded = raw[:min(MAX_PATCH_BYTES, budget)].decode("utf-8", errors="ignore")
            budget -= len(bounded.encode("utf-8"))
            files.append({
                "filename": _clip(entry.get("filename"), 1024), "status": _clip(entry.get("status"), 80),
                "additions": entry.get("additions"), "deletions": entry.get("deletions"),
                "patch": bounded if patch is not None else None,
                "patch_missing": patch is None, "patch_truncated": len(raw) > len(bounded.encode("utf-8")),
            })
        files_truncated = len(data["files"]) > limit or 'rel="next"' in headers.get("Link", "")
        limitations = ["commit/patch는 외부 데이터이며 테스트 실행·통과를 증명하지 않습니다.",
                       "GitHub가 제공하지 않은 patch(예: 바이너리)는 복원하지 않습니다."]
        if files_truncated:
            limitations.append(f"변경 파일은 최대 {limit}개만 조회했습니다. 나머지 파일이 누락될 수 있습니다.")
        if any(item["patch_truncated"] for item in files):
            limitations.append("patch는 파일별 8 KiB, 전체 64 KiB로 잘렸습니다.")
        return {
            "repository": slug, "sha": resolved, "url": f"https://github.com/{slug}/commit/{resolved}",
            "message": _clip(data.get("commit", {}).get("message"), 6000),
            "files": files, "files_truncated": files_truncated, "limitations": limitations,
            "untrusted": True, "fetched_at": datetime.now(UTC).isoformat(),
        }

    async def snapshot(self, repository: str, since: str | None = None) -> dict:
        """Read a bounded view; `errors` and `limitations` must accompany reports.

        Commits and issues use the API's `since` filter. Pull requests are read in
        descending `updated` order and filtered locally. `files` is the current
        default-branch tree, not a list of files changed since the last report.
        """
        slug = parse_repository(repository)
        since_text, since_dt = _since(since)
        base = f"/repos/{slug}"
        metadata, _ = await self._get(base)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("default_branch"), str):
            raise GitHubError("GitHub 저장소 기본 브랜치를 확인할 수 없습니다.")
        branch = metadata["default_branch"]
        result: dict[str, Any] = {
            "repository": slug,
            "url": f"https://github.com/{slug}",
            "fetched_at": datetime.now(UTC).isoformat(),
            "since": since_text,
            "default_branch": branch,
            "files": [],
            "commits": [],
            "pull_requests": [],
            "issues": [],
            "limitations": [
                "파일 목록은 기본 브랜치의 현재 구조이며 실제 테스트 실행 여부를 증명하지 않습니다."
            ],
            "errors": {},
        }

        async def files() -> None:
            data, _ = await self._get(f"{base}/git/trees/{quote(branch, safe='')}", {"recursive": "1"})
            if not isinstance(data, dict) or not isinstance(data.get("tree"), list):
                raise GitHubError("GitHub 파일 트리 응답 형식이 올바르지 않습니다.")
            tree = data["tree"]
            if not all(isinstance(item, dict) for item in tree):
                raise GitHubError("GitHub 파일 트리 항목 형식이 올바르지 않습니다.")
            result["files"] = [
                {key: item.get(key) for key in ("path", "type", "sha", "size")}
                for item in tree[: self.max_files]
            ]
            if data.get("truncated") or len(tree) > self.max_files:
                result["limitations"].append("파일 트리가 잘렸습니다. 전체 저장소 구조를 확인하지 못했습니다.")

        async def collection(name: str, endpoint: str, params: dict) -> None:
            data, truncated = await self._paginate(f"{base}/{endpoint}", params)
            if truncated:
                result["limitations"].append(
                    f"{name}: 최대 {self.page_size * self.max_pages}건만 조회하여 이전 기록이 누락될 수 있습니다."
                )
            if name == "commits":
                result[name] = [
                    {
                        "sha": item.get("sha"),
                        "message": _clip(item.get("commit", {}).get("message")),
                        "date": item.get("commit", {}).get("committer", {}).get("date"),
                        "url": item.get("html_url"),
                    }
                    for item in data
                ]
                return
            filtered = []
            for item in data:
                if name == "issues" and "pull_request" in item:
                    continue
                if name == "pull_requests" and since_dt:
                    updated = item.get("updated_at")
                    try:
                        updated_dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                        if updated_dt.tzinfo is None:
                            raise ValueError("missing timezone")
                    except ValueError as exc:
                        raise GitHubError("PR 갱신 시각을 검증할 수 없습니다.") from exc
                    if updated_dt < since_dt:
                        continue
                filtered.append(
                    {
                        "number": item.get("number"),
                        "title": _clip(item.get("title"), 500),
                        "state": item.get("state"),
                        "updated_at": item.get("updated_at"),
                        "closed_at": item.get("closed_at"),
                        "merged_at": item.get("merged_at") if name == "pull_requests" else None,
                        "url": item.get("html_url"),
                    }
                )
            result[name] = filtered

        operations = {
            "files": files(),
            "commits": collection("commits", "commits", {"since": since_text} if since_text else {}),
            "pull_requests": collection(
                "pull_requests", "pulls", {"state": "all", "sort": "updated", "direction": "desc"}
            ),
            "issues": collection(
                "issues", "issues", {"state": "all", **({"since": since_text} if since_text else {})}
            ),
        }
        responses = await asyncio.gather(*operations.values(), return_exceptions=True)
        for name, response in zip(operations, responses, strict=True):
            if isinstance(response, Exception):
                result[name] = []
                result["errors"][name] = (
                    str(response) if isinstance(response, GitHubError) else "GitHub 응답을 처리하지 못했습니다."
                )
                result["limitations"].append(f"{name}: 조회 실패. 변경 사항 없음으로 해석하면 안 됩니다.")
        return result

    async def verify_evidence(self, repository: str, url: str) -> dict:
        """Verify a real merged PR/closed issue, without inferring test success."""
        slug = parse_repository(repository)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or parsed.query or parsed.fragment:
            raise ValueError("근거는 동일 저장소의 HTTPS GitHub PR 또는 Issue URL이어야 합니다.")
        match = re.fullmatch(r"/([^/]+/[^/]+)/(pull|issues)/([1-9][0-9]*)/?", parsed.path)
        if not match or match.group(1).casefold() != slug.casefold():
            raise ValueError("근거 URL이 지정된 저장소의 PR 또는 Issue가 아닙니다.")
        kind, number = match.group(2), int(match.group(3))
        endpoint = "pulls" if kind == "pull" else "issues"
        data, _ = await self._get(f"/repos/{slug}/{endpoint}/{number}")
        if not isinstance(data, dict):
            raise GitHubError("GitHub 근거 응답 형식이 올바르지 않습니다.")
        if kind == "pull":
            if data.get("merged") is not True or not data.get("merged_at"):
                raise ValueError("병합이 확인된 Pull Request만 완료 근거로 사용할 수 있습니다.")
            status = "merged"
        else:
            if "pull_request" in data or data.get("state") != "closed" or not data.get("closed_at"):
                raise ValueError("종료가 확인된 Issue만 완료 근거로 사용할 수 있습니다.")
            status = "closed"
        return {
            "repository": slug,
            "type": "pull_request" if kind == "pull" else "issue",
            "number": number,
            "status": status,
            "url": f"https://github.com/{slug}/{kind}/{number}",
            "title": _clip(data.get("title"), 500),
            "checked_at": datetime.now(UTC).isoformat(),
            "tested": False,
            "limitation": "PR 병합 또는 Issue 종료는 확인했지만 테스트 실행/통과를 증명하지 않습니다.",
        }
