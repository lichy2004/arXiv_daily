#!/usr/bin/env python3
"""Shared Notion API helpers for the arXiv paper workflows."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
DEFAULT_DATA_SOURCE_ID = "4334d614-6676-4d47-acaa-a6637ecec9a5"

PROPERTY_PAPER = "Paper"
PROPERTY_ARXIV_ID = "Arxiv ID"
PROPERTY_AUTHORS = "Authors"
PROPERTY_PAPER_LINK = "Paper Link"
PROPERTY_STATUS = "Status"
PROPERTY_WEB_LINK = "Web Link"
PROPERTY_CODE_LINK = "Code Link"
PROPERTY_CATEGORY = "Category"
PROPERTY_COVER_IMAGE = "Cover Image"
PROPERTY_PUBLISHED_DATE = "Published Date"
PROPERTY_ABSTRACT = "Abstract"
PROPERTY_NOTES = "Notes"
PROPERTY_IMPORTED_AT = "Imported At"
PROPERTY_SOURCE_UPDATED_AT = "Source Updated At"

STATUS_UNREAD = "Unread"
STATUS_READ = "Read"
STATUS_SAVE = "Save"


class NotionAPIError(RuntimeError):
    """Raised when a Notion API request cannot be completed."""


class PaperDataError(ValueError):
    """Raised when paper data is invalid or ambiguous."""


@dataclass(frozen=True)
class PaperRecord:
    arxiv_id: str
    paper_name: str
    paper_link: str
    authors: tuple[str, ...]
    categories: tuple[str, ...]
    published_date: str
    abstract: str
    web_link: str
    code_link: str


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0


class NotionClient:
    """Small standard-library client for the Notion endpoints used here."""

    def __init__(
        self,
        token: str,
        *,
        version: str = NOTION_VERSION,
        base_url: str = NOTION_API_BASE,
        timeout: int = 30,
        max_retries: int = 5,
    ) -> None:
        if not token:
            raise ValueError("A Notion token is required.")
        self.token = token
        self.version = version
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Notion-Version": self.version,
                "User-Agent": "arXiv-daily-notion-sync/1.0",
            },
        )

        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as error:
                raw = error.read().decode("utf-8", errors="replace")
                retryable = error.code == 429 or 500 <= error.code < 600
                if retryable and attempt < self.max_retries:
                    retry_after = error.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(2**attempt, 30)
                    time.sleep(max(delay, 0))
                    continue
                try:
                    detail = json.loads(raw).get("message", raw)
                except json.JSONDecodeError:
                    detail = raw
                raise NotionAPIError(f"Notion API {error.code}: {detail}") from error
            except urllib.error.URLError as error:
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise NotionAPIError(f"Notion API network error: {error.reason}") from error

        raise NotionAPIError("Notion API request exhausted all retries.")

    def query_data_source(
        self,
        data_source_id: str,
        *,
        filter_: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if filter_:
                payload["filter"] = filter_
            if cursor:
                payload["start_cursor"] = cursor
            response = self._request("POST", f"/data_sources/{data_source_id}/query", payload)
            page_results = response.get("results", [])
            if not isinstance(page_results, list):
                raise NotionAPIError("Notion returned an invalid data source response.")
            results.extend(page_results)
            if not response.get("has_more"):
                return results
            cursor = response.get("next_cursor")
            if not cursor:
                raise NotionAPIError("Notion reported more results without a cursor.")

    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/pages",
            {
                "parent": {"type": "data_source_id", "data_source_id": data_source_id},
                "properties": properties,
            },
        )

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/pages/{page_id}", {"properties": properties})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_paper_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Paper JSON does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PaperDataError(f"Expected a JSON object in {path}")
    return data


def normalize_arxiv_id(value: str) -> str:
    candidate = str(value or "").strip()
    candidate = re.sub(r"[?#].*$", "", candidate)
    candidate = re.sub(r"\.pdf$", "", candidate, flags=re.IGNORECASE)
    if "/abs/" in candidate or "/pdf/" in candidate:
        candidate = re.split(r"/(?:abs|pdf)/", candidate, maxsplit=1)[-1]
    candidate = candidate.strip("/")
    candidate = re.sub(r"v\d+$", "", candidate, flags=re.IGNORECASE)
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})", candidate):
        raise PaperDataError(f"Invalid arXiv ID: {value!r}")
    return candidate


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Iterable[Any] = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    cleaned = [_clean_string(item) for item in items]
    return tuple(dict.fromkeys(item for item in cleaned if item))


def parse_paper_record(raw_id: str, raw: Any) -> PaperRecord:
    if not isinstance(raw, dict):
        raise PaperDataError(f"Paper {raw_id!r} must be a JSON object.")
    arxiv_id = normalize_arxiv_id(raw_id or raw.get("paper_link", ""))
    paper_name = _clean_string(raw.get("paper_name"))
    if not paper_name:
        raise PaperDataError(f"Paper {arxiv_id} is missing paper_name.")

    categories = _clean_list(raw.get("categories"))
    if not categories:
        categories = _clean_list(raw.get("category"))

    published_date = _clean_string(raw.get("published_date"))[:10]
    if published_date:
        try:
            datetime.strptime(published_date, "%Y-%m-%d")
        except ValueError as error:
            raise PaperDataError(f"Paper {arxiv_id} has an invalid published_date.") from error

    return PaperRecord(
        arxiv_id=arxiv_id,
        paper_name=paper_name,
        paper_link=_clean_string(raw.get("paper_link")) or f"https://arxiv.org/abs/{arxiv_id}",
        authors=_clean_list(raw.get("authors")),
        categories=categories,
        published_date=published_date,
        abstract=_clean_string(raw.get("abstract")),
        web_link=_clean_string(raw.get("web_link")),
        code_link=_clean_string(raw.get("code_link")),
    )


def parse_paper_records(data: dict[str, Any]) -> list[PaperRecord]:
    records: list[PaperRecord] = []
    seen: set[str] = set()
    for raw_id, raw in data.items():
        record = parse_paper_record(raw_id, raw)
        if record.arxiv_id in seen:
            raise PaperDataError(f"Duplicate normalized arXiv ID in JSON: {record.arxiv_id}")
        seen.add(record.arxiv_id)
        records.append(record)
    return records


def _text_parts(value: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": {"content": value[index : index + 2000]}}
        for index in range(0, len(value), 2000)
    ]


def title_property(value: str) -> dict[str, Any]:
    return {"type": "title", "title": _text_parts(value)}


def rich_text_property(value: str) -> dict[str, Any]:
    return {"type": "rich_text", "rich_text": _text_parts(value)}


def url_property(value: str) -> dict[str, Any]:
    return {"type": "url", "url": value or None}


def date_property(value: str) -> dict[str, Any]:
    return {"type": "date", "date": {"start": value} if value else None}


def select_property(value: str) -> dict[str, Any]:
    return {"type": "select", "select": {"name": value} if value else None}


def multi_select_property(values: Iterable[str]) -> dict[str, Any]:
    return {"type": "multi_select", "multi_select": [{"name": value} for value in values]}


def build_create_properties(record: PaperRecord, now: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        PROPERTY_PAPER: title_property(record.paper_name),
        PROPERTY_ARXIV_ID: rich_text_property(record.arxiv_id),
        PROPERTY_AUTHORS: rich_text_property(", ".join(record.authors)),
        PROPERTY_PAPER_LINK: url_property(record.paper_link),
        PROPERTY_STATUS: select_property(STATUS_UNREAD),
        PROPERTY_CATEGORY: multi_select_property(record.categories),
        PROPERTY_IMPORTED_AT: date_property(now),
        PROPERTY_SOURCE_UPDATED_AT: date_property(now),
    }
    if record.published_date:
        properties[PROPERTY_PUBLISHED_DATE] = date_property(record.published_date)
    if record.abstract:
        properties[PROPERTY_ABSTRACT] = rich_text_property(record.abstract)
    if record.web_link:
        properties[PROPERTY_WEB_LINK] = url_property(record.web_link)
    if record.code_link:
        properties[PROPERTY_CODE_LINK] = url_property(record.code_link)
    return properties


def page_property(page: dict[str, Any], name: str) -> dict[str, Any]:
    properties = page.get("properties") or {}
    value = properties.get(name) or {}
    return value if isinstance(value, dict) else {}


def property_text(page: dict[str, Any], name: str) -> str:
    prop = page_property(page, name)
    items = prop.get("title") if prop.get("type") == "title" else prop.get("rich_text")
    if not isinstance(items, list):
        return ""
    chunks: list[str] = []
    for item in items:
        if item.get("plain_text") is not None:
            chunks.append(str(item["plain_text"]))
        else:
            chunks.append(str((item.get("text") or {}).get("content") or ""))
    return "".join(chunks)


def property_url(page: dict[str, Any], name: str) -> str:
    return _clean_string(page_property(page, name).get("url"))


def property_date(page: dict[str, Any], name: str) -> str:
    date_value = page_property(page, name).get("date") or {}
    return _clean_string(date_value.get("start"))


def property_select(page: dict[str, Any], name: str) -> str:
    select_value = page_property(page, name).get("select") or {}
    return _clean_string(select_value.get("name"))


def property_multi_select(page: dict[str, Any], name: str) -> tuple[str, ...]:
    values = page_property(page, name).get("multi_select") or []
    return tuple(_clean_string(value.get("name")) for value in values if _clean_string(value.get("name")))


def build_update_properties(record: PaperRecord, page: dict[str, Any], now: str) -> dict[str, Any]:
    updates: dict[str, Any] = {}

    def update_text(name: str, desired: str, *, title: bool = False) -> None:
        if desired and property_text(page, name) != desired:
            updates[name] = title_property(desired) if title else rich_text_property(desired)

    def update_url(name: str, desired: str, *, only_when_empty: bool = False) -> None:
        current = property_url(page, name)
        if desired and current != desired and (not only_when_empty or not current):
            updates[name] = url_property(desired)

    update_text(PROPERTY_PAPER, record.paper_name, title=True)
    update_text(PROPERTY_ARXIV_ID, record.arxiv_id)
    update_text(PROPERTY_AUTHORS, ", ".join(record.authors))
    update_url(PROPERTY_PAPER_LINK, record.paper_link)
    if record.published_date and property_date(page, PROPERTY_PUBLISHED_DATE)[:10] != record.published_date:
        updates[PROPERTY_PUBLISHED_DATE] = date_property(record.published_date)
    update_text(PROPERTY_ABSTRACT, record.abstract)
    update_url(PROPERTY_WEB_LINK, record.web_link, only_when_empty=True)
    update_url(PROPERTY_CODE_LINK, record.code_link, only_when_empty=True)

    # Category, Status, Notes, and Cover Image are user-owned after creation.
    if updates:
        updates[PROPERTY_SOURCE_UPDATED_AT] = date_property(now)
    return updates


def index_notion_pages(
    pages: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: dict[str, list[str]] = {}

    for page in pages:
        raw_id = property_text(page, PROPERTY_ARXIV_ID).strip()
        if not raw_id:
            continue
        try:
            arxiv_id = normalize_arxiv_id(raw_id)
        except PaperDataError as error:
            raise PaperDataError(f"Notion page {page.get('id')} has {error}") from error
        if arxiv_id in indexed:
            duplicates.setdefault(arxiv_id, [str(indexed[arxiv_id].get("id"))]).append(str(page.get("id")))
        else:
            indexed[arxiv_id] = page

    if duplicates:
        details = "; ".join(f"{paper_id}: {', '.join(page_ids)}" for paper_id, page_ids in duplicates.items())
        raise PaperDataError(f"Duplicate arXiv IDs in Notion: {details}")
    return indexed


def sync_records(
    records: Iterable[PaperRecord],
    client: NotionClient,
    data_source_id: str,
    *,
    now: str | None = None,
    dry_run: bool = False,
) -> SyncStats:
    timestamp = now or utc_now()
    pages = client.query_data_source(data_source_id)
    indexed = index_notion_pages(pages)
    stats = SyncStats()

    for record in records:
        existing = indexed.get(record.arxiv_id)
        if existing:
            updates = build_update_properties(record, existing, timestamp)
            if updates:
                if not dry_run:
                    client.update_page(str(existing["id"]), updates)
                stats.updated += 1
            else:
                stats.skipped += 1
            continue

        if not dry_run:
            client.create_page(data_source_id, build_create_properties(record, timestamp))
        stats.created += 1

    return stats


def notion_settings_from_env() -> tuple[str, str]:
    token = os.getenv("NOTION_TOKEN", "").strip()
    data_source_id = os.getenv("NOTION_DATA_SOURCE_ID", DEFAULT_DATA_SOURCE_ID).strip()
    if not token:
        raise RuntimeError("NOTION_TOKEN is not set. Add it as a GitHub Actions secret or environment variable.")
    if not data_source_id:
        raise RuntimeError("NOTION_DATA_SOURCE_ID is not set.")
    return token, data_source_id


def append_github_summary(title: str, rows: Iterable[tuple[str, Any]]) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(f"## {title}\n\n")
        handle.write("| Metric | Count |\n| --- | ---: |\n")
        for label, value in rows:
            handle.write(f"| {label} | {value} |\n")
        handle.write("\n")
