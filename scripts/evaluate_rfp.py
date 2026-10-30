import argparse
import hashlib
import json
import re
from pathlib import Path


from src.pdf_loader import extract_pdf_pages
from src.result_merger import merge_group_results
from src.source_validator import validate_sources
from src.value_validator import parse_krw_amount
from src.candidate_resolver import resolve_candidates


ROOT = Path(__file__).resolve().parent.parent


def normalize(text):
    return re.sub(r"\s+", "", text).casefold()


def check_budget(value, gold):
    """사업비의 금액과 확인된 계약 조건을 비교합니다."""

    amount = parse_krw_amount(value)

    # 금액을 해석할 수 없다면 자동 평가 보류
    if amount is None or gold.get("amount_krw") is None:
        return None

    correct = amount == gold["amount_krw"]

    # 상한 조건 비교
    if gold.get("upper_bound") is not None:
        has_upper = bool(
        re.search(r"범위\s*내|이하|이내", value)
        )

        if gold["upper_bound"]:
            correct = correct and has_upper
        elif has_upper:
            correct = False

    # VAT 조건 비교
    if gold.get("vat_included") is not None:
        included = bool(
            re.search(
                r"(?:V\.?\s*A\.?\s*T\.?|부가(?:가치)?세)\s*포함",
                value,
                re.IGNORECASE
            )
        )


        excluded = bool(
            re.search(
                r"(?:V\.?\s*A\.?\s*T\.?|부가(?:가치)?세)\s*(?:별도|제외|미포함)",
                value,
                re.IGNORECASE
            )
        )

        if gold["vat_included"]:
            correct = correct and included and not excluded
        else:
            # VAT 관련 표현이 없으면 판단 보류
            if not included and not excluded:
                return None
            correct = correct and excluded and not included

    return correct

def check_candidate(field, value, answer):

    if field == "사업비":
        return check_budget(value, answer)

    return normalize(value) == normalize(answer["value"])

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--pdf", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--baseline", required=True)

    args = parser.parse_args()

    pdf_path = ROOT / args.pdf
    gold_path = ROOT / args.gold
    baseline_path = ROOT / args.baseline

    # 1. PDF 읽기
    pdf_bytes = pdf_path.read_bytes()
    document_id = hashlib.sha256(pdf_bytes).hexdigest()

    with pdf_path.open("rb") as file:
        pages = extract_pdf_pages(file)

    # 2. 저장한 베이스라인 불러오기
    baseline = json.loads(
        baseline_path.read_text(encoding="utf-8")
    )

    if baseline["document_id"] != document_id:
        raise ValueError("PDF와 베이스라인의 문서 ID가 다릅니다.")

    chunk_size = baseline["chunk_size"]

    group_results = {
        int(key): value
        for key, value in baseline["group_results"].items()
    }

    expected_groups = (
        len(pages) + chunk_size - 1
    ) // chunk_size

    if set(group_results) != set(range(expected_groups)):
        raise ValueError("분석 그룹이 누락되었거나 번호가 잘못됐습니다.")

    merged = merge_group_results(group_results)
    # 기존 후보 통합 결과에 새 후보 선택 알고리즘 적용
    # 베이스라인은 변경하지 않고 개선 후 결과를 별도로 평가한다.
    resolved = resolve_candidates(merged, pages)

    # 3. 사람이 작성한 정답 읽기
    gold = json.loads(
        gold_path.read_text(encoding="utf-8")
    )

    strict_matches = 0
    strict_total = 0
    manual_matches = 0
    manual_total = 0
    field_results = {}
    candidate_matched = 0
    candidate_total = 0

    # 4. 항목별 평가
    for field, answer in gold.items():

        entry = merged.get(field, {})
        candidates = entry.get("candidates", [])
        values = [c["value"] for c in candidates]
        candidate_match = None

        if answer.get("reviewed", False) and field != "사업목적":
        
            if answer.get("value") is None:
                candidate_match = len(values) == 0

            else:
                checks = [
                    check_candidate(field, value, answer)
                    for value in values
                ]

                if any(result is True for result in checks):
                    candidate_match = True

                elif any(result is None for result in checks):
                    candidate_match = None

                else:
                    candidate_match = False

            if candidate_match is not None:
                candidate_total += 1
                candidate_matched += int(candidate_match)
        score = None

        if not answer.get("reviewed", False):
            status = "정답 미검토"

        elif field == "사업목적":
            manual = answer.get("manual_evaluation", {})
            score = manual.get("correct")

            if isinstance(score, bool):
                manual_total += 1
                manual_matches += int(score)
                status = "수동 평가 일치" if score else "수동 평가 불일치"
            else:
                score = None
                status = "수동 의미 평가 필요"

        else:
            if not values:
                score = answer.get("value") is None
            elif len(values) > 1:
                # 단일 답변을 확정하지 못한 경우
                score = False
            elif answer.get("value") is None:
                score = False
            elif field == "사업비":
                score = check_budget(values[0], answer)
            else:
                score = (
                    normalize(values[0]) ==
                    normalize(answer["value"])
                )

            if score is None:
                status = "자동 평가 보류"
            else:
                strict_total += 1
                strict_matches += int(score)
                status = "일치" if score else "불일치 또는 복수 후보"

        # field_results[field] = {
        #     "status": status,
        #     "predictions": values
        # }

        # print(f"{field}: {status}")
        # print(f"  AI 추출값: {values}")
        
        # 기존 평가 결과와 개선된 후보 선택 결과를 모두 기록한다.
        decision = resolved[field]

        field_results[field] = {
            "status": status,
            "predictions": values,
            "resolved_status": decision["status"],
            "selected": decision["selected"]
        }

        # 최종 선택 결과를 우선 표시한다.
        if field in ("사업명", "사업기간", "사업비"):
        
            print(f"{field}: {decision['status']}")
            print(f"  최종 선택값: {decision['selected']}")

            # 여러 후보가 있었던 경우에만 검증용 정보 표시
            if len(values) > 1:
                print(f"  통합 전 후보 수: {len(values)}")

        else:
            # 사업목적의 수동 평가와 제출기한의 미확인 상태는 별도 표시
            print(f"{field}: {status}")
            print(f"  추출값: {values}")

        # 4-1. 후보 선택 알고리즘 적용 후 정확도 평가
    resolved_matched = 0
    resolved_total = 0

    print("\n--- 후보 선택 알고리즘 평가 ---")

    for field, answer in gold.items():

        # 검토하지 않은 항목과 사업목적은 자동 평가에서 제외
        if not answer.get("reviewed") or field == "사업목적":
            continue

        selected = resolved[field]["selected"]
        expected = answer.get("value")

        # 원문에 정보가 없는 경우
        if expected is None:
            # 후보 자체가 없어야 올바른 미추출로 인정
            correct = len(merged[field]["candidates"]) == 0

        # 대표값을 선택하지 못한 경우
        elif selected is None:
            correct = False

        # 사업비는 금액·VAT·상한 조건으로 평가
        elif field == "사업비":
            correct = check_budget(selected, answer)

        # 나머지는 기존 문자열 정규화 방식으로 평가
        else:
            correct = normalize(selected) == normalize(expected)

        # 자동 판단 불가능한 경우 분모에서 제외
        if correct is None:
            print(f"{field}: 평가 보류")
            continue

        resolved_total += 1
        resolved_matched += int(correct)

        result = "PASS" if correct else "FAIL"

        print(f"{field}: {result}")
        print(f"  선택값: {selected}")

    print(
        f"\n후보 선택 후 자동 일치: "
        f"{resolved_matched}/{resolved_total}"
    )
    
    # 5. 원문 근거 검증
    sources = validate_sources(merged, pages)

    # print("\n--- 베이스라인 평가 결과 ---")
    # print(f"자동 평가: {strict_matches}/{strict_total}")
    # print(f"후보 탐지: {candidate_matched}/{candidate_total}")
    # print(f"수동 의미 평가: {manual_matches}/{manual_total}")
    # print(
    #     f"근거 일치: {sources['matched']}/"
    #     f"{sources['checked']}"
    # )
    
    # print("\n--- 평가 결과 비교 ---")

    # 기존 후보 병합 결과
    print(f"기존 자동 평가: "f"{strict_matches}/{strict_total}")
    # 새 후보 선택 알고리즘 적용 결과
    print(f"개선 후 자동 평가: "f"{resolved_matched}/{resolved_total}")
    # 후보 탐지
    print(f"후보 탐지: "f"{candidate_matched}/{candidate_total}")
    # 사업목적 의미 평가
    print(f"수동 의미 평가: "f"{manual_matches}/{manual_total}")
    # 인용문 검증
    print(f"근거 일치: "f"{sources['matched']}/{sources['checked']}")

    # 6. 평가 보고서 저장
    report = {
        "pdf": pdf_path.name,
        "baseline": baseline_path.name,
        "automatic": {
            "matched": strict_matches,
            "total": strict_total
        },
        "manual": {
            "matched": manual_matches,
            "total": manual_total
        },
        "fields": field_results,
        "source_validation": sources,
        
        "candidate_detection": {
            "matched": candidate_matched,
            "total": candidate_total
        },
        "resolved_auto": {
        "matched": resolved_matched,
        "total": resolved_total
        }
    }

    output = (
        ROOT / "data" / "evaluation"
        / f"{pdf_path.stem}_baseline_v1_eval_v3_report.json"
    )
    

    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"\n평가 보고서 저장: {output}")


if __name__ == "__main__":
    main()