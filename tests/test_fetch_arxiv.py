from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from fetch_arxiv import (
    ARXIV_USER_AGENT,
    build_arxiv_request,
    build_query,
    collect_papers,
    fetch_arxiv_papers,
    http_get_text,
    main,
    merge_record,
    parse_arxiv_feed,
    parse_arxiv_rss,
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
    def test_arxiv_request_declares_atom_and_identifies_client(self):
        request = build_arxiv_request("https://example.test/query", {"search_query": "cat:cs.*"})
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("search_query=cat%3Acs.%2A", request.full_url)
        self.assertIn("application/atom+xml", request.get_header("Accept"))
        self.assertEqual(request.get_header("User-agent"), ARXIV_USER_AGENT)

    def test_http_406_is_delegated_without_replaying_request(self):
        body = io.BytesIO(b"upstream rejection")
        error = HTTPError("https://example.test", 406, "Not Acceptable", {"Via": "1.1 varnish"}, body)
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=error) as urlopen,
            patch("fetch_arxiv.time.sleep") as sleep,
            patch("fetch_arxiv.sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(HTTPError),
        ):
            http_get_text("https://example.test", {}, max_retries=4)
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        self.assertTrue(body.closed)
        self.assertIn("HTTP 406", stderr.getvalue())
        self.assertIn("URL=https://example.test", stderr.getvalue())
        self.assertIn("Via=1.1 varnish", stderr.getvalue())
        self.assertIn("upstream rejection", stderr.getvalue())

    def test_fetch_falls_back_to_cs_rss_after_406(self):
        error = HTTPError("https://example.test", 406, "Not Acceptable", {}, io.BytesIO())
        rss = """<?xml version="1.0"?>
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>Agent Example</title>
          <link>https://arxiv.org/abs/2609.12345</link>
          <description>Abstract: An embodied agent.</description>
          <pubDate>Thu, 17 Sep 2026 00:00:00 -0400</pubDate>
          <dc:creator>Alice, Bob</dc:creator>
        </item></channel></rss>"""
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=[error, FakeResponse(rss)]) as urlopen,
            patch("fetch_arxiv.sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            papers = fetch_arxiv_papers("cat:cs.* AND (all:agent)", 25, ["agent"])
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(urlopen.call_args_list[0].args[0].get_method(), "GET")
        self.assertIn("rss.arxiv.org/rss/cs", urlopen.call_args_list[1].args[0].full_url)
        self.assertEqual(papers[0]["paper_id"], "2609.12345")
        self.assertEqual(papers[0]["authors"], ["Alice", "Bob"])
        self.assertIn("falling back", stderr.getvalue())

    def test_fetch_reuses_rss_after_query_endpoint_is_rejected(self):
        error = HTTPError("https://example.test", 406, "Not Acceptable", {}, io.BytesIO())
        rss = """<?xml version="1.0"?>
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>Physical Agent</title><link>https://arxiv.org/abs/2609.12345</link>
          <description>A dynamic embodied agent.</description>
          <pubDate>Thu, 17 Sep 2026 00:00:00 -0400</pubDate>
          <dc:creator>Alice</dc:creator>
        </item></channel></rss>"""
        cache = {}
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=[error, FakeResponse(rss)]) as urlopen,
            patch("fetch_arxiv.sys.stderr", new_callable=io.StringIO),
        ):
            agent_papers = fetch_arxiv_papers("agent query", 25, ["agent"], cache)
            physical_papers = fetch_arxiv_papers("physical query", 25, ["physical"], cache)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(agent_papers[0]["paper_id"], "2609.12345")
        self.assertEqual(physical_papers[0]["paper_id"], "2609.12345")

    def test_http_error_body_logging_is_bounded(self):
        error = HTTPError("https://example.test", 406, "Not Acceptable", {}, io.BytesIO(b"x" * 2000 + b"TAIL"))
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=error),
            patch("fetch_arxiv.sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(HTTPError),
        ):
            http_get_text("https://example.test", {}, max_retries=0)
        self.assertIn("x" * 2000, stderr.getvalue())
        self.assertNotIn("TAIL", stderr.getvalue())

    def test_error_body_read_failure_does_not_prevent_retry(self):
        body = io.BytesIO()
        body.close()
        error = HTTPError("https://example.test", 503, "Unavailable", {}, body)
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=[error, FakeResponse("ok")]),
            patch("fetch_arxiv.time.sleep"),
            patch("fetch_arxiv.sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(http_get_text("https://example.test", {}, max_retries=1), "ok")
        self.assertIn("could not read response", stderr.getvalue())

    def test_fetch_failure_preserves_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "papers.json"
            original = '{"existing": {"paper_name": "Keep me"}}\n'
            output.write_text(original, encoding="utf-8")
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({
                "output_path": str(output),
                "categories": [{"name": "Agent", "filters": ["agent"]}],
            }), encoding="utf-8")
            errors = [HTTPError("https://example.test", 406, "Not Acceptable", {}, io.BytesIO()) for _ in range(5)]
            with (
                patch("fetch_arxiv.sys.argv", ["fetch_arxiv.py", "--config", str(config)]),
                patch("fetch_arxiv.urllib.request.urlopen", side_effect=errors),
                patch("fetch_arxiv.time.sleep"),
                patch("fetch_arxiv.sys.stderr", new_callable=io.StringIO),
                self.assertRaises(HTTPError),
            ):
                main()
            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_retry_after_respects_minimum_request_interval(self):
        error = HTTPError("https://example.test", 429, "rate limited", {"Retry-After": "0"}, None)
        with (
            patch("fetch_arxiv.urllib.request.urlopen", side_effect=[error, FakeResponse("ok")]),
            patch("fetch_arxiv.time.sleep") as sleep,
        ):
            self.assertEqual(http_get_text("https://example.test", {}, max_retries=1), "ok")
        sleep.assert_called_once_with(3.0)

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

    def test_rss_filters_keywords_and_limits_results(self):
        xml = """<?xml version="1.0"?>
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
          <item><title>Unrelated</title><link>https://arxiv.org/abs/2609.00001</link>
            <description>No match.</description><pubDate>Wed, 16 Sep 2026 00:00:00 -0400</pubDate>
            <dc:creator>Alice</dc:creator></item>
          <item><title>Physical Agent</title><link>https://arxiv.org/abs/2609.00002v1</link>
            <description>Dynamic manipulation.</description><pubDate>Thu, 17 Sep 2026 00:00:00 -0400</pubDate>
            <dc:creator>Bob, Carol</dc:creator></item>
        </channel></rss>"""
        papers = parse_arxiv_rss(xml, ["physical", "dynamic"], 1)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["paper_id"], "2609.00002")
        self.assertEqual(papers[0]["published_date"], "2026-09-17")

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
