#!/usr/bin/env python3
"""Export Notion papers with Status=Save to docs/paper_save.json."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from notion_papers import (
    PROPERTY_ABSTRACT,
    PROPERTY_ARXIV_ID,
    PROPERTY_AUTHORS,
    PROPERTY_CATEGORY,
    PROPERTY_CODE_LINK,
    PROPERTY_NOTES,
    PROPERTY_PAPER,
    PROPERTY_PAPER_LINK,
    PROPERTY_PUBLISHED_DATE,
    PROPERTY_SOURCE_UPDATED_AT,
    PROPERTY_STATUS,
    PROPERTY_WEB_LINK,
    STATUS_SAVE,
    NotionClient,
    PaperDataError,
    append_github_summary,
    normalize_arxiv_id,
    notion_settings_from_env,
    property_date,
    property_multi_select,
    property_select,
    property_text,
    property_url,
)


def authors_from_text(value: str) -> list[str]:
    return [author.strip() for author in value.split(",") if author.strip()]


def page_to_saved_record(page: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_id = property_text(page, PROPERTY_ARXIV_ID)
    paper_name = property_text(page, PROPERTY_PAPER)
    if not paper_name:
        raise PaperDataError(f"Saved Notion page {page.get('id')} has no Paper title.")

    if raw_id:
        arxiv_id = normalize_arxiv_id(raw_id)
        record_key = arxiv_id
        source = "arxiv"
    else:
        page_id = str(page.get("id") or "").replace("-", "")
        if not page_id:
            raise PaperDataError("A saved manual paper has no Notion page ID.")
        arxiv_id = ""
        record_key = f"notion:{page_id}"
        source = "manual"

    categories = list(property_multi_select(page, PROPERTY_CATEGORY))
    updated_at = property_date(page, PROPERTY_SOURCE_UPDATED_AT) or str(page.get("last_edited_time") or "")
    record = {
        "source": source,
        "arxiv_id": arxiv_id,
        "paper_name": paper_name,
        "paper_link": property_url(page, PROPERTY_PAPER_LINK),
        "authors": authors_from_text(property_text(page, PROPERTY_AUTHORS)),
        "published_date": property_date(page, PROPERTY_PUBLISHED_DATE)[:10],
        "categories": categories,
        "category": categories[0] if categories else "",
        "abstract": property_text(page, PROPERTY_ABSTRACT),
        "web_link": property_url(page, PROPERTY_WEB_LINK),
        "code_link": property_url(page, PROPERTY_CODE_LINK),
        "notes": property_text(page, PROPERTY_NOTES),
        "updated_at": updated_at,
    }
    return record_key, record


def build_saved_export(pages: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for page in pages:
        if property_select(page, PROPERTY_STATUS) != STATUS_SAVE:
            continue
        record_key, record = page_to_saved_record(page)
        if record_key in result:
            raise PaperDataError(f"Duplicate saved paper key in Notion: {record_key}")
        result[record_key] = record
    return {paper_id: result[paper_id] for paper_id in sorted(result)}


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export saved Notion papers to JSON.")
    parser.add_argument("--output", default="docs/paper_save.json", help="Destination JSON file.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token, data_source_id = notion_settings_from_env()
    client = NotionClient(token)
    pages = client.query_data_source(
        data_source_id,
        filter_={"property": PROPERTY_STATUS, "select": {"equals": STATUS_SAVE}},
    )
    saved = build_saved_export(pages)
    atomic_write_json(Path(args.output), saved)
    print(f"Exported {len(saved)} saved papers to {args.output}")
    append_github_summary("Notion saved-paper export", [("Saved papers", len(saved))])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
