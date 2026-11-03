"""The daily run: gather candidates from every source and write one file."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from pathlib import Path

from fetcher import enrich
from fetcher.funnels.fresh import paper_from_entry
from fetcher.http import HttpClient
from fetcher.models import CandidatesFile, Paper, SourceReport
from fetcher.sources.arxiv import ArxivSource
from fetcher.sources.openalex import OpenAlexSource
from fetcher.sources.semantic_scholar import SemanticScholarSource
from fetcher.topics import TopicsConfig

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FRESH_WINDOW_DAYS = 7
PER_TOPIC = 40
"""Candidates kept per topic after enrichment, so small topics are not crowded out."""
MAX_CANDIDATES = 300


@dataclass(frozen=True, slots=True)
class RunOptions:
    today: dt.date
    fresh_days: int = FRESH_WINDOW_DAYS
    per_topic: int = PER_TOPIC
    max_candidates: int = MAX_CANDIDATES


def fresh_window(today: dt.date, days: int) -> tuple[dt.datetime, dt.datetime]:
    end = dt.datetime.combine(today, dt.time.min, tzinfo=dt.UTC)
    return end - dt.timedelta(days=days), end


def run_fresh(
    source: ArxivSource, topics: TopicsConfig, options: RunOptions, now: dt.datetime
) -> tuple[list[Paper], SourceReport]:
    started = time.monotonic()
    since, until = fresh_window(options.today, options.fresh_days)
    report = SourceReport()
    papers: list[Paper] = []
    try:
        result = source.recent(topics.arxiv_query_categories(), since=since, until=until)
    except Exception as exc:  # one failed source must not fail the run
        report.errors.append(f"{type(exc).__name__}: {exc}")
    else:
        report.fetched = len(result.entries)
        report.errors.extend(result.errors)
        for entry in result.entries:
            try:
                paper = paper_from_entry(entry, topics=topics, today=options.today, now=now)
            except ValueError as exc:
                report.errors.append(f"{entry.arxiv_id}: {exc}")
                continue
            if paper is not None:
                papers.append(paper)
    report.duration_s = round(time.monotonic() - started, 2)
    return papers, report


def run_semantic_scholar(source: SemanticScholarSource, papers: list[Paper]) -> SourceReport:
    started = time.monotonic()
    by_id = enrich.s2_ids(papers)
    report = SourceReport()
    try:
        result = source.batch(list(by_id))
    except Exception as exc:
        report.errors.append(f"{type(exc).__name__}: {exc}")
    else:
        report.fetched = len(result.papers)
        report.errors.extend(result.errors)
        for key, s2 in result.papers.items():
            try:
                enrich.apply_s2(by_id[key], s2)
            except ValueError as exc:
                report.errors.append(f"{key}: {exc}")
    report.duration_s = round(time.monotonic() - started, 2)
    return report


def run_openalex(source: OpenAlexSource, papers: list[Paper], topics: TopicsConfig) -> SourceReport:
    started = time.monotonic()
    by_doi = enrich.openalex_dois(papers)
    report = SourceReport()
    try:
        result = source.by_dois(list(by_doi))
    except Exception as exc:
        report.errors.append(f"{type(exc).__name__}: {exc}")
    else:
        report.fetched = len(result.works)
        report.errors.extend(result.errors)
        # by_doi lists arXiv DOIs before publisher DOIs, so a published work applies last
        for doi, paper in by_doi.items():
            work = result.works.get(doi)
            if work is None:
                continue
            try:
                enrich.apply_openalex(paper, work, topics)
            except ValueError as exc:
                report.errors.append(f"{doi}: {exc}")
    report.duration_s = round(time.monotonic() - started, 2)
    return report


def run(
    http: HttpClient,
    topics: TopicsConfig,
    options: RunOptions,
    *,
    now: dt.datetime | None = None,
    arxiv: ArxivSource | None = None,
    semantic_scholar: SemanticScholarSource | None = None,
    openalex: OpenAlexSource | None = None,
) -> CandidatesFile:
    now = now or dt.datetime.now(dt.UTC)
    papers, arxiv_report = run_fresh(arxiv or ArxivSource(http), topics, options, now)
    s2_report = run_semantic_scholar(semantic_scholar or SemanticScholarSource(http), papers)
    oa_report = run_openalex(openalex or OpenAlexSource(http), papers, topics)
    for paper in papers:
        enrich.derive_signals(paper, options.today)
    selected = enrich.select_candidates(
        papers, per_topic=options.per_topic, cap=options.max_candidates
    )
    return CandidatesFile(
        date=options.today,
        generated_at=now,
        sources={"arxiv": arxiv_report, "semantic_scholar": s2_report, "openalex": oa_report},
        papers=selected,
    )


def write(candidates: CandidatesFile, data_dir: Path = DATA_DIR) -> Path:
    path = data_dir / "candidates" / f"{candidates.date.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(candidates.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
