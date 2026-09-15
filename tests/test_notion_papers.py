from __future__ import annotations

import unittest
from unittest.mock import patch

from notion_papers import (
    PROPERTY_ARXIV_ID,
    PROPERTY_CATEGORY,
    PROPERTY_CODE_LINK,
    PROPERTY_NOTES,
    PROPERTY_PAPER,
    PROPERTY_STATUS,
    PROPERTY_WEB_LINK,
    NotionAPIError,
    NotionClient,
    PaperDataError,
    build_create_properties,
    build_update_properties,
    normalize_arxiv_id,
    parse_paper_record,
    property_text,
    rich_text_property,
    select_property,
    sync_records,
    title_property,
    url_property,
)


class FakeResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.body


class FakeNotionClient:
    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.created = []
        self.updated = []

    def query_data_source(self, _data_source_id):
        return self.pages

    def create_page(self, _data_source_id, properties):
        page = {"id": f"created-{len(self.created) + 1}", "properties": properties}
        self.pages.append(page)
        self.created.append(page)
        return page

    def update_page(self, page_id, properties):
        page = next(page for page in self.pages if page["id"] == page_id)
        page.setdefault("properties", {}).update(properties)
        self.updated.append((page_id, properties))
        return page


class NotionPapersTests(unittest.TestCase):
    def test_notion_client_retries_timeout(self):
        client = NotionClient("token", max_retries=2)
        with (
            patch(
                "notion_papers.urllib.request.urlopen",
                side_effect=[TimeoutError(), TimeoutError(), FakeResponse('{"ok": true}')],
            ),
            patch("notion_papers.time.sleep") as sleep,
        ):
            self.assertEqual(client._request("GET", "/test"), {"ok": True})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_notion_client_wraps_exhausted_timeout(self):
        client = NotionClient("token", max_retries=1)
        with (
            patch("notion_papers.urllib.request.urlopen", side_effect=TimeoutError()),
            patch("notion_papers.time.sleep"),
            self.assertRaisesRegex(NotionAPIError, "POST /data_sources/test/query timed out after retries"),
        ):
            client._request("POST", "/data_sources/test/query", {})

    def test_normalize_arxiv_id(self):
        self.assertEqual(normalize_arxiv_id("https://arxiv.org/abs/2608.14530v2"), "2608.14530")
        self.assertEqual(normalize_arxiv_id("hep-th/9901001v1"), "hep-th/9901001")
        with self.assertRaises(PaperDataError):
            normalize_arxiv_id("not-an-arxiv-id")

    def test_create_properties_sets_unread_and_categories(self):
        record = parse_paper_record(
            "2608.14530",
            {
                "paper_name": "Example",
                "authors": ["A", "B"],
                "paper_link": "https://arxiv.org/abs/2608.14530",
                "categories": ["Agent", "Robotics"],
                "published_date": "2026-08-18",
            },
        )
        properties = build_create_properties(record, "2026-08-19T00:00:00Z")
        self.assertEqual(properties[PROPERTY_STATUS]["select"]["name"], "Unread")
        self.assertEqual(
            [item["name"] for item in properties[PROPERTY_CATEGORY]["multi_select"]],
            ["Agent", "Robotics"],
        )

    def test_update_does_not_overwrite_user_owned_fields(self):
        record = parse_paper_record(
            "2608.14530",
            {
                "paper_name": "New title",
                "paper_link": "https://arxiv.org/abs/2608.14530",
                "category": "Source category",
                "web_link": "https://new.example",
                "code_link": "https://github.com/new/example",
            },
        )
        page = {
            "id": "page-1",
            "properties": {
                PROPERTY_PAPER: title_property("Old title"),
                PROPERTY_ARXIV_ID: rich_text_property("2608.14530"),
                PROPERTY_STATUS: select_property("Save"),
                PROPERTY_CATEGORY: {"type": "multi_select", "multi_select": [{"name": "Manual"}]},
                PROPERTY_WEB_LINK: url_property("https://manual.example"),
                PROPERTY_CODE_LINK: url_property("https://github.com/manual/example"),
                PROPERTY_NOTES: rich_text_property("Keep this note"),
            },
        }
        updates = build_update_properties(record, page, "2026-08-19T00:00:00Z")
        self.assertIn(PROPERTY_PAPER, updates)
        self.assertNotIn(PROPERTY_STATUS, updates)
        self.assertNotIn(PROPERTY_CATEGORY, updates)
        self.assertNotIn(PROPERTY_WEB_LINK, updates)
        self.assertNotIn(PROPERTY_CODE_LINK, updates)
        self.assertNotIn(PROPERTY_NOTES, updates)

    def test_sync_ignores_manual_rows_and_is_idempotent(self):
        manual = {
            "id": "manual-1",
            "properties": {PROPERTY_PAPER: title_property("External paper")},
        }
        client = FakeNotionClient([manual])
        record = parse_paper_record("2608.14530", {"paper_name": "Example"})

        first = sync_records([record], client, "data-source", now="2026-08-19T00:00:00Z")
        self.assertEqual(first.created, 1)
        self.assertEqual(property_text(manual, PROPERTY_ARXIV_ID), "")

        second = sync_records([record], client, "data-source", now="2026-08-19T00:00:00Z")
        self.assertEqual(second.skipped, 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 0)


if __name__ == "__main__":
    unittest.main()
