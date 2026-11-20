import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.result_store import get_cache_path, load_group_results, save_group_results


class TestResultStore(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        mock = patch("src.result_store.CACHE_DIR", Path(directory.name))
        mock.start()
        self.addCleanup(mock.stop)
        self.document_id = "a" * 64

    def test_json_roundtrip_restores_integer_keys(self):
        results = {0: {"사업명": {"value": "테스트", "sources": []}}}
        save_group_results(self.document_id, 3, results)
        self.assertEqual(load_group_results(self.document_id, 3), results)

    def test_missing_cache(self):
        self.assertEqual(load_group_results(self.document_id, 3), {})

    def test_invalid_identity_and_chunk_size(self):
        for identity, size in (("../invalid", 3), (self.document_id, 0)):
            with self.assertRaises(ValueError):
                get_cache_path(identity, size)

    def test_invalid_cache_metadata_and_noncontiguous_groups(self):
        for data in ({"version": "wrong"}, {
            "version": "v1", "document_id": self.document_id,
            "chunk_size": 3, "group_results": {"1": {}}
        }):
            get_cache_path(self.document_id, 3).write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_group_results(self.document_id, 3)
