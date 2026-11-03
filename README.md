# Project Academia

Recommends three academic papers a day: new and fast-rising work in computer science
(ML, AI, vision, image processing, NLP, graph theory, robotics, theory), with astronomy
now and then. Uses only free metadata and abstracts from arXiv, Semantic Scholar,
OpenAlex, Hugging Face Daily Papers and Hacker News.

## Status

Step 1 of 4: the paper fetcher. The data model, topic mapping and arXiv source are in;
Semantic Scholar and OpenAlex enrichment, the "making waves" funnel and buzz signals come
next.

To try it, open **Actions → Fetch candidates (manual) → Run workflow**. The candidates
file is attached to the run as an artifact.

## Layout

| Path | What it is |
| --- | --- |
| `fetcher/models.py` | The `Paper` record and daily `CandidatesFile`, as Pydantic models |
| `fetcher/topics.py` | Loads `config/topics.yaml` and tags papers by topic |
| `config/topics.yaml` | Topics, their arXiv categories and OpenAlex subfields. Edit this to change topics |
| `schema/` | JSON Schemas generated from the models, read by the web app |
| `data/` | Daily output: `candidates/`, `picks/`, `runs/` |

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```sh
uv sync
uv run pytest            # tests
uv run mypy              # strict type check
uv run ruff check .      # lint
uv run python -m fetcher run             # fetch today's candidates into data/
uv run python -m fetcher topics          # show the topic mapping
uv run python -m fetcher schema          # regenerate schema/ after changing models
uv run python -m fetcher validate FILE   # check a candidates file
```

## API keys (optional)

The fetcher works without keys. Adding them as repository secrets makes it faster and
more reliable:

- `S2_API_KEY`: free from the [Semantic Scholar API page](https://www.semanticscholar.org/product/api)
- `OPENALEX_API_KEY`: free from your account settings at [openalex.org](https://openalex.org)
