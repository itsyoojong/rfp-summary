from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.result_store import (
    save_group_results,
    load_group_results
)

# 테스트용 문서 ID와 분석 결과
document_id = "a" * 64

sample_results = {
    0: {
        "사업명": {
            "value": "K-sure 홈페이지 개편",
            "sources": []
        }
    }
}

# 실제 프로젝트 파일을 건드리지 않도록 임시 폴더 사용
with TemporaryDirectory() as temp_dir:

    with patch(
        "src.result_store.CACHE_DIR",
        Path(temp_dir)
    ):

        # 저장
        save_group_results(
            document_id,
            3,
            sample_results
        )

        # 다시 불러오기
        loaded = load_group_results(
            document_id,
            3
        )

        assert loaded == sample_results
        assert 0 in loaded

        print("테스트 통과: JSON 저장 및 복원")