#!/usr/bin/env python3
"""Fetch arXiv papers and save them as JSON.

The output JSON is keyed by arXiv paper id and stores:
- paper_name
- paper_link
- authors
- category
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

ARXIV_API_URL = "https://export.arxiv.org/api/query"
HF_REPOS_API = "https://huggingface.co/api/arxiv/{arxiv_id}/repos"
GITHUB_SEARCH_REPO = "https://api.github.com/search/repositories"
GITHUB_SEARCH_CODE = "https://api.github.com/search/code"
DEFAULT_OUTPUT = "papers.json"
DEFAULT_CONFIG = {
    "output_path": DEFAULT_OUTPUT,
    "max_results_per_category": 25,
    "include_code_link": True,
    "categories": [
        {
            "name": "Robot & Agent",
            "filters": ["Embodied Agent", "Human-Robot", "Cross-Embodiment", "World Model"],
        },
        {
            "name": "Robotic Manipulation",
            "filters": ["Robot Manipulation", "Robotic Manipulation", "Grasp", "Loco-Manipulation", "Whole-body Manipulation"],
        },
        {
            "name": "Vision Language Action Model",
            "filters": ["Vision-Language-Action Model", "Vision Language Action Model"],
        },
        {
            "name": "Imitation Learning",
            "filters": ["Imitation Learning", "Behavior Cloning", "Behavioral Cloning"],
        },
    ],
}
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_CONFIG.copy()

    config = json.loads(path.read_text(encoding="utf-8"))
    merged = DEFAULT_CONFIG.copy()
    merged.update({k: v for k, v in config.items() if k != "categories"})
    merged["categories"] = config.get("categories", DEFAULT_CONFIG["categories"])
    return merged


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def quote_filter(term: str) -> str:
    term = term.strip()
    if not term:
        return term
    if " " in term or "-" in term:
        return f'"{term}"'
    return term


def build_query(filters: list[str]) -> str:
    return " OR ".join(f'all:{quote_filter(term)}' for term in filters if term.strip())


def http_get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    request = urllib.request.Request(url + query, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def http_get_text(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    request = urllib.request.Request(url + query, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset)


def http_get_bytes(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 20) -> bytes:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    request = urllib.request.Request(url + query, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def arxiv_id_from_entry_id(entry_id: str) -> str:
    match = re.search(r"/abs/([^?#]+)$", entry_id)
    if not match:
        return entry_id.rsplit("/", 1)[-1]
    short_id = match.group(1)
    return re.sub(r"v\d+$", "", short_id)


def parse_arxiv_feed(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    entries: list[dict[str, Any]] = []

    for entry in root.findall(f"{ATOM_NS}entry"):
        entry_id = normalize_whitespace(entry.findtext(f"{ATOM_NS}id", default=""))
        paper_id = arxiv_id_from_entry_id(entry_id)
        title = normalize_whitespace(entry.findtext(f"{ATOM_NS}title", default=""))
        paper_link = ""

        for link in entry.findall(f"{ATOM_NS}link"):
            if link.attrib.get("rel") == "alternate":
                paper_link = link.attrib.get("href", "")
                break
        if not paper_link:
            paper_link = f"https://arxiv.org/abs/{paper_id}"

        authors = []
        for author in entry.findall(f"{ATOM_NS}author"):
            name = normalize_whitespace(author.findtext(f"{ATOM_NS}name", default=""))
            if name:
                authors.append(name)

        entries.append(
            {
                "paper_id": paper_id,
                "paper_name": title,
                "paper_link": paper_link,
                "authors": authors,
                "published_date": normalize_whitespace(entry.findtext(f"{ATOM_NS}published", default=""))[:10],
                "arxiv_primary_category": entry.find(f"{ATOM_NS}category").attrib.get("term") if entry.find(f"{ATOM_NS}category") is not None else None,
            }
        )

    return entries


def fetch_arxiv_papers(query: str, max_results: int) -> list[dict[str, Any]]:
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    xml_text = http_get_text(
        ARXIV_API_URL,
        params=params,
        headers={"User-Agent": "arXiv_daily/1.0 (paper fetcher)"},
    )
    return parse_arxiv_feed(xml_text)


def get_hf_repo_link(arxiv_id: str) -> str | None:
    try:
        data = http_get_json(
            HF_REPOS_API.format(arxiv_id=urllib.parse.quote(arxiv_id, safe="")),
            headers={"User-Agent": "arXiv_daily/1.0"},
        )
    except Exception:
        return None

    def pick(items: Any, kind: str) -> str | None:
        for item in items or []:
            repo_id = item.get("id")
            if repo_id:
                return f"https://huggingface.co/{kind}/{repo_id}"
        return None

    return pick(data.get("spaces"), "spaces") or pick(data.get("models"), "models") or pick(data.get("datasets"), "datasets")


def get_github_repo_link(title: str, arxiv_id: str) -> str | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arXiv_daily/1.0",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    queries = [
        f'"{title}" in:readme,description',
        f'"{arxiv_id}" in:name,readme,description',
        f'"{arxiv_id}" in:file filename:README',
    ]

    for query in queries:
        try:
            data = http_get_json(
                GITHUB_SEARCH_REPO,
                params={"q": query, "sort": "stars", "order": "desc", "per_page": 5},
                headers=headers,
            )
            items = data.get("items", [])
            if items:
                return items[0].get("html_url")
        except Exception:
            continue
    return None


def get_github_code_link(title: str, arxiv_id: str) -> str | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arXiv_daily/1.0",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    queries = [
        f'"{arxiv_id}" in:file filename:README',
        f'"{arxiv_id}" in:file',
        f'"{title}" in:file filename:README',
    ]

    for query in queries:
        try:
            data = http_get_json(
                GITHUB_SEARCH_CODE,
                params={"q": query, "per_page": 5},
                headers=headers,
            )
            items = data.get("items", [])
            if items:
                repository = items[0].get("repository") or {}
                if repository.get("html_url"):
                    return repository["html_url"]
        except Exception:
            continue
    return None


def get_code_link(title: str, arxiv_id: str) -> str | None:
    return get_hf_repo_link(arxiv_id) or get_github_repo_link(title, arxiv_id) or get_github_code_link(title, arxiv_id)


def merge_record(existing: dict[str, Any], new_record: dict[str, Any]) -> dict[str, Any]:
    merged = existing.copy()
    for key, value in new_record.items():
        if key == "category" and merged.get(key):
            continue
        if key == "code_link" and merged.get(key):
            continue
        if key == "authors" and merged.get(key):
            continue
        if value is not None and value != "":
            merged[key] = value
    return merged


def collect_papers(config: dict[str, Any], include_code_link: bool = True) -> dict[str, Any]:
    collected: dict[str, Any] = {}
    max_results = int(config.get("max_results_per_category", 25))

    for category in config.get("categories", []):
        category_name = category["name"]
        filters = category.get("filters", [])
        query = build_query(filters)
        if not query:
            continue

        for paper in fetch_arxiv_papers(query, max_results=max_results):
            paper_id = paper["paper_id"]
            if paper_id in collected:
                continue

            code_link = None
            if include_code_link:
                code_link = get_code_link(paper["paper_name"], paper_id)

            collected[paper_id] = {
                "paper_name": paper["paper_name"],
                "paper_link": paper["paper_link"],
                "authors": paper["authors"],
                "published_date": paper.get("published_date", ""),
                "category": category_name,
            }

    return collected


def parse_published_date(record: dict[str, Any]) -> date:
    value = record.get("published_date", "")
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return date.max


def trim_oldest_papers(data: dict[str, Any], max_items: int) -> dict[str, Any]:
    if max_items <= 0 or len(data) <= max_items:
        return data

    items = sorted(
        data.items(),
        key=lambda item: (
            parse_published_date(item[1]),
            item[0],
        ),
    )
    trimmed = dict(items[-max_items:])
    return {paper_id: trimmed[paper_id] for paper_id in sorted(trimmed.keys())}


def update_output(output_path: Path, new_data: dict[str, Any], max_items: int | None = None) -> dict[str, Any]:
    existing = read_json(output_path)
    merged = existing.copy()

    for paper_id, record in new_data.items():
        if paper_id in merged:
            merged[paper_id] = merge_record(merged[paper_id], record)
        else:
            merged[paper_id] = record

    if max_items is not None:
        merged = trim_oldest_papers(merged, max_items)

    ordered = {paper_id: merged[paper_id] for paper_id in sorted(merged.keys())}
    write_json(output_path, ordered)
    return ordered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch arXiv papers and save them to JSON.")
    parser.add_argument("--config", default="config.json", help="Path to the JSON config file.")
    parser.add_argument("--output", default=None, help="Override the output JSON path from the config.")
    parser.add_argument("--skip-code-link", action="store_true", help="Skip code-link lookup for faster runs.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = Path(args.config)
    config = load_config(config_path)

    output_path = Path(args.output or config.get("output_path", DEFAULT_OUTPUT))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    max_items = config.get("max_items")
    if max_items is None:
        max_items = config.get("max_papers")
    if max_items is None:
        max_items = config.get("max_json_items")
    max_items = int(max_items) if max_items is not None else None

    new_data = collect_papers(config, include_code_link=not args.skip_code_link and bool(config.get("include_code_link", True)))
    update_output(output_path, new_data, max_items=max_items)

    print(f"Saved {len(new_data)} papers to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
