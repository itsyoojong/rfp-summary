"""Streamlit 세션의 문서 전환과 선택적 로컬 캐시 연결."""
import os
from collections.abc import MutableMapping

from src.result_store import load_group_results, save_group_results


def disk_cache_enabled() -> bool:
    return os.getenv("RFP_ENABLE_DISK_CACHE", "false").strip().lower() == "true"


def set_document(state: MutableMapping, document_id: str | None, chunk_size: int) -> None:
    state.setdefault("page_results", {})
    state.setdefault("group_results", {})
    if "document_id" in state and state["document_id"] == document_id:
        return

    # 복원 실패 때도 이전 문서의 결과가 새 문서에 남지 않도록 먼저 초기화한다.
    state.update(document_id=document_id, page_results={}, group_results={})
    state.pop("cache_save_failed", None)
    if document_id is not None and disk_cache_enabled():
        state["group_results"] = load_group_results(document_id, chunk_size)


def persist_groups(document_id: str, chunk_size: int, group_results: dict) -> None:
    if disk_cache_enabled():
        save_group_results(document_id, chunk_size, group_results)
