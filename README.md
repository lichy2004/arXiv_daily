# arXiv_daily

A small utility for fetching arXiv papers and saving them as JSON.

## What it does

The script fetches papers from arXiv by category keywords and stores them in a JSON file keyed by paper id.

Each record contains:

- `paper_name`
- `paper_link`
- `code_link`
- `authors`
- `category`

## Output format

The generated JSON looks like this:

```json
{
  "2608.14530": {
    "paper_name": "Marionette: Predicting World States, Rendering Geometry, Painting Appearance",
    "paper_link": "https://arxiv.org/abs/2608.14530",
    "code_link": "https://github.com/example/repo",
    "authors": ["Zian Meng", "..."],
    "category": "Robot & Agent"
  }
}
```

## Installation

Install Python dependencies:

```bash
pip install arxiv requests
```

If you want faster GitHub code-link lookup and better rate limits, set `GITHUB_TOKEN` in your environment.

## Configuration

Edit `config.json` to control:

- `output_path`: where the JSON file is written
- `max_results_per_category`: number of papers fetched per category
- `max_items`: maximum number of papers to keep in `papers.json`; the oldest papers by `published_date` are trimmed when this limit is exceeded
- `include_code_link`: whether to search for a code link
- `categories`: category names and arXiv keyword filters

## Usage

Run the fetcher from the `arXiv_daily` folder:

```bash
python fetch_arxiv.py
```

Use a custom config file:

```bash
python fetch_arxiv.py --config config.json
```

Write to a custom output path:

```bash
python fetch_arxiv.py --output papers.json
```

Skip code-link lookup if you only want paper metadata:

```bash
python fetch_arxiv.py --skip-code-link
```

## Notes

- The script merges new results into the existing output JSON if the file already exists.
- Duplicate papers are deduplicated by paper id.
- Code links are best-effort and may be `null` if no repository is found.
