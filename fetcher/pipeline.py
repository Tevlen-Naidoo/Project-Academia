"""The daily run: gather candidates from every source and write one file."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from pathlib import Path

from fetcher import enrich, merge
from fetcher.funnels import waves
from fetcher.funnels.fresh import paper_from_entry
from fetcher.http import HttpClient
from fetcher.models import CandidatesFile, Funnel, Paper, SourceReport
from fetcher.sources.arxiv import ArxivSource
from fetcher.sources.hackernews import HackerNewsSource
from fetcher.sources.huggingface import HuggingFaceSource
from fetcher.sources.openalex import OpenAlexSource
from fetcher.sources.semantic_scholar import SemanticScholarSource
from fetcher.topics import TopicsConfig

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FRESH_WINDOW_DAYS = 7
HN_WINDOW_DAYS = 30
FRESH_PER_TOPIC = 25
FRESH_CAP = 200
WAVES_PER_TOPIC = 12
WAVES_CAP = 100
"""Candidates kept after enrichment. Each funnel and each topic gets its own quota, so
highly cited older papers cannot crowd out new ones, nor ML crowd out astronomy."""


@dataclass(frozen=True, slots=True)
class RunOptions:
    today: dt.date
    fresh_days: int = FRESH_WINDOW_DAYS
    fresh_per_topic: int = FRESH_PER_TOPIC
    fresh_cap: int = FRESH_CAP
    waves_per_topic: int = WAVES_PER_TOPIC
    waves_cap: int = WAVES_CAP


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


def run_waves(
    source: OpenAlexSource, topics: TopicsConfig, options: RunOptions, now: dt.datetime
) -> tuple[list[Paper], SourceReport]:
    """Most-cited recent works per configured OpenAlex subfield, in two date windows."""
    started = time.monotonic()
    report = SourceReport()
    papers: list[Paper] = []
    for subfield in topics.openalex_subfields():
        scale = topics.citation_scale(subfield)
        for window in waves.windows(options.today):
            try:
                works = source.most_cited(
                    subfield,
                    since=window.since,
                    until=window.until,
                    min_citations=max(1, round(window.min_citations * scale)),
                )
            except Exception as exc:  # one failed query must not lose the rest
                report.errors.append(
                    f"subfield {subfield} {window.name}: {type(exc).__name__}: {exc}"
                )
                continue
            report.fetched += len(works)
            for work in works:
                try:
                    paper = waves.paper_from_work(work, topics=topics, today=options.today, now=now)
                except ValueError as exc:
                    report.errors.append(f"{work.id}: {exc}")
                    continue
                if paper is not None:
                    papers.append(paper)
    report.duration_s = round(time.monotonic() - started, 2)
    return papers, report


def run_huggingface(
    source: HuggingFaceSource, papers: list[Paper], options: RunOptions
) -> SourceReport:
    started = time.monotonic()
    report = SourceReport()
    result = source.recent(until=options.today, days=options.fresh_days)
    report.fetched = len(result.buzz)
    report.errors.extend(result.errors)
    for paper in papers:
        hit = result.buzz.get(paper.ids.arxiv or "")
        if hit is not None:
            paper.buzz.hf_upvotes = hit.upvotes
            paper.buzz.hf_comments = hit.comments
    report.duration_s = round(time.monotonic() - started, 2)
    return report


def run_hackernews(source: HackerNewsSource, papers: list[Paper], now: dt.datetime) -> SourceReport:
    started = time.monotonic()
    report = SourceReport()
    result = source.recent(since=now - dt.timedelta(days=HN_WINDOW_DAYS))
    report.fetched = result.stories
    report.errors.extend(result.errors)
    for paper in papers:
        hit = result.by_arxiv.get(paper.ids.arxiv or "") or result.by_doi.get(
            (paper.ids.doi or "").lower()
        )
        if hit is not None:
            paper.buzz.hn_points = hit.points
            paper.buzz.hn_comments = hit.comments
    report.duration_s = round(time.monotonic() - started, 2)
    return report


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
    huggingface: HuggingFaceSource | None = None,
    hackernews: HackerNewsSource | None = None,
) -> CandidatesFile:
    now = now or dt.datetime.now(dt.UTC)
    openalex = openalex or OpenAlexSource(http)
    fresh, arxiv_report = run_fresh(arxiv or ArxivSource(http), topics, options, now)
    rising, waves_report = run_waves(openalex, topics, options, now)
    papers = merge.merge(fresh, rising)
    s2_report = run_semantic_scholar(semantic_scholar or SemanticScholarSource(http), papers)
    # Waves papers came from OpenAlex already; only look up the rest
    oa_report = run_openalex(openalex, [p for p in papers if p.ids.openalex is None], topics)
    hf_report = run_huggingface(huggingface or HuggingFaceSource(http), papers, options)
    hn_report = run_hackernews(hackernews or HackerNewsSource(http), papers, now)
    for paper in papers:
        enrich.derive_signals(paper, options.today)
    fresh_pool = [p for p in papers if Funnel.FRESH in p.funnel]
    waves_pool = [p for p in papers if Funnel.FRESH not in p.funnel]
    selected = merge.merge(
        enrich.select_candidates(
            fresh_pool, per_topic=options.fresh_per_topic, cap=options.fresh_cap
        ),
        enrich.select_candidates(
            waves_pool, per_topic=options.waves_per_topic, cap=options.waves_cap
        ),
    )
    return CandidatesFile(
        date=options.today,
        generated_at=now,
        sources={
            "arxiv": arxiv_report,
            "openalex_waves": waves_report,
            "semantic_scholar": s2_report,
            "openalex": oa_report,
            "huggingface": hf_report,
            "hackernews": hn_report,
        },
        papers=selected,
    )


def write(candidates: CandidatesFile, data_dir: Path = DATA_DIR) -> Path:
    path = data_dir / "candidates" / f"{candidates.date.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(candidates.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
