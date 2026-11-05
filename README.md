# Project Academia

Recommends three academic papers a day: new and fast-rising work in computer science
(ML, AI, vision, image processing, NLP, graph theory, robotics, theory), with astronomy,
history, art and culture now and then. Uses only free metadata and abstracts from arXiv, Semantic Scholar,
OpenAlex, Hugging Face Daily Papers and Hacker News.

## Status

All 4 steps are in place: the paper fetcher, the scorer, the web app and the daily run. Two funnels feed the daily candidates file:

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
first papers, at most 3 hops down. Background papers must share a field of study with
the pick (from Semantic Scholar or OpenAlex) unless Semantic Scholar says the pick builds on
them, so a highly cited paper from another field is not suggested on citations alone. Papers shown in the last 30 days are skipped.

```sh
uv run python -m reader prereqs                      # today's picks
uv run python -m reader prereqs data/candidates/2026-11-05.json --ids arxiv:2410.12345
```

With no file it reads today's picks from `data/picks/YYYY-MM-DD.json`, in rank order.
Given a candidates file instead, it takes the top 3 by the fetcher's rough order.
Output goes to `data/prereqs/YYYY-MM-DD.json`.

## Connected papers

The same run saves a citation graph per pick in `data/graphs/YYYY-MM-DD.json`: the pick,
its prerequisites, up to 15 other references and the 10 most-cited papers that cite it,
with the citations between all of them (one extra Semantic Scholar call per pick for the
citing papers, and one for the links between them). The site draws it on a "Connected
papers" page for each day, linked from every pick, with time running left to right.

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

## Web app

`site_builder/` turns the picks, prerequisites and reading board into a static site, which
GitHub Pages hosts. It has Today (the 3 picks, their abstracts, links and what to read
first), an Archive with topic, kind and title filters, a page per day, and the Reading
board.

Board buttons on the site save straight to `data/board.json` in this repository, so the
daily picks keep skipping papers on the board. That needs a GitHub key, pasted once per
device on the site's Settings page:

1. Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new).
2. Give it access to **only this repository**, with **Contents: Read and write**. Nothing else.
3. Paste it on the Settings page. It stays in that browser and is only sent to GitHub.

Every board save is a commit, which rebuilds the site. One-time setup: **Settings → Pages →
Source: GitHub Actions**.

```sh
uv run python -m site_builder build --serve   # build into site/ and preview on localhost:8000
```

## Daily run

**Actions → Daily papers** runs every day at 03:15 UTC (05:15 SAST), so the papers are up
by 07:00 SAST. It fetches the candidates, picks 3 papers, finds what to read first,
commits `data/picks/` and `data/prereqs/` for the day, and republishes the site. The
candidates file is only attached to the run, for 30 days. If a run fails, nothing is
committed and the site keeps the previous day's papers; GitHub emails you about the
failure. To fill in a missed day, run the workflow by hand with that date.

## Layout

| Path | What it is |
| --- | --- |
| `fetcher/models.py` | The `Paper` record and daily `CandidatesFile`, as Pydantic models |
| `fetcher/topics.py` | Loads `config/topics.yaml` and tags papers by topic |
| `config/topics.yaml` | Topics, their arXiv categories and OpenAlex subfields. Edit this to change topics |
| `scorer/` | The daily picks (`scorer/models.py` holds `Pick` and `PicksFile`) |
| `config/scoring.yaml` | Scoring weights, light days, topic spread and repeat rules |
| `site_builder/` | The static web app: templates, CSS and `board.js`, which saves the board |
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
