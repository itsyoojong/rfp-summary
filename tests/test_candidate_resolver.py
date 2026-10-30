import unittest

from src.candidate_resolver import (
    period_signature,
    select_project_name,
)


class TestCandidateResolver(unittest.TestCase):
    """후보 통합 알고리즘의 주요 판단 규칙을 검증한다."""

    def test_same_period(self):
        """표현이 달라도 동일한 사업기간이면 같은 값으로 변환된다."""

        a = "계약일로부터 2026년 4월 30일(목)까지 (약 4개월)"
        b = "계약일로부터 2026년 4월 30일까지"

        self.assertEqual(
            period_signature(a),
            period_signature(b)
        )

    def test_different_period(self):
        """종료일이 다른 사업기간은 동일하게 판단하지 않는다."""

        a = "계약일로부터 2026년 4월 30일까지"
        b = "계약일로부터 2026년 5월 31일까지"

        self.assertNotEqual(
            period_signature(a),
            period_signature(b)
        )

    def test_select_official_name(self):
        """사업 개요와 입찰 서식에서 확인된 명칭을 선택한다."""

        official = (
            "차세대 개방형 학습관리시스템(LMS) 및 "
            "학습콘텐츠관리시스템(CMS) 고도화 사업"
        )

        candidates = [
            {"value": "차세대 개방형 LMS 고도화 사업"},
            {"value": "개방형 LMS 고도화 사업"},
            {"value": official}
        ]

        pages = [
            {
                "page_number": 1,
                "text": "사 업 명 차세대 개방형 LMS 고도화 사업"
            },
            {
                "page_number": 2,
                "text": f"I. 사업 개요\n1. 사업명\n◯ {official}"
            },
            {
                "page_number": 40,
                "text": f"서약서\n건 명 {official}"
            }
        ]

        result = select_project_name(candidates, pages)

        self.assertEqual(result, official)

    def test_missing_contract_evidence(self):
        """입찰 서식의 교차 근거가 없다면 선택을 보류한다."""

        name = "차세대 개방형 LMS 고도화 사업"

        candidates = [{"value": name}]

        pages = [
            {
                "page_number": 2,
                "text": f"1. 사업명\n{name}"
            }
        ]

        result = select_project_name(candidates, pages)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()