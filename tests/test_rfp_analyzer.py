import json
import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from groq import (AuthenticationError, BadRequestError, InternalServerError,
                  PermissionDeniedError, RateLimitError, APIConnectionError, APITimeoutError)

from src.diagnostics import AnalysisError, analysis_stage, error_code, user_message
from src.rfp_analyzer import analyze_rfp_document, analyze_rfp_text


def valid_result():
    return {field: {"value": None, "sources": []}
            for field in ("사업명", "사업기간", "사업비", "사업목적", "제출기한")}


def response(content, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish, message=SimpleNamespace(content=content))])


class TestAnalyzer(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"GROQ_API_KEY": "secret-test-key"})
        env.start()
        self.addCleanup(env.stop)
        sdk = patch("src.rfp_analyzer.Groq")
        self.client = sdk.start()
        self.addCleanup(sdk.stop)
        self.create = self.client.return_value.chat.completions.create
        self.pages = [{"page_number": 4, "text": "PRIVATE_DOCUMENT_TEXT"}]

    def test_normal_structured_response_and_request_parameters(self):
        data = valid_result()
        data["사업명"] = {"value": "사업", "sources": [{"page": 4, "quote": "근거"}]}
        self.create.return_value = response(json.dumps(data))
        self.assertEqual(analyze_rfp_document(self.pages), data)
        kwargs = self.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/gpt-oss-20b")
        self.assertEqual(kwargs["reasoning_effort"], "low")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})

    def assert_bad_response(self, content, code, finish="stop"):
        self.create.return_value = response(content, finish)
        with self.assertLogs("rfp.diagnostics", level="ERROR") as logs:
            with self.assertRaises(AnalysisError) as caught:
                analyze_rfp_document(self.pages, group_number=2)
        self.assertEqual(caught.exception.code, code)
        text = "\n".join(logs.output)
        for secret in ("PRIVATE_DOCUMENT_TEXT", "secret-test-key", "PRIVATE_LLM_RESPONSE"):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, user_message(caught.exception))
        records = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["group_number"], 2)
        self.assertTrue(records[0]["locations"])
        self.assertIn("input_chars", records[0])
        return records[0]

    def test_empty_response(self):
        for content in (None, "", " \n "):
            with self.subTest(content=content):
                self.assert_bad_response(content, "empty_response")

    def test_no_choices(self):
        self.create.return_value = SimpleNamespace(choices=[])
        with self.assertLogs("rfp.diagnostics"), self.assertRaises(AnalysisError) as caught:
            analyze_rfp_document(self.pages)
        self.assertEqual(caught.exception.code, "empty_response")

    def test_invalid_json_keeps_specific_exception_in_safe_log(self):
        record = self.assert_bad_response("PRIVATE_LLM_RESPONSE", "invalid_json")
        self.assertEqual(record["stage"], "json_parse")
        self.assertEqual(record["exception"], "JSONDecodeError")
        self.assertEqual(record["error_code"], "invalid_json")

    def test_non_object_json(self):
        for value in ([], None, 3, "PRIVATE_LLM_RESPONSE"):
            self.assert_bad_response(json.dumps(value), "invalid_schema")

    def test_missing_required_fields(self):
        data = valid_result()
        for key in ("사업명", "사업기간", "사업비", "사업목적", "제출기한"):
            invalid = deepcopy(data)
            del invalid[key]
            self.assert_bad_response(json.dumps(invalid), "invalid_schema")
        for key in ("value", "sources"):
            invalid = deepcopy(data)
            del invalid["사업명"][key]
            self.assert_bad_response(json.dumps(invalid), "invalid_schema")

    def test_invalid_types(self):
        entries = [None, [], {"value": 3, "sources": []},
                   {"value": None, "sources": None},
                   {"value": "값", "sources": [None]},
                   {"value": "값", "sources": [{"page": "4", "quote": "인용"}]},
                   {"value": "값", "sources": [{"page": True, "quote": "인용"}]},
                   {"value": "값", "sources": [{"page": 0, "quote": "인용"}]},
                   {"value": "값", "sources": [{"page": 4, "quote": []}]}]
        for entry in entries:
            invalid = valid_result()
            invalid["사업명"] = entry
            self.assert_bad_response(json.dumps(invalid), "invalid_schema")

    def test_response_length_stop_is_not_success(self):
        self.assert_bad_response(json.dumps(valid_result()), "truncated_response", "length")

    def test_missing_key_does_not_call_sdk(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "  "}), self.assertLogs("rfp.diagnostics"):
            with self.assertRaises(AnalysisError) as caught:
                analyze_rfp_document(self.pages)
        self.assertEqual(caught.exception.code, "missing_api_key")
        self.client.assert_not_called()

    def test_no_pages_or_text_does_not_call_sdk(self):
        for pages in ([], [{"page_number": 1, "text": "  "}]):
            with self.assertLogs("rfp.diagnostics"), self.assertRaises(AnalysisError):
                analyze_rfp_document(pages)
        self.client.assert_not_called()

    def test_api_status_errors_are_distinguished_without_body_leaks(self):
        cases = ((RateLimitError, 429, "rate_limit", None),
                 (AuthenticationError, 401, "authentication", None),
                 (PermissionDeniedError, 403, "permission", None),
                 (BadRequestError, 400, "bad_request", None),
                 (BadRequestError, 413, "input_size", None),
                 (BadRequestError, 400, "context_limit", "context_length_exceeded"),
                 (InternalServerError, 500, "api_server", None))
        for cls, status, code, api_code in cases:
            error = cls("PRIVATE_LLM_RESPONSE secret-test-key", response=httpx.Response(
                status, request=httpx.Request("POST", "https://example.test")),
                body={"error": {"code": api_code, "message": "PRIVATE_DOCUMENT_TEXT"}})
            self.create.side_effect = error
            with self.assertLogs("rfp.diagnostics") as logs, self.assertRaises(cls):
                analyze_rfp_document(self.pages)
            self.assertEqual(error_code(error), code)
            record = json.loads(logs.records[0].getMessage())
            self.assertEqual(record["stage"], "groq_request")
            self.assertEqual(record["http_status"], status)
            self.assertNotIn("PRIVATE", "\n".join(logs.output))
            self.assertNotIn("secret-test-key", user_message(error))

    def test_connection_and_timeout(self):
        request = httpx.Request("POST", "https://example.test")
        for error, code in ((APIConnectionError(request=request), "connection"),
                            (APITimeoutError(request=request), "timeout")):
            self.create.side_effect = error
            with self.assertLogs("rfp.diagnostics"), self.assertRaises(type(error)):
                analyze_rfp_document(self.pages)
            self.assertEqual(error_code(error), code)

    def test_legacy_text_api_preserves_flat_json_contract(self):
        self.create.return_value = response('{"사업명": "사업"}')
        self.assertEqual(analyze_rfp_text("synthetic input"), {"사업명": "사업"})

    def test_unexpected_failure_does_not_log_message_or_traceback_locals(self):
        with self.assertLogs("rfp.diagnostics") as logs:
            with self.assertRaises(ValueError):
                with analysis_stage("session_save", page_count=3):
                    raise ValueError("PRIVATE_DOCUMENT_TEXT secret-test-key")
        self.assertNotIn("PRIVATE", "\n".join(logs.output))
        self.assertNotIn("secret-test-key", "\n".join(logs.output))
        self.assertEqual(json.loads(logs.records[0].getMessage())["stage"], "session_save")
