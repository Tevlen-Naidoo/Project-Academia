from __future__ import annotations

import datetime as dt
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from fetcher.models import CandidatesFile, Funnel, Paper
from fetcher.topics import load_topics
from reader import board
from reader.lookup import ref_from_paper
from scorer import cli, pipeline
from scorer.config import ScoringConfig, load_scoring
from scorer.models import PicksFile, Signal, Slot
from scorer.reasons import reason
from scorer.select import History, choose, id_forms, is_light_day, load_history
from scorer.signals import MEDIAN, Percentiles, score_all

THURSDAY = dt.date(2026, 11, 5)
FRIDAY = dt.date(2026, 11, 6)
NOW = dt.datetime(2026, 11, 5, 6, 0, tzinfo=dt.UTC)
TOPICS = load_topics()
CONFIG = load_scoring()


def make_paper(
    n: int,
    *,
    topics: tuple[str, ...] = ("ml",),
    funnel: tuple[Funnel, ...] = (Funnel.FRESH,),
    upvotes: int | None = None,
    per_month: float | None = None,
    total: int | None = None,
    influential: int | None = None,
    status: str = "preprint",
    venue: str | None = None,
    submitted: dt.date = dt.date(2026, 11, 3),
    doi: str | None = None,
) -> Paper:
    arxiv = f"2611.{n:05d}"
    data: dict[str, Any] = {
        "id": f"arxiv:{arxiv}",
        "ids": {"arxiv": arxiv, "doi": doi},
        "title": f"Paper number {n}",
        "abstract": "An abstract.",
        "topics": list(topics),
        "dates": {"first_seen": "2026-11-05", "submitted": submitted.isoformat()},
        "funnel": list(funnel),
        "fetched_at": "2026-11-05T06:00:00Z",
        "links": {"abstract_page": f"https://arxiv.org/abs/{arxiv}"},
        "buzz": {"hf_upvotes": upvotes},
        "citations": {"per_month": per_month, "total": total, "influential": influential},
        "publication": {
            "status": status,
            "venue": venue,
            "evidence": "none" if status == "preprint" else "arxiv_comment",
        },
    }
    return Paper.model_validate(data)


def pick_ids(papers: list[Paper], today: dt.date, **kwargs: Any) -> list[str]:
    history = kwargs.pop("history", History())
    picks, _, _ = choose(
        score_all(papers, CONFIG, today),
        config=CONFIG,
        topics=TOPICS,
        today=today,
        history=history,
        **kwargs,
    )
    return [p.paper.id for p in picks]


# --- config -----------------------------------------------------------------


def test_default_config_loads() -> None:
    assert CONFIG.picks_per_day == 3
    assert CONFIG.light_days == ("mon", "wed", "fri")


def test_weights_must_add_up_to_one() -> None:
    raw = CONFIG.model_dump(mode="json")
    raw["weights"]["fresh"]["buzz"] = 0.9
    with pytest.raises(ValidationError, match="add up to 1"):
        ScoringConfig.model_validate(raw)


# --- signals ----------------------------------------------------------------


def test_percentiles() -> None:
    pct = Percentiles([0, 0, 0, 5, 10])
    assert pct(0) == 0.0
    assert pct(5) == pytest.approx(0.7)
    assert pct(10) == pytest.approx(0.9)
    assert Percentiles([])(3) == 0.0


def test_missing_citations_score_as_median() -> None:
    paper = make_paper(1, funnel=(Funnel.WAVES,))
    (scored,) = score_all([paper], CONFIG, THURSDAY)
    speed = next(s for s in scored.breakdown if s.signal is Signal.CITATION_SPEED)
    assert speed.value == MEDIAN
    assert speed.known is False


def test_paper_in_both_funnels_gets_the_better_score() -> None:
    fresh_only = make_paper(1, upvotes=50)
    both = make_paper(2, upvotes=50, funnel=(Funnel.FRESH, Funnel.WAVES), per_month=30)
    a, b = score_all([fresh_only, both], CONFIG, THURSDAY)
    assert b.score >= a.score


def test_citation_speed_is_ranked_within_topic() -> None:
    ml = [make_paper(i, per_month=10.0 * i, funnel=(Funnel.WAVES,)) for i in range(1, 5)]
    history = make_paper(9, topics=("history",), per_month=2.0, funnel=(Funnel.WAVES,))
    scored = score_all([*ml, history], CONFIG, THURSDAY)
    hist = next(s for s in scored if s.paper.id == history.id)
    speed = next(s for s in hist.breakdown if s.signal is Signal.CITATION_SPEED)
    assert speed.value == pytest.approx(0.5)  # the only history paper: its own median


def test_recency_falls_over_the_window() -> None:
    new = make_paper(1, submitted=THURSDAY)
    old = make_paper(2, submitted=THURSDAY - dt.timedelta(days=7))
    a, b = score_all([new, old], CONFIG, THURSDAY)
    recency = {s.paper.id: s.breakdown[-1].value for s in (a, b)}
    assert recency == {new.id: 1.0, old.id: 0.0}


# --- reasons ----------------------------------------------------------------


def test_reason_uses_the_two_strongest_signals() -> None:
    paper = make_paper(1, upvotes=142, status="accepted", venue="CVPR 2026")
    others = [make_paper(i) for i in range(2, 6)]
    scored = score_all([paper, *others], CONFIG, THURSDAY)[0]
    assert reason(paper, scored.breakdown, THURSDAY) == (
        "142 upvotes on Hugging Face · Accepted at CVPR 2026"
    )


def test_reason_for_a_waves_paper() -> None:
    paper = make_paper(1, funnel=(Funnel.WAVES,), total=120, per_month=20, influential=15)
    others = [make_paper(i, funnel=(Funnel.WAVES,), total=1, per_month=0.5) for i in range(2, 6)]
    scored = score_all([paper, *others], CONFIG, THURSDAY)[0]
    assert reason(paper, scored.breakdown, THURSDAY).startswith("120 citations, about 20 a month")


def test_reason_has_a_fallback() -> None:
    paper = make_paper(1, submitted=THURSDAY - dt.timedelta(days=30))
    (scored,) = score_all([paper], CONFIG, THURSDAY)
    assert reason(paper, scored.breakdown, THURSDAY) == "Top-scoring paper in its topic today"


# --- selection --------------------------------------------------------------


def test_light_days() -> None:
    assert not is_light_day(THURSDAY, CONFIG)
    assert is_light_day(FRIDAY, CONFIG)
    assert is_light_day(dt.date(2026, 11, 2), CONFIG)  # Monday


def test_picks_come_from_different_topics() -> None:
    papers = [
        make_paper(1, upvotes=100),
        make_paper(2, upvotes=90),
        make_paper(3, topics=("vision",), upvotes=50),
        make_paper(4, topics=("nlp",), upvotes=10),
    ]
    assert pick_ids(papers, THURSDAY) == [papers[0].id, papers[2].id, papers[3].id]


def test_same_topic_is_a_last_resort() -> None:
    papers = [make_paper(i, upvotes=100 - i) for i in range(1, 5)]
    picks, notes, _ = choose(
        score_all(papers, CONFIG, THURSDAY),
        config=CONFIG,
        topics=TOPICS,
        today=THURSDAY,
        history=History(),
    )
    assert len(picks) == 3
    assert sum("any topic" in n for n in notes) == 2


def test_light_topics_wait_for_light_days() -> None:
    papers = [
        make_paper(1, topics=("astronomy",), upvotes=500),
        make_paper(2, upvotes=10),
        make_paper(3, topics=("vision",), upvotes=9),
        make_paper(4, topics=("nlp",), upvotes=8),
    ]
    assert papers[0].id not in pick_ids(papers, THURSDAY)

    picks, _, light_day = choose(
        score_all(papers, CONFIG, FRIDAY),
        config=CONFIG,
        topics=TOPICS,
        today=FRIDAY,
        history=History(),
    )
    assert light_day
    slots = {p.paper.id: p.slot for p in picks}
    assert slots[papers[0].id] is Slot.LIGHT
    assert list(slots.values()).count(Slot.LIGHT) == 1


def test_light_topics_fill_in_when_core_runs_out() -> None:
    papers = [make_paper(1), make_paper(2, topics=("art",))]
    assert pick_ids(papers, THURSDAY) == [papers[0].id, papers[1].id]


def test_board_papers_are_skipped_by_any_id() -> None:
    papers = [
        make_paper(1, upvotes=100, doi="10.1000/ABC"),
        make_paper(2, topics=("vision",)),
    ]
    assert pick_ids(papers, THURSDAY, exclude={"doi:10.1000/abc"}) == [papers[1].id]


def _history(day: dt.date, paper: Paper, topic: str | None = None) -> History:
    history = History()
    for form in id_forms(paper.ids):
        history.last_picked[form] = day
    if topic:
        history.topic_days[topic] = {day}
    return history


def test_recent_picks_wait_out_the_cooldown() -> None:
    old = make_paper(1, upvotes=100)
    other = make_paper(2, topics=("vision",), upvotes=1)
    recent = _history(THURSDAY - dt.timedelta(days=10), old)
    assert pick_ids([old, other], THURSDAY, history=recent) == [other.id]

    long_ago = _history(THURSDAY - dt.timedelta(days=31), old)
    picks, _, _ = choose(
        score_all([old, other], CONFIG, THURSDAY),
        config=CONFIG,
        topics=TOPICS,
        today=THURSDAY,
        history=long_ago,
    )
    repick = next(p for p in picks if p.paper.id == old.id)
    assert repick.repick
    assert repick.adjusted == pytest.approx(repick.score * CONFIG.repeats.factor, abs=1e-4)


def test_topic_spread_lowers_yesterdays_topic() -> None:
    ml = make_paper(1, upvotes=100)
    vision = make_paper(2, topics=("vision",), upvotes=95)
    filler = [make_paper(10 + i, topics=("nlp",), upvotes=1 + i) for i in range(8)]
    yesterday = History(topic_days={"ml": {THURSDAY - dt.timedelta(days=1)}})
    picks, _, _ = choose(
        score_all([ml, vision, *filler], CONFIG, THURSDAY),
        config=CONFIG,
        topics=TOPICS,
        today=THURSDAY,
        history=yesterday,
    )
    assert picks[0].paper.id == vision.id
    lowered = next(p for p in picks if p.paper.id == ml.id)
    assert lowered.adjusted == pytest.approx(lowered.score * 0.85, abs=1e-4)


def test_topic_spread_compounds_over_days() -> None:
    days = {THURSDAY - dt.timedelta(days=d) for d in (1, 2, 3, 4)}
    history = History(topic_days={"ml": days})
    assert history.recent_topic_days("ml", THURSDAY, 3) == 3
    assert history.recent_topic_days("ml", THURSDAY - dt.timedelta(days=4), 3) == 0


# --- pipeline ---------------------------------------------------------------


def _write_candidates(data_dir: Path, day: dt.date, papers: list[Paper]) -> Path:
    path = pipeline.candidates_path(day, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        CandidatesFile(date=day, generated_at=NOW, papers=papers).model_dump_json(), "utf-8"
    )
    return path


def _pool(day: dt.date) -> list[Paper]:
    """A varied pool of new ids each day: every topic, both funnels, mixed signals."""
    papers: list[Paper] = []
    base = (day - dt.date(2026, 11, 1)).days * 1000
    for t, tag in enumerate(TOPICS.topics):
        for i in range(6):
            n = base + t * 100 + i
            papers.append(
                make_paper(
                    n,
                    topics=(tag,),
                    funnel=(Funnel.WAVES,) if i % 3 == 0 else (Funnel.FRESH,),
                    upvotes=(n * 37 + t * 11) % 50 or None,
                    per_month=float((n * 13 + t * 7) % 40) if i % 3 == 0 else None,
                    submitted=day - dt.timedelta(days=i),
                )
            )
    return papers


def test_run_is_deterministic_and_valid(tmp_path: Path) -> None:
    source = _write_candidates(tmp_path, THURSDAY, _pool(THURSDAY))
    first = pipeline.run(source, config=CONFIG, topics=TOPICS, data_dir=tmp_path, now=NOW)
    second = pipeline.run(source, config=CONFIG, topics=TOPICS, data_dir=tmp_path, now=NOW)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.candidates_file == "candidates/2026-11-05.json"

    path = pipeline.write(first, tmp_path)
    assert path == tmp_path / "picks" / "2026-11-05.json"
    loaded = PicksFile.model_validate_json(path.read_bytes())
    assert [p.rank for p in loaded.picks] == [1, 2, 3]
    assert len({p.topic for p in loaded.picks}) == 3


def test_history_reads_earlier_files_only(tmp_path: Path) -> None:
    source = _write_candidates(tmp_path, THURSDAY, _pool(THURSDAY))
    picks = pipeline.run(source, config=CONFIG, topics=TOPICS, data_dir=tmp_path, now=NOW)
    pipeline.write(picks, tmp_path)
    (tmp_path / "picks" / "2026-11-01.json").write_text("not json", "utf-8")

    same_day, _ = load_history(tmp_path, THURSDAY)
    assert same_day.last_picked == {}
    next_day, broken = load_history(tmp_path, FRIDAY)
    assert broken == ["2026-11-01.json"]
    assert picks.picks[0].paper.id in next_day.last_picked


def test_picks_file_rejects_bad_ranks() -> None:
    paper = make_paper(1)
    (scored,) = score_all([paper], CONFIG, THURSDAY)
    pick = {
        "rank": 2,
        "slot": "open",
        "topic": "ml",
        "funnel": "fresh",
        "score": scored.score,
        "adjusted": scored.score,
        "breakdown": [s.model_dump() for s in scored.breakdown],
        "reason": "x",
        "paper": paper.model_dump(mode="json"),
    }
    with pytest.raises(ValidationError, match="ranks"):
        PicksFile.model_validate(
            {
                "date": "2026-11-05",
                "generated_at": NOW,
                "candidates_file": "c",
                "light_day": False,
                "picks": [pick],
            }
        )


def test_thirty_days_spread_topics(tmp_path: Path) -> None:
    """A month of picks from the same kind of pool: light topics 3 days a week, no topic hogs."""
    start = dt.date(2026, 11, 2)  # a Monday
    light = {tag for tag, t in TOPICS.topics.items() if t.weight == "light"}
    topic_days: Counter[str] = Counter()
    light_days = 0
    for offset in range(30):
        day = start + dt.timedelta(days=offset)
        source = _write_candidates(tmp_path, day, _pool(day))
        picks = pipeline.run(source, config=CONFIG, topics=TOPICS, data_dir=tmp_path, now=NOW)
        pipeline.write(picks, tmp_path)
        assert len(picks.picks) == 3
        topics = {p.topic for p in picks.picks if p.topic}
        topic_days.update(topics)
        if topics & light:
            light_days += 1
            assert picks.light_day
    assert light_days == 13  # Mon, Wed, Fri over 30 days from a Monday
    assert max(topic_days.values()) <= 15


def test_cli_skips_papers_on_the_board(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pool = _pool(THURSDAY)
    first = (
        pipeline.run(
            _write_candidates(tmp_path, THURSDAY, pool),
            config=CONFIG,
            topics=TOPICS,
            data_dir=tmp_path,
            now=NOW,
        )
        .picks[0]
        .paper
    )
    b = board.Board()
    b.add(ref_from_paper(first), today=THURSDAY)
    board.save(b, tmp_path / "board.json", now=NOW)

    code = cli.main(["run", "--date", "2026-11-05", "--data-dir", str(tmp_path)])
    assert code == 0
    written = PicksFile.model_validate_json((tmp_path / "picks" / "2026-11-05.json").read_bytes())
    assert first.id not in {p.paper.id for p in written.picks}
    assert "3 picks for 2026-11-05" in capsys.readouterr().out


def test_cli_without_candidates(tmp_path: Path) -> None:
    assert cli.main(["run", "--date", "2026-11-05", "--data-dir", str(tmp_path)]) == 1
