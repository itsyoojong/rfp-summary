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

def select_project_name(candidates: list, pages: list) -> str | None:
    """
    복수 사업명 후보 중 원문 근거가 교차 확인되는 명칭을 선택한다.

    설계 배경:
    - 표지, 사업 개요, 입찰 서식에서 사업명이 다르게 표현될 수 있다.
    - 가장 긴 문자열이나 처음 발견된 문자열만 선택하면
      공식 사업명이 아닌 축약 명칭을 선택할 위험이 있다.

    선택 기준:
    - '1. 사업명' 항목에서 확인된다.
    - 별도 페이지의 '건명' 또는 '용역명'에서도 확인된다.
    - 두 조건을 만족하는 후보가 정확히 하나일 때만 선택한다.
    """

    supported = []

    for candidate in candidates:
        name = normalize_text(candidate["value"])

        overview_pages = set()
        contract_pages = set()

        for page in pages:
            page_number = page["page_number"]
            lines = page["text"].splitlines()

            for index, line in enumerate(lines):

                current = normalize_text(line)

                next_line = (
                    normalize_text(lines[index + 1])
                    if index + 1 < len(lines)
                    else ""
                )

                # 사업 개요의 공식 사업명 확인
                # PDF에 따라 항목명과 내용이 서로 다른 줄에 있을 수 있다.
                if re.search(r"(?<!\d)1\.사업명", current):

                    if name in current or name in next_line:
                        overview_pages.add(page_number)

                # 계약·입찰 서식의 명칭 확인
                if "건명" in current or "용역명" in current:

                    if name in current:
                        contract_pages.add(page_number)

        # 서로 다른 페이지에서 교차 확인되어야 한다.
        # 같은 페이지의 반복 표기만으로는 확정하지 않는다.
        cross_verified = any(
            overview != contract
            for overview in overview_pages
            for contract in contract_pages
        )

        if cross_verified:
            supported.append(candidate["value"])

    # 유일한 후보일 때만 대표값 선택
    if len(supported) == 1:
        return supported[0]

    return None

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
        
        elif field == "사업명":

            decision = select_project_name(candidates, pages)

            if decision is not None:
                status = "사업명 교차 검증 · 대표값 선택"
                selected = decision

            else:
                status = "검토 필요"
                selected = None
                
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