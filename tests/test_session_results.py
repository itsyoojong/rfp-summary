import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.session_results import disk_cache_enabled, persist_groups, set_document


class TestSessionResults(unittest.TestCase):
    def test_public_mode_never_reads_or_writes_existing_cache(self):
        for settings in ({}, {"RFP_ENABLE_DISK_CACHE": "false"}, {"RFP_ENABLE_DISK_CACHE": "1"}):
            with self.subTest(settings=settings), patch.dict(os.environ, settings, clear=True), \
                    patch("src.session_results.load_group_results") as load, \
                    patch("src.session_results.save_group_results") as save:
                state = {}
                set_document(state, "a" * 64, 3)
                state["group_results"][0] = {"value": "session only"}
                persist_groups("a" * 64, 3, state["group_results"])
                self.assertFalse(disk_cache_enabled())
                load.assert_not_called()
                save.assert_not_called()

    @patch.dict(os.environ, {"RFP_ENABLE_DISK_CACHE": "true"})
    def test_local_opt_in_roundtrip(self):
        with TemporaryDirectory() as directory, patch("src.result_store.CACHE_DIR", Path(directory)):
            persist_groups("a" * 64, 3, {0: {"value": "local"}})
            state = {}
            set_document(state, "a" * 64, 3)
            self.assertEqual(state["group_results"], {0: {"value": "local"}})

    @patch.dict(os.environ, {}, clear=True)
    def test_rerun_preserves_and_document_change_resets(self):
        state = {}
        set_document(state, "a" * 64, 3)
        state["page_results"][1] = "page"
        state["group_results"][0] = "group"
        set_document(state, "a" * 64, 3)
        self.assertEqual(state["group_results"], {0: "group"})
        self.assertEqual(state["page_results"], {1: "page"})
        set_document(state, "b" * 64, 3)
        self.assertEqual(state["group_results"], {})
        self.assertEqual(state["page_results"], {})

    @patch.dict(os.environ, {}, clear=True)
    def test_sessions_are_isolated_and_upload_removal_resets(self):
        first, second = {}, {}
        set_document(first, "a" * 64, 3)
        first["group_results"][0] = "private"
        set_document(second, "a" * 64, 3)
        self.assertEqual(second["group_results"], {})
        set_document(first, None, 3)
        self.assertEqual(first["group_results"], {})

    @patch.dict(os.environ, {"RFP_ENABLE_DISK_CACHE": "true"})
    @patch("src.session_results.load_group_results", side_effect=ValueError("bad cache"))
    def test_failed_restore_does_not_keep_previous_document(self, load):
        state = {"document_id": "a" * 64, "group_results": {0: "old"}}
        with self.assertRaises(ValueError):
            set_document(state, "b" * 64, 3)
        self.assertEqual(state["group_results"], {})
