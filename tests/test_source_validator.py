import unittest
from copy import deepcopy

from src.source_validator import validate_sources
from src.value_validator import parse_krw_amount, validate_budget_amount
from src.rfp_analyzer import validate_budget_condition


class TestSourceValidator(unittest.TestCase):
    def setUp(self):
        self.pages = [{"page_number": 3, "text": "사업비: 6천만원 범위 내 (VAT 포함)"}]
        self.merged = {"사업비": {"candidates": [{
            "value": "6천만원 범위 내 (VAT 포함)",
            "sources": [{"page": 3, "quote": self.pages[0]["text"]}]
        }]}}

    def test_normal_quote(self):
        self.assertEqual(validate_sources(self.merged, self.pages)["matched"], 1)

    def test_wrong_or_empty_quote(self):
        for quote in ("사업비: 60억원", "", "   "):
            with self.subTest(quote=quote):
                merged = deepcopy(self.merged)
                merged["사업비"]["candidates"][0]["sources"][0]["quote"] = quote
                result = validate_sources(merged, self.pages)
                self.assertEqual(result["matched"], 0)
                self.assertEqual(len(result["issues"]), 1)

    def test_nonexistent_page(self):
        self.merged["사업비"]["candidates"][0]["sources"][0]["page"] = 99
        result = validate_sources(self.merged, self.pages)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["issues"][0]["reason"], "존재하지 않는 페이지")

    def test_actual_page_found(self):
        self.merged["사업비"]["candidates"][0]["sources"][0]["page"] = 5
        result = validate_sources(self.merged, self.pages + [{"page_number": 5, "text": "다른 내용"}])
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["issues"][0]["actual_pages"], [3])

    def test_amount_normalization(self):
        for text, amount in (("6천만원", 60000000), ("60,000,000원", 60000000), ("60억원", 6000000000)):
            self.assertEqual(parse_krw_amount(text), amount)
        self.assertIsNone(parse_krw_amount("1억 5천만원"))

    def test_wrong_value_with_valid_quote(self):
        self.merged["사업비"]["candidates"][0]["value"] = "60억원 범위 내 (VAT 포함)"
        self.assertEqual(validate_sources(self.merged, self.pages)["matched"], 1)
        self.assertEqual(len(validate_budget_amount(self.merged)), 1)

    def test_matching_budget(self):
        self.assertEqual(validate_budget_amount(self.merged), [])

    def test_budget_limit_condition(self):
        candidate = self.merged["사업비"]["candidates"][0]
        self.assertEqual(validate_budget_condition({"사업비": candidate}), [])
        candidate["value"] = "6천만원"
        self.assertEqual(len(validate_budget_condition({"사업비": candidate})), 1)
