from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from earnings_notifier.models import Disclosure
from earnings_notifier.nasdaq import NasdaqClient, parse_disclosure_html


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
    assert events[0].event_date == date(2027, 2, 5)


def test_admicom_calendar() -> None:
    disclosure = Disclosure(
        1398228, "Admicom Oyj", "Admicom Oyj: financial reporting in 2026",
        "First North Finland", "Financial calendar", datetime(2025, 11, 3, tzinfo=UTC),
        "https://view.news.eu.nasdaq.com/view?id=b02e8fef6643b5abb7dcad2aec3293585&lang=en",
        "en", "{}",
    )
    events = parse_disclosure_html(disclosure, Path("tests/fixtures/admicom.html").read_text())

    assert {(event.event_date, event.event_type) for event in events} == {
        (date(2026, 1, 21), "full_year"),
        (date(2026, 4, 14), "interim"),
        (date(2026, 7, 8), "half_year"),
        (date(2026, 10, 8), "interim"),
    }


def test_fetches_both_finnish_markets_and_excludes_other_markets() -> None:
    def response(params):
        first_north = params["globalName"] == "NordicFirstNorth"
        assert params["timeZone"] == "UTC"
        assert params["market"] == (
            "First North Finland" if first_north else "Main Market, Helsinki"
        )
        assert params["cnscategory"] == (
            "Financial calendar" if first_north else "Financial Calendar"
        )
        item = {
            "disclosureId": 1398228 if first_north else 1,
            "company": "Admicom Oyj" if first_north else "Test Oyj",
            "headline": "Calendar", "published": "2025-11-03 10:56:23",
            "market": params["market"], "cnsCategory": params["cnscategory"],
            "messageUrl": "https://example.test/calendar", "language": "en",
        }
        return {"results": {"item": [
            item,
            dict(item, disclosureId=2, market="Main Market, Stockholm"),
            dict(item, disclosureId=3, published="2020-01-01 00:00:00"),
            dict(item, disclosureId=4, cnsCategory="Annual Financial Report"),
        ]}}

    client = NasdaqClient()
    try:
        with patch.object(client, "_get_json", side_effect=response) as query:
            disclosures = client.fetch_disclosures(date(2025, 1, 1), date(2026, 10, 8))
        assert query.call_count == 2
        assert {item.company for item in disclosures} == {"Admicom Oyj", "Test Oyj"}
        assert all(item.published_at.hour == 10 for item in disclosures)
    finally:
        client.close()


def test_pagination_limit_is_an_error_instead_of_silent_partial_results() -> None:
    client = NasdaqClient()
    try:
        with patch.object(client, "_get_json", return_value={"results": {"item": [{
            "market": "Main Market, Stockholm", "published": "2026-10-01 00:00:00",
        }]}}):
            with pytest.raises(RuntimeError, match="pagination limit"):
                client.fetch_disclosures(date(2025, 1, 1), date(2026, 10, 8), 1, 1)
    finally:
        client.close()


@pytest.mark.parametrize("entry", [
    "<p>Interim report<br>8 October 2026</p>",
    "<ul><li><p>Interim report<br>8 October 2026</p></li></ul>",
    "<table><tr><td><p>Interim report<br>period ending on 30 September 2026</p></td>"
    "<td><p>Published on Thursday<br>8 October 2026</p></td></tr></table>",
])
def test_line_breaks_do_not_separate_report_from_publication_date(entry: str) -> None:
    disclosure = Disclosure(
        1, "Test Oyj", "Calendar", "Main Market, Helsinki", "Financial Calendar",
        datetime(2026, 1, 1, tzinfo=UTC), "https://example.test/1", "en", "{}",
    )
    events = parse_disclosure_html(disclosure, f'<div id="view-body">{entry}</div>')
    assert len(events) == 1
    assert events[0].event_date == date(2026, 10, 8)


def test_separate_calendar_entries_still_split_at_line_breaks() -> None:
    disclosure = Disclosure(
        1, "Test Oyj", "Calendar", "Main Market, Helsinki", "Financial Calendar",
        datetime(2026, 1, 1, tzinfo=UTC), "https://example.test/1", "en", "{}",
    )
    events = parse_disclosure_html(disclosure, """
        <div id="view-body"><p>
        Interim report on 8 October 2026<br>
        Financial statements release on 21 January 2027<br>
        Annual report on 10 February 2027<br>
        AGM on 17 March 2027
        </p></div>
    """)
    assert {event.event_date for event in events} == {date(2026, 10, 8), date(2027, 1, 21)}
