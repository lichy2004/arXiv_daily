from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from export_notion_saved import atomic_write_json, build_saved_export
from notion_papers import (
    PROPERTY_ARXIV_ID,
    PROPERTY_AUTHORS,
    PROPERTY_CATEGORY,
    PROPERTY_PAPER,
    PROPERTY_STATUS,
    multi_select_property,
    rich_text_property,
    select_property,
    title_property,
)


class SavedExportTests(unittest.TestCase):
    def test_only_save_status_is_exported(self):
        saved_page = {
            "id": "saved",
            "last_edited_time": "2026-08-19T00:00:00Z",
            "properties": {
                PROPERTY_PAPER: title_property("Saved paper"),
                PROPERTY_ARXIV_ID: rich_text_property("2608.14530"),
                PROPERTY_AUTHORS: rich_text_property("A, B"),
                PROPERTY_STATUS: select_property("Save"),
                PROPERTY_CATEGORY: multi_select_property(["Agent"]),
            },
        }
        read_page = {
            "id": "read",
            "properties": {
                PROPERTY_PAPER: title_property("Read paper"),
                PROPERTY_ARXIV_ID: rich_text_property("2608.14531"),
                PROPERTY_STATUS: select_property("Read"),
            },
        }
        result = build_saved_export([read_page, saved_page])
        self.assertEqual(list(result), ["2608.14530"])
        self.assertEqual(result["2608.14530"]["authors"], ["A", "B"])

    def test_atomic_write_replaces_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.json"
            path.write_text('{"old": true}\n', encoding="utf-8")
            atomic_write_json(path, {"new": {"value": 1}})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"new": {"value": 1}})

    def test_manual_saved_paper_uses_stable_notion_key(self):
        page = {
            "id": "12345678-1234-1234-1234-123456789abc",
            "properties": {
                PROPERTY_PAPER: title_property("External paper"),
                PROPERTY_STATUS: select_property("Save"),
            },
        }
        result = build_saved_export([page])
        key = "notion:12345678123412341234123456789abc"
        self.assertEqual(list(result), [key])
        self.assertEqual(result[key]["source"], "manual")
        self.assertEqual(result[key]["arxiv_id"], "")


if __name__ == "__main__":
    unittest.main()
