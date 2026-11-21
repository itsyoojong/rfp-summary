import unittest
from copy import deepcopy

from streamlit.testing.v1 import AppTest

from src.candidate_resolver import resolve_candidates
from src.result_merger import merge_group_results
from src.result_ui import card_state
from src.rfp_analyzer import validate_budget_condition
from src.source_validator import validate_sources
from src.value_validator import validate_budget_amount


def entry(value, page=1, quote=None):
    return {"value": value, "sources": [{"page": page, "quote": quote or value}]}


def prepare(groups, pages):
    merged = merge_group_results(groups)
    resolved = resolve_candidates(merged, pages)
    validation = validate_sources(merged, pages)
    budget_warnings = validate_budget_amount(merged)
    for candidate in merged["사업비"]["candidates"]:
        budget_warnings.extend(validate_budget_condition({"사업비": candidate}))
    return resolved, merged, validation, budget_warnings


def render_app(groups, pages, complete=True):
    resolved, merged, validation, warnings = prepare(groups, pages)
    source = (
        "from src.result_ui import render_analysis_result\n"
        f"render_analysis_result({resolved!r}, {merged!r}, {pages!r}, {validation!r}, "
        f"{warnings!r}, {groups!r}, 3, complete={complete!r})"
    )
    return AppTest.from_string(source).run()


class TestResultUI(unittest.TestCase):
    def state(self, field, groups, pages):
        resolved, merged, validation, warnings = prepare(groups, pages)
        return card_state(field, resolved[field], merged[field], validation, warnings)

    def test_single_verified_candidate(self):
        groups = {0: {"사업명": entry("테스트 사업")}}
        state = self.state("사업명", groups, [{"page_number": 1, "text": "테스트 사업"}])
        self.assertEqual(state["status"], "근거 확인")
        self.assertTrue(state["has_sources"])
        self.assertFalse(state["integrated"])

    def test_project_name_integration(self):
        groups = {0: {"사업명": entry("공식 사업명")}, 1: {"사업명": entry("축약명")}}
        pages = [{"page_number": 1, "text": "1. 사업명\n공식 사업명\n축약명"},
                 {"page_number": 2, "text": "건 명 공식 사업명"}]
        state = self.state("사업명", groups, pages)
        self.assertEqual(state["status"], "근거 확인")
        self.assertTrue(state["integrated"])

    def test_period_integration(self):
        first = "계약일로부터 2026년 4월 30일(목)까지 (약 4개월)"
        second = "계약일로부터 2026년 4월 30일까지"
        groups = {0: {"사업기간": entry(first)}, 1: {"사업기간": entry(second, page=2)}}
        pages = [{"page_number": 1, "text": first}, {"page_number": 2, "text": second}]
        state = self.state("사업기간", groups, pages)
        self.assertEqual(state["status"], "근거 확인")
        self.assertTrue(state["integrated"])

    def test_selected_value_with_one_failed_quote_needs_review(self):
        candidate = entry("테스트 사업")
        candidate["sources"].append({"page": 2, "quote": "없는 인용문"})
        groups = {0: {"사업명": candidate}}
        pages = [{"page_number": 1, "text": "테스트 사업"}, {"page_number": 2, "text": "다른 내용"}]
        resolved, _, _, _ = prepare(groups, pages)
        self.assertEqual(resolved["사업명"]["selected"], "테스트 사업")
        self.assertEqual(self.state("사업명", groups, pages)["status"], "검토 필요")

    def test_integration_does_not_hide_other_candidate_quote_failure(self):
        groups = {0: {"사업명": entry("공식 사업명")},
                  1: {"사업명": entry("축약명", quote="없는 인용문")}}
        pages = [{"page_number": 1, "text": "1. 사업명\n공식 사업명"},
                 {"page_number": 2, "text": "건 명 공식 사업명"}]
        state = self.state("사업명", groups, pages)
        self.assertTrue(state["integrated"])
        self.assertEqual(state["status"], "검토 필요")

    def test_no_source_even_when_name_resolver_selects(self):
        groups = {0: {"사업명": {"value": "공식 사업명", "sources": []}},
                  1: {"사업명": {"value": "축약명", "sources": []}}}
        pages = [{"page_number": 1, "text": "1. 사업명\n공식 사업명"},
                 {"page_number": 2, "text": "건 명 공식 사업명"}]
        state = self.state("사업명", groups, pages)
        self.assertTrue(state["integrated"])
        self.assertFalse(state["has_sources"])
        self.assertEqual(state["status"], "검토 필요")

    def test_conflicting_periods_are_not_integrated(self):
        first = "계약일로부터 2026년 4월 30일까지"
        second = "계약일로부터 2026년 5월 31일까지"
        groups = {0: {"사업기간": entry(first)}, 1: {"사업기간": entry(second, page=2)}}
        pages = [{"page_number": 1, "text": first}, {"page_number": 2, "text": second}]
        state = self.state("사업기간", groups, pages)
        self.assertEqual(state["status"], "검토 필요")
        self.assertFalse(state["integrated"])

    def test_budget_conditions_preserved_and_missing_limit_warns(self):
        value = "6천만원 범위 내 (VAT 포함)"
        pages = [{"page_number": 1, "text": value}]
        groups = {0: {"사업비": entry(value)}}
        app = render_app(groups, pages)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(value == item.value for item in app.markdown))
        self.assertEqual(self.state("사업비", groups, pages)["status"], "근거 확인")
        groups[0]["사업비"]["value"] = "6천만원 (VAT 포함)"
        self.assertEqual(self.state("사업비", groups, pages)["status"], "검토 필요")

    def test_long_unconfirmed_purpose_and_json_details(self):
        purpose = "◯ 접근성 개선 " + "문서의 목적을 그대로 유지합니다. " * 35 + "◯ 운영 안정화"
        groups = {0: {"사업목적": {"value": purpose, "sources": []}}}
        pages = [{"page_number": 1, "text": purpose}]
        original = deepcopy(groups)
        app = render_app(groups, pages, complete=False)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("중간 결과" in message.value for message in app.info))
        labels = [expander.label for expander in app.expander]
        for label in ("사업목적 미확정 추출 후보", "사업목적 후보 1 전체 내용",
                      "최종 결과 JSON 확인", "전체 추출 후보 확인", "그룹별 분석 결과"):
            self.assertIn(label, labels)
        display = "\n".join(item.value for item in app.markdown)
        self.assertIn("확정된 정보가 아닙니다", "\n".join(item.value for item in app.caption))
        self.assertIn(purpose.replace("◯", "\n\n◯").lstrip(), display)
        self.assertEqual(len(app.json), 4)
        self.assertEqual(groups, original)

    def test_missing_deadline_and_five_cards(self):
        pages = [{"page_number": 1, "text": "테스트 사업"}]
        groups = {0: {"사업명": entry("테스트 사업")}}
        state = self.state("제출기한", groups, pages)
        self.assertEqual(state["status"], "미확인")
        app = render_app(groups, pages)
        headings = [heading.value for heading in app.subheader]
        self.assertEqual(headings[:6], ["RFP 핵심 분석 결과", "사업명", "사업기간", "사업비", "사업목적", "제출기한"])
        self.assertTrue(any("전체 그룹 분석 완료" in caption.value for caption in app.caption))

    def test_no_upload_has_no_error(self):
        from pathlib import Path
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
