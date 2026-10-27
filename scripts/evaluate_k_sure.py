import hashlib
import json
import re
from pathlib import Path

from src.pdf_loader import extract_pdf_pages
from src.result_store import load_group_results
from src.result_merger import merge_group_results
from src.source_validator import validate_sources
from src.value_validator import parse_krw_amount


ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "data" / "sample.pdf"
GOLD_PATH = ROOT / "evaluation" / "k_sure_gold.json"


def normalize(value):
    return re.sub(r"\s+", "", value).casefold()


def check_budget(value, gold):
    amount = parse_krw_amount(value)

    upper_bound = bool(
        re.search(r"범위\s*내|이하", value)
    )

    vat_included = bool(
        re.search(r"VAT\s*포함", value, re.IGNORECASE)
    )

    return (
        amount == gold["amount_krw"]
        and upper_bound == gold["upper_bound"]
        and vat_included == gold["vat_included"]
    )


def main():
    # 1. 원본 PDF와 저장된 분석 결과 불러오기
    pdf_bytes = PDF_PATH.read_bytes()
    document_id = hashlib.sha256(pdf_bytes).hexdigest()

    with PDF_PATH.open("rb") as pdf_file:
        pages = extract_pdf_pages(pdf_file)

    group_results = load_group_results(document_id, 3)

    expected_groups = (len(pages) + 2) // 3

    if len(group_results) != expected_groups:
        raise ValueError(
            f"전체 분석 미완료: "
            f"{len(group_results)}/{expected_groups}그룹"
        )

    merged = merge_group_results(group_results)

    # 2. 사람이 확인한 기준 답안 읽기
    gold = json.loads(
        GOLD_PATH.read_text(encoding="utf-8")
    )

    field_results = {}
    strict_matched = 0

    # 3. 항목별 평가
    for field, answer in gold.items():

        candidates = merged[field]["candidates"]
        values = [c["value"] for c in candidates]

        if not answer["reviewed"]:
            status = "정답 미검토"

        elif not values:
            status = "정보 누락"

        elif len(values) > 1:
            status = "복수 후보 - 수동 검토 필요"

        elif field == "사업목적":
            status = "의미 평가 필요"

        elif field == "사업비":
            correct = check_budget(values[0], answer)
            status = "일치" if correct else "검토 필요"
            strict_matched += int(correct)

        else:
            correct = normalize(values[0]) == normalize(
                answer["value"]
            )
            status = "일치" if correct else "검토 필요"
            strict_matched += int(correct)

        field_results[field] = {
            "status": status,
            "predictions": values
        }

        print(f"{field}: {status}")
        print(f"  AI 추출값: {values}")

    # 4. 원문 인용문 검사
    source_result = validate_sources(merged, pages)

    print("\n--- 평가 요약 ---")
    strict_total = sum(
    1
    for field, answer in gold.items()
    if answer["reviewed"] and field != "사업목적"
)
    print(f"엄격 자동 일치: {strict_matched}/{strict_total}개 항목")
    print(
        f"근거 일치: {source_result['matched']}/"
        f"{source_result['checked']}개 인용문"
    )

    # 5. 평가 보고서 저장
    report = {
        "strict_auto_matches": strict_matched,

        "strict_auto_total": strict_total,
        "fields": field_results,
        "source_validation": source_result
    }

    output = ROOT / "data" / "evaluation" / "k_sure_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"\n평가 보고서 저장: {output}")


if __name__ == "__main__":
    main()