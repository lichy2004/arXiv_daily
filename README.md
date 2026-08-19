# arXiv Daily

Fetch papers from arXiv every day, review them in Notion, and export saved papers back to GitHub.

## Workflow

```text
arXiv API
  → docs/paper_arxiv.json
  → Notion Papers database
  → Review Papers: Unread / Read / Save
  → Saved Papers: Table / Gallery
  → docs/paper_save.json
```

GitHub owns fetched arXiv metadata. Notion owns review status, notes, manual links, categories, and cover images. `paper_save.json` is a generated export and should not be edited manually.

## Notion structure

The [arXiv Paper](https://app.notion.com/p/3c138e7ccd39802a8afdd62b6e4bdc26) page contains:

- `Papers`: the single underlying data source.
- [Review Papers](https://app.notion.com/p/3c138e7ccd3981ba8e6eeea6d03944d0): a page with the `All Papers` table showing `Paper`, `Category`, `Paper Link`, and `Status`. `Authors` remains in the underlying data source but is hidden from this review view.
- [Saved Papers](https://app.notion.com/p/3c138e7ccd398167be89d00c3f308691): a page with `Table` and `Gallery` views filtered to `Status = Save`.

All three interfaces reference the same records; changing `Status` never creates a duplicate.

Status values:

- `Unread`: waiting for review.
- `Read`: reviewed but not retained.
- `Save`: retained and included in the saved views and GitHub export.

### Add a paper manually

Add both arXiv and non-arXiv papers from `Review Papers`:

1. Create a row and fill `Paper` immediately.
2. Fill `Paper Link`, `Category`, and other useful fields. `Authors` is optional and can be edited from the paper page even though it is hidden from the review table.
3. Use `Unread`, `Read`, or `Save` as needed.
4. For a paper without an arXiv version, leave `Arxiv ID` empty. Do not put a DOI or OpenReview ID in that field.

Manual records are not changed by the arXiv sync. A saved manual paper is exported with a stable `notion:<page-id>` key; an arXiv paper uses its versionless arXiv ID.

## Repository files

| File | Purpose |
| --- | --- |
| `config.json` | Keywords, categories, result limit, and archive size |
| `fetch_arxiv.py` | Fetch and merge arXiv metadata |
| `notion_papers.py` | Shared Notion API and mapping logic |
| `sync_to_notion.py` | Upsert fetched arXiv papers into Notion |
| `export_notion_saved.py` | Export `Status = Save` records |
| `docs/paper_arxiv.json` | Versioned arXiv archive |
| `docs/paper_save.json` | Versioned saved-paper export |

The project uses Python 3.11+ and only the standard library.

## Configuration

Edit `config.json`:

```json
{
  "output_path": "docs/paper_arxiv.json",
  "max_results_per_category": 25,
  "max_items": 1000,
  "categories": [
    {"name": "Agent", "filters": ["self-improv", "embodied agent"]},
    {"name": "Dexterous", "filters": ["Dexterous Manipulation", "Dexterity"]}
  ]
}
```

Filters within one category are joined with `OR`. A paper matching multiple categories keeps all matching category names.

## Local commands

Fetch papers:

```bash
python fetch_arxiv.py
```

Preview or run the Notion sync:

```bash
NOTION_TOKEN=secret_xxx python sync_to_notion.py --dry-run
NOTION_TOKEN=secret_xxx python sync_to_notion.py
```

Export saved papers:

```bash
NOTION_TOKEN=secret_xxx python export_notion_saved.py
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

## GitHub Actions

- `Run arXiv Papers Daily`: runs at 00:00 UTC, commits `paper_arxiv.json`, then syncs it to Notion.
- `Export Saved Notion Papers`: runs at 00:30 UTC and can be triggered manually; it commits `paper_save.json` only when content changes.

Required setup:

1. Create a Notion internal integration with read, insert, and update content access.
2. Share the `Papers` database with the integration.
3. Add its token as the repository Actions secret `NOTION_TOKEN`.
4. Give GitHub Actions read/write repository permission.

The data source ID is already configured in both workflows. Never commit the Notion token or expose it in client-side code.

## Synchronization rules

- arXiv IDs are normalized without version suffixes and used for deduplication.
- New fetched papers start as `Unread`.
- Fetched title, authors, paper link, and date may be refreshed. Abstracts are not stored in `paper_arxiv.json`.
- `Status`, `Category`, `Notes`, and `Cover Image` are never overwritten after creation.
- `Web Link` and `Code Link` are filled only when the Notion field is empty.
- Manual records with an empty `Arxiv ID` are ignored by the arXiv sync.
- `Read` changes stay in Notion. `Save` records are exported on the next saved-paper workflow run.
