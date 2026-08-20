#!/usr/bin/env python3
"""Fetch recent arXiv papers by keyword and merge them into JSON."""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
DEFAULT_OUTPUT = "docs/paper_arxiv.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config does not exist: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("categories"), list):
        raise ValueError("Config must be an object with a categories array.")
    return config


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def quote_filter(term: str) -> str:
    term = term.strip()
    return f'"{term}"' if " " in term or "-" in term else term


def build_query(filters: list[str]) -> str:
    return " OR ".join(f"all:{quote_filter(term)}" for term in filters if term.strip())


def http_get_text(url: str, params: dict[str, Any], timeout: int = 120) -> str:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "arXiv_daily/1.0 (paper fetcher)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset)


def arxiv_id_from_entry_id(entry_id: str) -> str:
    return re.sub(r"v\d+$", "", entry_id.rsplit("/", 1)[-1])


def parse_arxiv_feed(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    papers: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        paper_id = arxiv_id_from_entry_id(normalize_whitespace(entry.findtext(f"{ATOM_NS}id", default="")))
        paper_link = next(
            (
                link.attrib.get("href", "")
                for link in entry.findall(f"{ATOM_NS}link")
                if link.attrib.get("rel") == "alternate"
            ),
            f"https://arxiv.org/abs/{paper_id}",
        )
        authors = [
            name
            for author in entry.findall(f"{ATOM_NS}author")
            if (name := normalize_whitespace(author.findtext(f"{ATOM_NS}name", default="")))
        ]
        papers.append(
            {
                "paper_id": paper_id,
                "paper_name": normalize_whitespace(entry.findtext(f"{ATOM_NS}title", default="")),
                "paper_link": paper_link,
                "authors": authors,
                "published_date": normalize_whitespace(entry.findtext(f"{ATOM_NS}published", default=""))[:10],
            }
        )
    return papers


def fetch_arxiv_papers(query: str, max_results: int) -> list[dict[str, Any]]:
    xml_text = http_get_text(
        ARXIV_API_URL,
        {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )
    return parse_arxiv_feed(xml_text)


def collect_papers(config: dict[str, Any]) -> dict[str, Any]:
    collected: dict[str, Any] = {}
    max_results = int(config.get("max_results_per_category", 25))

    for category in config["categories"]:
        category_name = str(category.get("name", "")).strip()
        query = build_query([str(value) for value in category.get("filters", [])])
        if not category_name or not query:
            continue

        for paper in fetch_arxiv_papers(query, max_results):
            paper_id = paper.pop("paper_id")
            if paper_id in collected:
                categories = collected[paper_id]["categories"]
                if category_name not in categories:
                    categories.append(category_name)
                continue
            collected[paper_id] = {**paper, "category": category_name, "categories": [category_name]}
    return collected


def merge_record(existing: dict[str, Any], fetched: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged.pop("abstract", None)
    for key, value in fetched.items():
        if key not in {"category", "categories"} and value not in (None, "", []):
            merged[key] = value

    categories: list[str] = []
    for source in (existing.get("categories"), [existing.get("category")], fetched.get("categories")):
        for value in source or []:
            clean = str(value).strip()
            if clean and clean not in categories:
                categories.append(clean)
    merged["categories"] = categories
    merged["category"] = categories[0] if categories else ""
    return merged


def parse_published_date(record: dict[str, Any]) -> date:
    try:
        return date.fromisoformat(str(record.get("published_date", ""))[:10])
    except ValueError:
        return date.min


def trim_oldest_papers(data: dict[str, Any], max_items: int) -> dict[str, Any]:
    if max_items <= 0 or len(data) <= max_items:
        return data
    newest = sorted(data.items(), key=lambda item: (parse_published_date(item[1]), item[0]))[-max_items:]
    return dict(sorted(newest))


def update_output(output_path: Path, fetched: dict[str, Any], max_items: int | None = None) -> dict[str, Any]:
    merged = read_json(output_path)
    for paper_id, record in fetched.items():
        merged[paper_id] = merge_record(merged.get(paper_id, {}), record)
    if max_items is not None:
        merged = trim_oldest_papers(merged, max_items)
    merged = dict(sorted(merged.items()))
    write_json(output_path, merged)
    return merged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch arXiv papers and merge them into JSON.")
    parser.add_argument("--config", default="config.json", help="Configuration file.")
    parser.add_argument("--output", help="Override output_path from the configuration.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(Path(args.config))
    output_path = Path(args.output or config.get("output_path", DEFAULT_OUTPUT))
    max_items = int(config["max_items"]) if config.get("max_items") is not None else None
    fetched = collect_papers(config)
    total = len(update_output(output_path, fetched, max_items))
    print(f"Fetched {len(fetched)} papers; archive contains {total}: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
