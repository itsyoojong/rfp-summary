import re


def normalize_text(text: str) -> str:
    # 비교할 때 공백 차이 무시
    return re.sub(r"\s+", "", text)


def validate_sources(merged_result: dict, pages: list[dict]) -> dict:

    # 페이지 번호와 원문 연결
    page_map = {
        page["page_number"]: normalize_text(page["text"])
        for page in pages
    }

    checked = 0
    matched = 0
    issues = []

    # 통합된 항목들을 순회
    for field, entry in merged_result.items():

        for candidate in entry["candidates"]:

            for source in candidate["sources"]:

                checked += 1

                page_number = source["page"]
                quote = source["quote"]

                # 페이지 번호 검증
                if page_number not in page_map:
                    issues.append({
                        "field": field,
                        "page": page_number,
                        "reason": "존재하지 않는 페이지"
                    })
                    continue

                # 인용문 검증
                # if normalize_text(quote) not in page_map[page_number]:
                #     issues.append({
                #         "field": field,
                #         "page": page_number,
                #         "reason": "원문에서 인용문을 찾지 못함"
                #     })
                #     continue
                if normalize_text(quote) not in page_map[page_number]:
                    
                    # 다른 PDF 페이지에 같은 인용문이 있는지 탐색
                    actual_pages = [
                        number
                        for number, text in page_map.items()
                        if normalize_text(quote) in text
                    ]
    
                    # 다른 페이지에서 발견했을 경우
                    if actual_pages:
                        reason = "인용문이 다른 페이지에서 발견됨"
    
                    # 문서 전체에서도 찾지 못한 경우
                    else:
                        reason = "문서 전체에서 인용문을 찾지 못함"
    
                    issues.append({
                        "field": field,
                        "page": page_number,
                        "reason": reason,
                        "actual_pages": actual_pages
                    })
    
                    continue
                
                matched += 1

    return {
        "checked": checked,
        "matched": matched,
        "issues": issues
    }