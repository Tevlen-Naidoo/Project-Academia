# Project Academia

Recommends three academic papers a day: new and fast-rising work in computer science
(ML, AI, vision, image processing, NLP, graph theory, robotics, theory), with astronomy,
history, art and culture now and then. Uses only free metadata and abstracts from arXiv, Semantic Scholar,
OpenAlex, Hugging Face Daily Papers and Hacker News.

## Status

Steps 1 and 2 of 4: the paper fetcher and the scorer. Two funnels feed the daily candidates file:

- **Fresh:** arXiv papers from the last 7 days in the configured categories.
- **Making waves:** the most-cited papers of the last 18 months per topic, from OpenAlex,
  split into a 0 to 6 month and a 6 to 18 month window. Older work with a new publication
  date (a reprint, or the journal version of an old preprint) is left out.

Every candidate is enriched with citations, venue and published status from Semantic
Scholar and OpenAlex, then trimmed with a quota per topic and per funnel (up to 200 fresh
and 100 making-waves papers). Buzz comes from Hugging Face Daily Papers upvotes (last 7 days) and Hacker News stories
that link to a paper (last 30 days).

To try it, open **Actions → Fetch candidates (manual) → Run workflow**. The run fetches,
then scores, and attaches the candidates and picks files as an artifact. The log shows the
picks and the number of candidates per topic.

## Daily picks

`scorer/` picks 3 papers from the day's candidates and writes `data/picks/YYYY-MM-DD.json`,
which embeds each full paper with its score, score breakdown and a one-line reason.

- Each signal (buzz, publication status, citations per month, influential citations,
  recency) becomes a percentile, then the signals are weighted. New and making-waves papers
  have their own weights, and each funnel's scores are scaled so its best paper scores 1;
  the scores alone decide the mix. Citations are compared within
  a topic, so slow-citing fields can compete.
- The 3 picks come from different topics. On Monday, Wednesday and Friday one slot goes to
  a light topic (astronomy, history, art, culture).
- A topic picked on recent days scores a little lower each time, so one topic can't win
  every day.
- Papers on the reading board are never picked again. Other past picks can return after
  30 days.

Weights and rules live in `config/scoring.yaml`. No AI model is involved, and the same
inputs always give the same picks.

```sh
uv run python -m scorer run --date 2026-11-05   # reads data/candidates/2026-11-05.json
```

## Prerequisite reading

`reader/` finds what to read before each pick. For every pick it ranks the paper's
references (Semantic Scholar, falling back to OpenAlex) and keeps up to 2 **Read first**
papers (influential, cited for their method), 3 **Background** papers (at most one survey),
and for each Read first paper the **Foundation**: the lowest paper in its own chain of Read
first papers, at most 3 hops down. Papers shown in the last 30 days are skipped.

```sh
uv run python -m reader prereqs data/candidates/2026-11-05.json --ids arxiv:2410.12345
```

Without `--ids` it takes the top 3 candidates by the fetcher's rough order. Output goes to `data/prereqs/YYYY-MM-DD.json`.

## Reading board

`data/board.json` is your reading list, in three columns: To read, Reading and Done.
Papers only go on it when you add them; the daily picks are suggestions and never land
there by themselves. To read holds at most 10 papers. Every card keeps links to the
paper, and papers on the board (Done included) will be skipped by future picks.

```sh
uv run python -m reader board add 2410.12345          # or a DOI, or an arXiv / DOI / Semantic Scholar link
uv run python -m reader board add 2410.12345 --pick   # note it came from the daily picks
uv run python -m reader board start 2410.12345        # move to Reading
uv run python -m reader board done 2410.12345         # move to Done (stamps the date)
uv run python -m reader board later 2410.12345        # back to To read
uv run python -m reader board remove 2410.12345
uv run python -m reader board list
```

The commands edit `data/board.json`; commit it to keep the change. From a phone, use
**Actions → Update reading board → Run workflow**, which makes the change and commits it.

## Layout

| Path | What it is |
| --- | --- |
| `fetcher/models.py` | The `Paper` record and daily `CandidatesFile`, as Pydantic models |
| `fetcher/topics.py` | Loads `config/topics.yaml` and tags papers by topic |
| `config/topics.yaml` | Topics, their arXiv categories and OpenAlex subfields. Edit this to change topics |
| `scorer/` | The daily picks (`scorer/models.py` holds `Pick` and `PicksFile`) |
| `config/scoring.yaml` | Scoring weights, light days, topic spread and repeat rules |
| `reader/` | Prerequisite reading for the picks (`reader/models.py` holds `PaperRef` and `PrereqsFile`) and the reading board (`reader/board.py`) |
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
uv run python -m scorer run              # pick today's papers
uv run python -m scorer validate FILE    # check a picks file
```

## API keys (optional)

The fetcher works without keys. Adding them as repository secrets makes it faster and
more reliable:

- `S2_API_KEY`: free from the [Semantic Scholar API page](https://www.semanticscholar.org/product/api)
- `OPENALEX_API_KEY`: free from your account settings at [openalex.org](https://openalex.org)
