#!/usr/bin/env python3
"""Synchronize docs/paper_arxiv.json into the Notion Papers database."""
from __future__ import annotations

import argparse
from pathlib import Path

from notion_papers import (
    NotionClient,
    append_github_summary,
    notion_settings_from_env,
    parse_paper_records,
    read_paper_json,
    sync_records,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize arXiv paper JSON into Notion.")
    parser.add_argument("--input", default="docs/paper_arxiv.json", help="Paper JSON file to synchronize.")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing to Notion.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    records = parse_paper_records(read_paper_json(Path(args.input)))
    token, data_source_id = notion_settings_from_env()
    client = NotionClient(token)
    stats = sync_records(records, client, data_source_id, dry_run=args.dry_run)

    mode = "Dry run" if args.dry_run else "Sync complete"
    print(
        f"{mode}: created={stats.created}, updated={stats.updated}, skipped={stats.skipped}"
    )
    append_github_summary(
        "Notion paper sync",
        [
            ("Input papers", len(records)),
            ("Created", stats.created),
            ("Updated", stats.updated),
            ("Skipped", stats.skipped),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
