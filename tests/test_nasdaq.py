from datetime import UTC, datetime
from pathlib import Path

from earnings_notifier.models import Disclosure
from earnings_notifier.nasdaq import parse_disclosure_html


def test_extracts_only_earnings_events() -> None:
    disclosure = Disclosure(
        disclosure_id=1461491,
        company="Elisa",
        headline="Elisa's Earnings Releases and AGM in 2027",
        market="Main Market, Helsinki",
        category="Financial Calendar",
        published_at=datetime(2026, 9, 2, tzinfo=UTC),
        source_url="https://example.test/elisa",
        language="en",
        raw_json="{}",
    )
    html = Path("tests/fixtures/elisa.html").read_text()

    events = parse_disclosure_html(disclosure, html)

    assert len(events) == 4
    assert {event.event_type for event in events} == {"full_year", "interim", "half_year"}
    assert {event.event_date.isoformat() for event in events} == {
        "2027-01-29",
        "2027-04-20",
        "2027-07-16",
        "2027-10-26",
    }


def test_event_ids_are_deterministic() -> None:
    disclosure = Disclosure(
        1,
        "Test Oyj",
        "Calendar",
        "Main Market, Helsinki",
        "Financial Calendar",
        datetime(2026, 1, 1, tzinfo=UTC),
        "https://example.test/1",
        "en",
        "{}",
    )
    html = "<div id='view-body'><p>Interim report on 24 April 2027</p></div>"

    first = parse_disclosure_html(disclosure, html)[0]
    second = parse_disclosure_html(disclosure, html)[0]

    assert first.event_id == second.event_id


def test_prefers_publication_date_over_financial_period_end() -> None:
    disclosure = Disclosure(
        2,
        "Kreate Group Oyj",
        "Calendar",
        "Main Market, Helsinki",
        "Financial Calendar",
        datetime(2026, 8, 1, tzinfo=UTC),
        "https://example.test/2",
        "en",
        "{}",
    )
    html = """
        <div id="view-body">
          <p>5 February 2027: Financial Statement Release for financial period
          ending on 31 December 2026</p>
        </div>
    """

    events = parse_disclosure_html(disclosure, html)

    assert len(events) == 1
