from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fetch_arxiv import (
    build_query,
    collect_papers,
    http_get_text,
    merge_record,
    parse_arxiv_feed,
    trim_oldest_papers,
)


class FakeResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")
        self.headers = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get_content_charset(self):
        return "utf-8"

    def read(self):
        return self.body


class FetchArxivTests(unittest.TestCase):
    def test_http_retries_429_and_honors_retry_after(self):
        error = HTTPError("https://example.test", 429, "rate limited", {"Retry-After": "7"}, None)
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=[error, FakeResponse("ok")]),
            patch("fetch_arxiv.time.sleep") as sleep,
        ):
            self.assertEqual(http_get_text("https://example.test", {}, max_retries=1), "ok")
        sleep.assert_called_once_with(7.0)

    def test_http_does_not_retry_non_retryable_client_error(self):
        error = HTTPError("https://example.test", 400, "bad request", {}, None)
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=error),
            patch("fetch_arxiv.time.sleep") as sleep,
            self.assertRaises(HTTPError),
        ):
            http_get_text("https://example.test", {}, max_retries=4)
        sleep.assert_not_called()

    def test_http_retries_timeout_with_exponential_backoff(self):
        with (
            patch(
                "fetch_arxiv.urllib.request.urlopen",
                side_effect=[TimeoutError(), TimeoutError(), FakeResponse("ok")],
            ),
            patch("fetch_arxiv.time.sleep") as sleep,
        ):
            self.assertEqual(http_get_text("https://example.test", {}, max_retries=2), "ok")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [10.0, 20.0])

    def test_category_requests_are_spaced(self):
        config = {
            "categories": [
                {"name": "Agent", "filters": ["agent"]},
                {"name": "Physical", "filters": ["physical"]},
            ]
        }
        with (
            patch("fetch_arxiv.fetch_arxiv_papers", return_value=[]) as fetch,
            patch("fetch_arxiv.time.sleep") as sleep,
        ):
            collect_papers(config)
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(3.0)

    def test_query_is_scoped_to_computer_science(self):
        self.assertEqual(
            build_query(["physics", "physical", "dynamic"]),
            "cat:cs.* AND (all:physics OR all:physical OR all:dynamic)",
        )
        self.assertEqual(build_query(["", "  "]), "")

    def test_feed_uses_versionless_id_and_omits_abstract(self):
        xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/2608.14530v2</id>
            <title>  Example\n paper  </title>
            <summary>  Abstract\n text. </summary>
            <published>2026-08-18T00:00:00Z</published>
            <author><name>Alice</name></author>
            <link rel="alternate" href="https://arxiv.org/abs/2608.14530v2" />
          </entry>
        </feed>"""
        paper = parse_arxiv_feed(xml)[0]
        self.assertEqual(paper["paper_id"], "2608.14530")
        self.assertEqual(paper["paper_name"], "Example paper")
        self.assertNotIn("abstract", paper)

    def test_merge_preserves_all_source_categories(self):
        merged = merge_record(
            {"paper_name": "Old", "category": "Agent"},
            {"paper_name": "New", "category": "Dexterous", "categories": ["Dexterous"]},
        )
        self.assertEqual(merged["paper_name"], "New")
        self.assertEqual(merged["categories"], ["Agent", "Dexterous"])

    def test_merge_does_not_turn_missing_category_into_none(self):
        merged = merge_record(
            {},
            {"paper_name": "New", "category": "Dexterous", "categories": ["Dexterous"]},
        )
        self.assertEqual(merged["categories"], ["Dexterous"])
        self.assertEqual(merged["category"], "Dexterous")

    def test_merge_removes_legacy_none_category(self):
        merged = merge_record(
            {"paper_name": "Old", "category": "None", "categories": ["None", "Agent"]},
            {"paper_name": "New", "category": "Agent", "categories": ["Agent"]},
        )
        self.assertEqual(merged["categories"], ["Agent"])
        self.assertEqual(merged["category"], "Agent")

    def test_trim_removes_records_without_dates_first(self):
        data = {
            "missing": {"published_date": ""},
            "old": {"published_date": "2025-01-01"},
            "new": {"published_date": "2026-01-01"},
        }
        self.assertEqual(set(trim_oldest_papers(data, 2)), {"old", "new"})


if __name__ == "__main__":
    unittest.main()
