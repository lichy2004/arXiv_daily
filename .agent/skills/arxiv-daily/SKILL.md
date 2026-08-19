---
name: arxiv-daily
description: Maintain this repository's arXiv-to-Notion paper workflow. Use when changing paper keywords, fetch behavior, Notion synchronization, saved-paper export, related GitHub Actions, or explaining this project's paper data flow.
---

# arXiv Daily

Keep one workflow and one review state:

```text
arXiv → docs/paper_arxiv.json → Notion → docs/paper_save.json
```

Read `README.md` before changing behavior. Use English for all Notion page, view, property, status, and instructional content.

## Source of truth

- arXiv/GitHub owns fetched metadata.
- Notion owns `Status`, `Category`, `Notes`, `Cover Image`, and manual records.
- `docs/paper_save.json` is generated from Notion; never treat it as editable source data.
- Do not add a browser-local review state or expose tokens in frontend code.

## Active files

- `config.json`: keyword groups and archive limits.
- `fetch_arxiv.py`: standard-library arXiv fetcher.
- `notion_papers.py`: Notion API, mapping, and upsert helpers.
- `sync_to_notion.py`: GitHub JSON → Notion.
- `export_notion_saved.py`: Notion `Save` records → saved JSON.
- `.github/workflows/daily_arxiv.yml`: daily fetch and Notion sync.
- `.github/workflows/export_notion_saved.yml`: saved-paper export.
- `tests/`: behavior tests.

## Required behavior

### Fetch

- Build each category query from its `filters`, joined with `OR`.
- Normalize arXiv IDs by removing version suffixes.
- Do not store abstracts in `docs/paper_arxiv.json`; preserve all matching configured categories.
- Merge into `docs/paper_arxiv.json` and trim the oldest records beyond `max_items`.
- Keep the implementation on the Python standard library unless a real requirement justifies a dependency.

### Notion sync

- Upsert arXiv records by `Arxiv ID`; fail on duplicate valid IDs.
- Create new records with `Status = Unread`.
- Never overwrite `Status`, `Category`, `Notes`, or `Cover Image` after creation.
- Fill `Web Link` and `Code Link` only when their Notion values are empty.
- Ignore manual records whose `Arxiv ID` is empty.
- Do not reuse blank Notion rows or delete records automatically.

### Manual papers

- Add them through `Review Papers` and fill `Paper` immediately.
- Leave `Arxiv ID` empty when no arXiv version exists; do not store DOI or OpenReview IDs there.
- A manual `Save` record exports with `notion:<page-id>` as its stable key.

### Saved export

- Export only `Status = Save` records.
- Use the normalized arXiv ID for arXiv records and the Notion page ID for manual records.
- Write JSON atomically and preserve the previous file if querying or serialization fails.

## Configuration and validation

The workflows require the `NOTION_TOKEN` GitHub Actions secret. The data source ID is configured in the workflow files and defaults in `notion_papers.py`.

After changes, run:

```bash
python -m unittest discover -s tests -v
python -m py_compile fetch_arxiv.py notion_papers.py sync_to_notion.py export_notion_saved.py
python -m json.tool config.json >/dev/null
python -m json.tool docs/paper_arxiv.json >/dev/null
python -m json.tool docs/paper_save.json >/dev/null
git diff --check
```

Do not call live Notion or arXiv services during ordinary validation unless the user requests it or the change requires an integration check.
