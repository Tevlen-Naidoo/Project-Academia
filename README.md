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

The reading board is your reading list, in three columns: To read, Reading and Done.
Papers only go on it when you add them; the daily picks are suggestions and never land
there by themselves. To read holds at most 10 papers. Every card keeps links to the
paper, and papers on the board (Done included) are skipped by future picks.

The board lives in your Firestore database (see [Database](#database)), not in this
repository, so changing it never makes a commit. Use it on the site, or from a terminal:

```sh
uv run python -m reader board add 2410.12345          # or a DOI, or an arXiv / DOI / Semantic Scholar link
uv run python -m reader board add 2410.12345 --pick   # note it came from the daily picks
uv run python -m reader board start 2410.12345        # move to Reading
uv run python -m reader board done 2410.12345         # move to Done (stamps the date)
uv run python -m reader board later 2410.12345        # back to To read
uv run python -m reader board remove 2410.12345
uv run python -m reader board list
```

The commands use Firestore when `FIREBASE_SERVICE_ACCOUNT` holds the service account key,
and `data/board.json` otherwise (handy for trying things locally). `--board FILE` uses a
file of your choice.

## Database

The board and the notification devices are stored in [Cloud Firestore](https://firebase.google.com/docs/firestore)
on Firebase's free plan. The site stays static: the browser signs in with Firebase and talks
to Firestore directly, and the [security rules](firestore.rules) let only your account read
or write. The daily run reads the board (to skip papers on it) and the devices (to send
notifications) with a service account key.

| Document | What it holds |
| --- | --- |
| `boards/main` | The board, in the same shape as `reader.board.Board` (`schema/board.schema.json`) |
| `devices/<sha256 of endpoint>` | One notification device each |
| `owners/<account id>` | The accounts allowed in. Only the service account can change this |

One-time setup (about 10 minutes, no card needed):

1. At [console.firebase.google.com](https://console.firebase.google.com), **Create a project**
   (Google Analytics is not needed).
2. **Build → Firestore Database → Create database**, in production mode, in a nearby region.
   Open its **Rules** tab, paste [`firestore.rules`](firestore.rules) and **Publish**.
3. **Build → Authentication → Get started**. Turn on **Google** (and **Email/Password** if you
   want it for the Home Screen app). Under **Settings → Authorised domains**, add
   `tevlen-naidoo.github.io`.
4. **Project settings → General → Your apps → Web (`</>`)**, register an app (no hosting) and
   copy the `firebaseConfig` object. In GitHub, **Settings → Secrets and variables → Actions →
   Variables → New repository variable**, name it `FIREBASE_CONFIG` and paste it. It is
   public by design; the rules protect the data.
5. **Project settings → Service accounts → Generate new private key**. In GitHub, add a
   repository **secret** named `FIREBASE_SERVICE_ACCOUNT` with the whole file's contents, then
   delete the file.
6. Run **Actions → Publish site**, open the site's **Settings**, and sign in. It shows your
   account id.
7. Run **Actions → Set up database** with that id. It lets your account in, copies the
   current board and devices into Firestore, and removes `data/board.json` and
   `data/push/subscriptions.json` from the repository.

Until step 7, the daily run keeps using the files in `data/`.

## Web app

`site_builder/` turns the picks and prerequisites into a static site, which GitHub Pages
hosts, with the reading board loaded live from the database once you sign in. It has Today (the 3 picks, their abstracts, links and what to read
first), an Archive with topic, kind and title filters, a page per day, and the Reading
board, where cards move between columns by dragging them (press and hold first on a touch
screen) or with their buttons.

Every part of a page is a web component built with [Lit](https://lit.dev), written in
TypeScript in `web/src/components/`. Vite bundles them, with Lit and the Firebase SDK from
npm, into `web/dist/`, and the Python builder (`site_builder/`) writes the pages and copies
that bundle into `site/static/`. The site GitHub Pages serves is still plain static files.
The builder hands each component its data as a JSON attribute (`site_builder/views.py`;
the matching TypeScript types are in `web/src/lib/types.ts`).

Board buttons on the site save straight to the database, after you sign in once per device
on the Settings page. One-time setup: **Settings → Pages → Source: GitHub Actions**, plus the
[Database](#database) steps.

### Working on the site

You need [Node.js](https://nodejs.org) 22 or later as well as uv. Once:

```sh
npm install
```

Then:

```sh
npm run dev       # live preview on http://localhost:5173
```

The dev server builds the pages from `data/` with the Python builder and serves them with
the TypeScript sources. Saving a stylesheet updates the open page without a reload; saving
a component, a template in `site_builder/templates/` or anything in `data/` reloads it.

Styles live in `web/src/styles/`:

| File | What it styles |
| --- | --- |
| `theme.css` | Colours (light and dark), fonts, text sizes, corner radii and page widths, as CSS variables. Start here. |
| `base.css` | The page frame, headings, links, buttons, badges and cards |
| `layout.css`, `papers.css`, `archive.css`, `board.css`, `graph.css`, `settings.css` | One file per component file in `web/src/components/` |

Components render into the page (not a shadow root), so these stylesheets reach everything.

```sh
npm run typecheck # check the TypeScript
npm run preview   # build exactly what gets published into site/ and serve it on localhost:8000
```

## Daily run

**Actions → Daily papers** runs every day at 03:15 UTC (05:15 SAST), so the papers are up
by 07:00 SAST. It fetches the candidates, picks 3 papers, finds what to read first,
commits `data/picks/` and `data/prereqs/` for the day, and republishes the site. The
candidates file is only attached to the run, for 30 days. If a run fails, nothing is
committed and the site keeps the previous day's papers; GitHub emails you about the
failure. To fill in a missed day, run the workflow by hand with that date.

## Notifications

After each daily run has published the site, **Actions → Send notifications** pushes
"3 new papers for today" with the three titles to every device that turned notifications
on. Tapping it opens Today. It uses Web Push straight from the site (no app or outside
service), so the site can be added to the Home Screen as an app.

One-time setup:

1. Make a key on your computer, in this repository: `uv run python -m notifier keys`
   (or `openssl ecparam -name prime256v1 -genkey -noout` and copy all of its output).
2. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**,
   name it `VAPID_PRIVATE_KEY` and paste the private key. Keep it nowhere else.
3. Run **Actions → Publish site → Run workflow**, so the site learns the matching public key.

On each iPhone or iPad (iOS 16.4 or later):

1. Open the site in Safari, tap **Share → Add to Home Screen**, and open **Academia** from
   the Home Screen.
2. Go to **Settings**, sign in again (the Home Screen app keeps its own sign-in), then tap
   **Turn on notifications** and allow them.

On a computer or Android phone, step 2 works straight from the browser. To check it all,
run **Actions → Send notifications → Run workflow** for a test message.

Devices are saved in the database (`devices/`). A device that turns notifications off, or
whose subscription expires, is removed after the next send. Without the secret, the
daily run skips notifications and the Settings page says they aren't set up.

## Layout

| Path | What it is |
| --- | --- |
| `fetcher/models.py` | The `Paper` record and daily `CandidatesFile`, as Pydantic models |
| `fetcher/topics.py` | Loads `config/topics.yaml` and tags papers by topic |
| `config/topics.yaml` | Topics, their arXiv categories and OpenAlex subfields. Edit this to change topics |
| `scorer/` | The daily picks (`scorer/models.py` holds `Pick` and `PicksFile`) |
| `config/scoring.yaml` | Scoring weights, light days, topic spread and repeat rules |
| `site_builder/` | Builds the static site: page templates, the JSON each component gets (`views.py`), icons, and `root/sw.js` for notifications |
| `web/` | The site's Lit web components in TypeScript (`src/components/`), its stylesheets (`src/styles/`), and `src/lib/` (`board-store.ts` and `firebase.ts` sign in and save the board, `push.ts` handles notifications) |
| `notifier/` | Sends the daily Web Push notification (`python -m notifier send`) |
| `store/` | Firestore access for the daily run and the command line (`python -m store setup`, `check`) |
| `firestore.rules` | Who may read and write the database. Paste into the Firebase console |
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
