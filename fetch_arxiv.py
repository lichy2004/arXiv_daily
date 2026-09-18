#!/usr/bin/env python3
"""Fetch recent arXiv papers by keyword and merge them into JSON."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_CATEGORY_QUERY = "cat:cs.*"
ARXIV_REQUEST_DELAY_SECONDS = 3.0
ARXIV_MAX_RETRIES = 4
ARXIV_RETRY_BASE_SECONDS = 10.0
ARXIV_RETRY_MAX_SECONDS = 120.0
ARXIV_USER_AGENT = "arXiv_daily/1.0 (https://github.com/lichy2004/arXiv_daily)"
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
    keyword_query = " OR ".join(f"all:{quote_filter(term)}" for term in filters if term.strip())
    return f"{ARXIV_CATEGORY_QUERY} AND ({keyword_query})" if keyword_query else ""


def retry_delay_seconds(error: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return max(float(retry_after), ARXIV_REQUEST_DELAY_SECONDS)
        except ValueError:
            pass
    return min(ARXIV_RETRY_BASE_SECONDS * (2**attempt), ARXIV_RETRY_MAX_SECONDS)


def log_retry(reason: str, attempt: int, max_retries: int, delay: float) -> None:
    print(
        f"arXiv request failed ({reason}); retrying in {delay:g}s "
        f"(attempt {attempt + 2}/{max_retries + 1})",
        file=sys.stderr,
    )


def log_http_error(error: urllib.error.HTTPError) -> None:
    """Capture bounded diagnostics and release the response before retrying."""
    try:
        try:
            body = error.read(2000).decode("utf-8", errors="replace") or "<empty>"
        except (OSError, ValueError) as read_error:
            body = f"<could not read response: {read_error}>"
        headers = error.headers or {}
        details = "; ".join(
            f"{name}={headers[name]}"
            for name in ("Content-Type", "Server", "Via", "Retry-After", "X-Served-By", "X-Cache")
            if headers.get(name)
        )
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(
            f"arXiv HTTP {error.code} at {timestamp}; URL={error.url}\n"
            f"Response headers: {details or '<none>'}\n"
            f"Response body (first 2000 bytes): {body}",
            file=sys.stderr,
        )
    finally:
        error.close()


def build_arxiv_request(url: str, params: dict[str, Any], use_post: bool = False) -> urllib.request.Request:
    encoded_params = urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/atom+xml",
        "User-Agent": ARXIV_USER_AGENT,
    }
    if use_post:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return urllib.request.Request(url, data=encoded_params.encode("ascii"), headers=headers, method="POST")
    return urllib.request.Request(f"{url}?{encoded_params}", headers=headers, method="GET")


def http_get_text(
    url: str,
    params: dict[str, Any],
    timeout: int = 120,
    max_retries: int = ARXIV_MAX_RETRIES,
) -> str:
    use_post = False
    for attempt in range(max_retries + 1):
        request = build_arxiv_request(url, params, use_post=use_post)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset)
        except urllib.error.HTTPError as error:
            log_http_error(error)
            # arXiv can return an empty 406 even for valid queries. Allow
            # bounded retries, but keep persistent rejection a visible failure.
            retryable = error.code in {406, 429} or 500 <= error.code < 600
            if not retryable or attempt >= max_retries:
                raise
            # A valid query can occasionally be rejected by an arXiv/Fastly
            # edge with an empty 406 response. Retrying the identical GET only
            # replays the rejected request, so switch to the API's supported
            # form-encoded POST transport for the remaining attempts.
            if error.code == 406:
                use_post = True
            delay = retry_delay_seconds(error, attempt)
            log_retry(f"HTTP {error.code}", attempt, max_retries, delay)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt >= max_retries:
                raise
            delay = min(ARXIV_RETRY_BASE_SECONDS * (2**attempt), ARXIV_RETRY_MAX_SECONDS)
            log_retry(type(error).__name__, attempt, max_retries, delay)
            time.sleep(delay)

    raise RuntimeError("arXiv request exhausted all retries")


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
    request_count = 0

    for category in config["categories"]:
        category_name = str(category.get("name", "")).strip()
        query = build_query([str(value) for value in category.get("filters", [])])
        if not category_name or not query:
            continue

        if request_count:
            print(f"Waiting {ARXIV_REQUEST_DELAY_SECONDS:g}s before fetching {category_name}...")
            time.sleep(ARXIV_REQUEST_DELAY_SECONDS)
        print(f"Fetching arXiv category {category_name}: {query}")
        papers = fetch_arxiv_papers(query, max_results)
        request_count += 1
        print(f"Fetched {len(papers)} papers for {category_name}")

        for paper in papers:
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
    category_sources = (
        existing.get("categories"),
        existing.get("category"),
        fetched.get("categories"),
        fetched.get("category"),
    )
    for source in category_sources:
        values = source if isinstance(source, (list, tuple)) else [source]
        for value in values:
            if value is None:
                continue
            clean = str(value).strip()
            # Older runs accidentally serialized a missing category as the
            # literal string "None". Drop that sentinel while merging so the
            # archive repairs itself on the next fetch.
            if clean and clean.casefold() != "none" and clean not in categories:
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
