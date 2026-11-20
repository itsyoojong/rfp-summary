import os
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class TestApp(unittest.TestCase):
    def test_group_analysis_rerun_reset_and_public_cache_policy(self):
        pages = [{"page_number": 1, "text": "사업비: 6천만원 범위 내"}]
        result = {
            field: {"value": None, "sources": []}
            for field in ("사업명", "사업기간", "사업비", "사업목적", "제출기한")
        }
        result["사업비"] = {
            "value": "6천만원 범위 내",
            "sources": [{"page": 1, "quote": pages[0]["text"]}]
        }
        with patch.dict(os.environ, {"RFP_ENABLE_DISK_CACHE": "false"}), \
                patch("streamlit.file_uploader", return_value=BytesIO(b"document A")) as upload, \
                patch("src.pdf_loader.extract_pdf_pages", return_value=pages), \
                patch("src.rfp_analyzer.analyze_rfp_document", return_value=result) as analyze, \
                patch("src.session_results.load_group_results") as load, \
                patch("src.session_results.save_group_results") as save:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            self.assertEqual(len(app.exception), 0)
            next(b for b in app.button if b.label == "다음 그룹 분석").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertEqual(app.session_state["group_results"], {0: result})
            self.assertTrue(any("모든 그룹 분석이 완료" in message.value for message in app.success))
            app.run()
            self.assertEqual(app.session_state["group_results"], {0: result})
            other = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            self.assertEqual(other.session_state["group_results"], {})
            upload.return_value = BytesIO(b"document B")
            app.run()
            self.assertEqual(app.session_state["group_results"], {})
            next(b for b in app.button if b.label == "AI 분석 시작").click().run()
            self.assertEqual(app.session_state["page_results"], {1: result})
            upload.return_value = None
            app.run()
            self.assertEqual(app.session_state["page_results"], {})
            self.assertEqual(app.session_state["group_results"], {})
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(analyze.call_count, 2)
            load.assert_not_called()
            save.assert_not_called()

    def test_empty_response_stays_unsaved_and_retry_can_succeed(self):
        from types import SimpleNamespace
        import json
        result = {field: {"value": None, "sources": []}
                  for field in ("사업명", "사업기간", "사업비", "사업목적", "제출기한")}
        def response(content):
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=content))])
        with patch.dict(os.environ, {"RFP_ENABLE_DISK_CACHE": "false", "GROQ_API_KEY": "dummy"}), \
                patch("streamlit.file_uploader", return_value=BytesIO(b"synthetic")), \
                patch("src.pdf_loader.extract_pdf_pages", return_value=[{"page_number": 1, "text": "PRIVATE_TEXT"}]), \
                patch("src.rfp_analyzer.Groq") as sdk:
            create = sdk.return_value.chat.completions.create
            create.return_value = response("")
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            with self.assertLogs("rfp.diagnostics") as logs:
                next(b for b in app.button if b.label == "다음 그룹 분석").click().run()
            self.assertEqual(app.session_state["group_results"], {})
            self.assertTrue(any("응답이 비어" in e.value for e in app.error))
            self.assertNotIn("PRIVATE_TEXT", "\n".join(logs.output))
            record = json.loads(logs.records[0].getMessage())
            self.assertEqual(record["stage"], "llm_response")
            self.assertEqual(record["group_number"], 1)
            create.return_value = response(json.dumps(result))
            next(b for b in app.button if b.label == "다음 그룹 분석").click().run()
            self.assertEqual(app.session_state["group_results"], {0: result})
            self.assertEqual(len(app.error), 0)

    def test_session_write_failure_is_reported_without_saving(self):
        import json
        class BrokenResults(dict):
            def __setitem__(self, key, value):
                raise ValueError("PRIVATE_STORAGE_DETAILS")
        result = {field: {"value": None, "sources": []}
                  for field in ("사업명", "사업기간", "사업비", "사업목적", "제출기한")}
        with patch.dict(os.environ, {"RFP_ENABLE_DISK_CACHE": "false"}), \
                patch("streamlit.file_uploader", return_value=BytesIO(b"synthetic")), \
                patch("src.pdf_loader.extract_pdf_pages", return_value=[{"page_number": 1, "text": "synthetic"}]), \
                patch("src.rfp_analyzer.analyze_rfp_document", return_value=result):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            app.session_state["group_results"] = BrokenResults()
            with self.assertLogs("rfp.diagnostics") as logs:
                next(b for b in app.button if b.label == "다음 그룹 분석").click().run()
            self.assertEqual(app.session_state["group_results"], {})
            self.assertTrue(app.error)
            self.assertEqual(json.loads(logs.records[0].getMessage())["stage"], "session_save")
            self.assertNotIn("PRIVATE_STORAGE_DETAILS", "\n".join(logs.output))
