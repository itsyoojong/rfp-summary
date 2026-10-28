import re
from datetime import date
from src.source_validator import normalize_text

def period_signature(value: str):
    """사업기간의 시작 기준과 종료일을 비교 가능한 형태로 변환한다."""

    text = re.sub(r"\s+", "", value)

    # 현재는 '계약일로부터' 시작하는 기간만 처리
    if "계약일로부터" not in text:
        return None

    matches = re.findall(
        r"(\d{4})년(\d{1,2})월(\d{1,2})일",
        text
    )

    # 날짜가 없거나 여러 개면 자동 판단하지 않는다
    if len(matches) != 1:
        return None

    year, month, day = map(int, matches[0])

    try:
        end_date = date(year, month, day)
    except ValueError:
        return None

    return ("계약일로부터", end_date.isoformat())

def resolve_candidates(merged: dict, pages: list[dict]) -> dict:
    """통합된 후보의 원문 근거를 확인하고 선택 상태를 결정한다."""

    # PDF 페이지 번호와 본문 연결
    page_map = {
        page["page_number"]: normalize_text(page["text"])
        for page in pages
    }

    resolved = {}

    for field, entry in merged.items():
        candidates = []

        for candidate in entry["candidates"]:
            valid_pages = []
            issues = []

            for source in candidate["sources"]:
                page = source["page"]
                quote = normalize_text(source["quote"])

                # 인용문이 지정된 페이지에 실제로 존재하는지 검사
                if quote and quote in page_map.get(page, ""):
                    valid_pages.append(page)
                else:
                    # 다른 페이지에 존재하는지도 확인
                    actual_pages = [
                        number
                        for number, text in page_map.items()
                        if quote and quote in text
                    ]

                    issues.append({
                        "claimed_page": page,
                        "actual_pages": actual_pages
                    })

            candidates.append({
                "value": candidate["value"],
                "valid_pages": sorted(set(valid_pages)),
                "issues": issues
            })

        # 후보가 없을 때
        if not candidates:
            status = "미확인"
            selected = None

        # 후보가 하나이고 근거도 확인된 경우
        elif len(candidates) == 1 and candidates[0]["valid_pages"]:
            status = "단일 후보 · 근거 확인"
            selected = candidates[0]["value"]

                
        elif field == "사업기간":

            signatures = [
                period_signature(candidate["value"])
                for candidate in candidates
            ]

            verified = [
                candidate
                for candidate in candidates
                if candidate["valid_pages"]
            ]

            # 명시된 개월 수가 서로 충돌하는지도 검사
            durations = set()

            for candidate in candidates:
                match = re.search(
                    r"(\d+)\s*개월",
                    candidate["value"]
                )

                if match:
                    durations.add(int(match.group(1)))

            # 날짜가 같고, 기간 설명이 모순되지 않으며,
            # 최소 하나의 원문 근거가 정확할 때만 선택
            if (
                all(signature is not None for signature in signatures)
                and len(set(signatures)) == 1
                and len(durations) <= 1
                and verified
            ):
                status = "기간 표현 통합 · 근거 확인"
                selected = verified[0]["value"]

            else:
                status = "검토 필요"
                selected = None

        # 후보가 여러 개거나 근거가 부족한 경우
        else:
            status = "검토 필요"
            selected = None

        resolved[field] = {
            "status": status,
            "selected": selected,
            "candidates": candidates
        }

    return resolved