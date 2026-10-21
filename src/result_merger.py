def merge_group_results(group_results: dict[int, dict]) -> dict:

    # 통합할 RFP 항목
    fields = (
        "사업명",
        "사업기간",
        "사업비",
        "사업목적",
        "제출기한"
    )

    # 통합 결과를 저장할 딕셔너리
    merged = {
        field: {
            "status": "미확인",
            "candidates": []
        }
        for field in fields
    }

    # 그룹을 번호순으로 순회
    for group_index, result in sorted(group_results.items()):

        if not isinstance(result, dict):
            continue

        for field in fields:

            entry = result.get(field)

            if not isinstance(entry, dict):
                continue

            value = entry.get("value")

            # 값이 없거나 문자열이 아니면 건너뛰기
            if not isinstance(value, str) or not value.strip():
                continue

            candidates = merged[field]["candidates"]

            # 비교를 위해 공백과 대소문자 차이를 정규화
            normalized = "".join(value.split()).casefold()

            # 이미 수집한 값인지 확인
            existing = next(
                (
                    candidate
                    for candidate in candidates
                    if "".join(
                        candidate["value"].split()
                    ).casefold() == normalized
                ),
                None
            )

            # 새로운 값이라면 후보 생성
            if existing is None:
                existing = {
                    "value": value.strip(),
                    "groups": [],
                    "sources": []
                }
                candidates.append(existing)

            existing["groups"].append(group_index + 1)

            # 근거 데이터 수집
            sources = entry.get("sources")

            if isinstance(sources, list):
                for source in sources:

                    if not isinstance(source, dict):
                        continue

                    page = source.get("page")
                    quote = source.get("quote")

                    if (
                        not isinstance(page, int)
                        or not isinstance(quote, str)
                        or not quote.strip()
                    ):
                        continue

                    clean_source = {
                        "page": page,
                        "quote": quote
                    }

                    if clean_source not in existing["sources"]:
                        existing["sources"].append(clean_source)

    # 항목마다 통합 상태 표시
    for entry in merged.values():

        count = len(entry["candidates"])

        if count == 0:
            entry["status"] = "미확인"
        elif count == 1:
            entry["status"] = "단일 후보"
        else:
            entry["status"] = "복수 후보 · 검토 필요"

    return merged