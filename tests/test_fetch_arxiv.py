from __future__ import annotations

import unittest

from fetch_arxiv import merge_record, parse_arxiv_feed, trim_oldest_papers


class FetchArxivTests(unittest.TestCase):
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

    def test_trim_removes_records_without_dates_first(self):
        data = {
            "missing": {"published_date": ""},
            "old": {"published_date": "2025-01-01"},
            "new": {"published_date": "2026-01-01"},
        }
        self.assertEqual(set(trim_oldest_papers(data, 2)), {"old", "new"})


if __name__ == "__main__":
    unittest.main()
