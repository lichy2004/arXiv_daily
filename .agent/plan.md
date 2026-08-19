# arXiv Daily Implementation Plan

## Goal

Maintain one clear workflow:

```text
arXiv → GitHub JSON → Notion review → saved JSON → GitHub
```

Notion is the only review interface. The repository keeps fetched metadata and generated saved-paper exports.

## Implemented structure

### GitHub → Notion

- `fetch_arxiv.py` fetches configured keyword groups and updates `docs/paper_arxiv.json`.
- `.github/workflows/daily_arxiv.yml` runs daily at 00:00 UTC, commits the archive, and calls `sync_to_notion.py`.
- `sync_to_notion.py` upserts by normalized `Arxiv ID`.
- New records use `Status = Unread`.
- User-owned Notion fields are protected from later fetches.

### Notion

- `arXiv Paper` is the root page.
- `Papers` is the only data source.
- `Review Papers` is a separate page with an `All Papers` linked view.
- `Saved Papers` is a separate page with `Table` and `Gallery` views filtered to `Status = Save`.
- Manual non-arXiv papers leave `Arxiv ID` empty and are ignored by arXiv synchronization.

### Notion → GitHub

- `export_notion_saved.py` exports all `Save` records to `docs/paper_save.json`.
- arXiv records use their arXiv ID as the JSON key.
- Manual records use a stable `notion:<page-id>` key.
- `.github/workflows/export_notion_saved.yml` runs daily at 00:30 UTC and supports manual runs.
- Export writes atomically, so a failed query does not replace the previous valid file.

## Data ownership

| Data | Owner |
| --- | --- |
| Fetched title, authors, paper link, date, abstract | GitHub/arXiv sync |
| Status, Category, Notes, Cover Image | Notion user |
| Web Link, Code Link | Notion user; sync only fills empty values |
| `paper_save.json` | Generated from Notion |

## Removed legacy paths

- Browser-local review UI and Jekyll configuration.
- Local JSON import/export helper scripts.
- Old GitHub Pages setup screenshots.
- Unused code-link discovery and third-party dependency installation.

These paths duplicated Notion state or were not used by the active automation.

## Remaining external setup

- Create a Notion internal integration.
- Share the `Papers` database with it.
- Add its token to GitHub Actions as `NOTION_TOKEN`.
- Confirm GitHub Actions has repository write permission.

## Acceptance checks

- [x] Fetch output is valid JSON and bounded by `max_items`.
- [x] Multi-category matches are retained; abstracts are excluded from the arXiv archive.
- [x] Repeated Notion sync does not duplicate arXiv papers.
- [x] Manual non-arXiv records are not overwritten.
- [x] `Read` and `Save` are not overwritten by synchronization.
- [x] Saved arXiv and manual papers can be exported.
- [x] Saved output is atomically replaced.
- [ ] GitHub workflows have been run with the repository `NOTION_TOKEN`.
