import re

# 한국어 금액 단위
UNIT_MAP = {
    "억": 100_000_000,
    "천만": 10_000_000,
    "백만": 1_000_000,
    "십만": 100_000,
    "만": 10_000,
}

def parse_krw_amount(text: str) -> int | None:

    # 복합 금액은 현재 지원하지 않음
    if re.search(r"\d[\d,]*\s*억\s*\d", text):
        return None

    pattern = r"(\d[\d,]*)\s*(천만|백만|십만|억|만)?\s*원"

    matches = re.findall(pattern, text)

    # 금액이 없거나 여러 개라면 자동 판단하지 않음
    if len(matches) != 1:
        return None

    number, unit = matches[0]

    number = int(number.replace(",", ""))

    multiplier = UNIT_MAP.get(unit, 1)

    return number * multiplier


def validate_budget_amount(merged_result: dict) -> list[str]:

    warnings = []

    budget = merged_result.get("사업비", {})
    candidates = budget.get("candidates", [])

    for candidate in candidates:

        value = candidate["value"]
        value_amount = parse_krw_amount(value)

        for source in candidate["sources"]:

            quote = source["quote"]
            quote_amount = parse_krw_amount(quote)

            # 비교할 수 없는 금액은 수동 검토
            if value_amount is None or quote_amount is None:
                warnings.append(
                    "금액을 자동으로 비교할 수 없습니다."
                )
                continue

            # 숫자로 변환한 금액 비교
            if value_amount != quote_amount:
                warnings.append(
                    f"사업비 금액 불일치: "
                    f"추출값 {value_amount:,}원 / "
                    f"근거 {quote_amount:,}원"
                )

    return warnings