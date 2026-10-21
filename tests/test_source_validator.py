from copy import deepcopy
from src.source_validator import validate_sources

# 테스트용 PDF 원문
pages = [
    {
        "page_number": 3,
        "text": "사업비: 6천만원 범위 내 (VAT 포함)"
    }
]

# 테스트용 AI 분석 결과
merged = {
    "사업비": {
        "status": "단일 후보",
        "candidates": [
            {
                "value": "6천만원 범위 내 (VAT 포함)",
                "groups": [1],
                "sources": [
                    {
                        "page": 3,
                        "quote": "사업비: 6천만원 범위 내 (VAT 포함)"
                    }
                ]
            }
        ]
    }
}

# 테스트 1: 정상 인용문
normal = validate_sources(merged, pages)

assert normal["matched"] == 1
print("테스트 1 통과: 정상 인용문")


# 테스트 2: 존재하지 않는 금액으로 변경
wrong_quote = deepcopy(merged)

wrong_quote["사업비"]["candidates"][0]["sources"][0]["quote"] = (
    "사업비: 60억원"
)

result = validate_sources(wrong_quote, pages)

assert result["matched"] == 0
assert len(result["issues"]) == 1

print("테스트 2 통과: 잘못된 인용문 탐지")


# 테스트 3: 존재하지 않는 페이지로 변경
wrong_page = deepcopy(merged)

wrong_page["사업비"]["candidates"][0]["sources"][0]["page"] = 99

result = validate_sources(wrong_page, pages)

assert result["matched"] == 0
assert len(result["issues"]) == 1

print("테스트 3 통과: 잘못된 페이지 탐지")

# 테스트 4: 근거는 정확하지만 추출값이 잘못된 경우

wrong_value = deepcopy(merged)

wrong_value["사업비"]["candidates"][0]["value"] = (
    "60억원 범위 내 (VAT 포함)"
)

result = validate_sources(wrong_value, pages)

print("검증 결과:", result)
print("잘못된 추출값:", wrong_value["사업비"]["candidates"][0]["value"])


from src.value_validator import (
    parse_krw_amount,
    validate_budget_amount
)

# 테스트 5: 금액 정규화
assert parse_krw_amount("6천만원") == 60_000_000
assert parse_krw_amount("60,000,000원") == 60_000_000
assert parse_krw_amount("60억원") == 6_000_000_000

print("테스트 5 통과: 금액 정규화")


# 테스트 6: 잘못된 추출 금액 탐지
warnings = validate_budget_amount(wrong_value)

assert len(warnings) == 1

print("테스트 6 통과:", warnings[0])


# 테스트 7: 올바른 추출 금액
warnings = validate_budget_amount(merged)

assert len(warnings) == 0

print("테스트 7 통과: 금액 일치")