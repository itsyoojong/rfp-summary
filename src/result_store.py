import json
import re
from pathlib import Path

# 저장 위치와 캐시 버전
CACHE_VERSION = "v1"

CACHE_DIR = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "cache"
)


def get_cache_path(document_id: str, chunk_size: int) -> Path:

    # 파일 식별값과 그룹 크기 확인
    if not re.fullmatch(r"[0-9a-f]{64}", document_id):
        raise ValueError("올바르지 않은 문서 ID입니다.")

    if chunk_size < 1:
        raise ValueError("그룹 크기는 1 이상이어야 합니다.")

    filename = (
        f"{document_id}_chunk{chunk_size}_"
        f"{CACHE_VERSION}.json"
    )

    return CACHE_DIR / filename


def save_group_results(
    document_id: str,
    chunk_size: int,
    group_results: dict
) -> None:

    # 저장 폴더 생성
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    path = get_cache_path(document_id, chunk_size)

    data = {
        "version": CACHE_VERSION,
        "document_id": document_id,
        "chunk_size": chunk_size,
        "group_results": group_results
    }

    # 임시 파일에 먼저 저장한 후 교체
    temp_path = path.with_suffix(".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp_path.replace(path)


def load_group_results(
    document_id: str,
    chunk_size: int
) -> dict:

    path = get_cache_path(document_id, chunk_size)

    # 저장된 파일이 없으면 빈 딕셔너리 반환
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    # 저장 데이터가 현재 분석 설정과 맞는지 확인
    if (
        not isinstance(data, dict)
        or data.get("version") != CACHE_VERSION
        or data.get("document_id") != document_id
        or data.get("chunk_size") != chunk_size
    ):
        raise ValueError("저장 파일의 설정이 일치하지 않습니다.")

    saved_results = data.get("group_results")

    if not isinstance(saved_results, dict):
        raise ValueError("저장된 그룹 결과가 올바르지 않습니다.")

    # JSON의 문자열 키를 정수 인덱스로 복원
    restored = {
        int(key): value
        for key, value in saved_results.items()
    }

    if sorted(restored) != list(range(len(restored))):
        raise ValueError("그룹 번호가 연속되지 않습니다.")

    return restored