import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from earnings_notifier.listings import Listing, company_key
from earnings_notifier.nasdaq import LISTINGS_URL, NasdaqClient
from earnings_notifier.storage import connect, replace_listings


@pytest.mark.parametrize("security,issuer", [
    ("SSAB A", "SSAB AB"),
    ("SSAB B", "SSAB AB"),
    ("Telia Company", "Telia Company AB"),
    ("Nordea Bank Abp", "Nordea Bank Abp"),
    ("Stora Enso Oyj R", "Stora Enso Oyj"),
    ("AS Tallink Grupp FDR", "AS Tallink Grupp"),
    ("Elisa Oyj", "Elisa"),
    ("Alisa Pankki Oyj", "Alisa Bank Plc"),
    ("Nokian Renkaat Oyj", "Nokian Tyres plc"),
    ("Lamor Corporation Oyj", "Lamor Oyj (publ)"),
    ("Afarak Group", "Afarak Group SE"),
    ("  Test–Group Oyj B ", "Test-Group Plc"),
])
def test_listing_and_disclosure_names_match(security: str, issuer: str) -> None:
    assert company_key(security.strip()) == company_key(issuer)
    assert company_key("Unrelated AB") != company_key(issuer)


def test_fetches_all_helsinki_segments_and_first_north() -> None:
    def response(params, *, url):
        assert url == LISTINGS_URL
        assert params["market"] == "HEL"
        if params["category"] == "MAIN_MARKET":
            assert set(params["segment"]) == {"LARGE_CAP", "MID_CAP", "SMALL_CAP", "SPAC"}
        return json.loads(Path(
            f"tests/fixtures/listings_{params['category'].lower()}.json"
        ).read_text())

    client = NasdaqClient()
    try:
        with patch.object(client, "_get_json", side_effect=response):
            listings = client.fetch_listings()
        assert {"nordeabank", "ssab", "teliacompany", "duell"} <= {
            listing.company_key for listing in listings
        }
        assert {listing.market for listing in listings} == {
            "Main Market, Helsinki", "First North Finland"
        }
    finally:
        client.close()


@pytest.mark.parametrize("payload", [
    {"data": None},
    {"data": {"instrumentListing": {"rows": []}, "pagination": {"totalPages": 1}}},
])
def test_listing_failure_stops_disclosure_fetch(payload) -> None:
    client = NasdaqClient()
    try:
        with patch.object(client, "_get_json", return_value=payload) as query:
            with pytest.raises(ValueError, match="incomplete listing"):
                client.fetch_disclosures(date(2025, 1, 1), date(2026, 10, 15))
            assert query.call_count == 1
    finally:
        client.close()


def test_listing_pagination_includes_later_pages() -> None:
    def response(params, *, url):
        assert url == LISTINGS_URL
        page = params["page"]
        category = params["category"]
        return {"data": {
            "instrumentListing": {"rows": [{
                "fullName": f"Issuer {category} {page}", "issuerFullName": "",
                "orderbookId": f"{category}-{page}", "symbol": f"TEST{page}", "isin": "FI123",
            }]},
            "pagination": {"totalPages": 2},
        }}

    client = NasdaqClient()
    try:
        with patch.object(client, "_get_json", side_effect=response) as query:
            listings = client.fetch_listings()
        assert len(listings) == 4
        assert query.call_count == 4
    finally:
        client.close()


def test_existing_database_gets_company_keys_without_losing_notifications(tmp_path: Path) -> None:
    path = tmp_path / "earnings.duckdb"
    connection = connect(path)
    connection.execute("ALTER TABLE raw.disclosures DROP COLUMN company_key")
    connection.execute("""
        INSERT INTO raw.disclosures
        (disclosure_id, company, headline, market, category, published_at,
         source_url, language, raw_json)
        VALUES (1, 'SSAB AB', 'Calendar', 'Main Market, Stockholm', 'Financial Calendar',
                current_timestamp, 'https://example.test/calendar', 'en', '{}')
    """)
    connection.execute("""
        INSERT INTO ops.sent_notifications (notification_key, event_date, recipient)
        VALUES ('previous-notification', current_date, 'reader@example.test')
    """)
    connection.close()
    connection = connect(path)
    try:
        assert connection.execute("SELECT company_key FROM raw.disclosures").fetchone() == ("ssab",)
        assert connection.execute("SELECT count(*) FROM ops.sent_notifications").fetchone() == (1,)
    finally:
        connection.close()


def test_snapshot_replacement_removes_delisted_issuers_and_is_atomic(tmp_path: Path) -> None:
    connection = connect(tmp_path / "earnings.duckdb")
    old = Listing("old", "Old issuer", "OLD", "FI123", "Main Market, Helsinki")
    new = Listing("new", "New issuer", "NEW", "FI456", "First North Finland")
    try:
        replace_listings(connection, [old])
        replace_listings(connection, [new])
        assert connection.execute("SELECT company_key FROM raw.helsinki_listings").fetchall() == [
            ("newissuer",)
        ]
        with pytest.raises(ValueError, match="empty"):
            replace_listings(connection, [])
        with pytest.raises(duckdb.ConstraintException):
            replace_listings(connection, [old, old])
        assert connection.execute("SELECT company_key FROM raw.helsinki_listings").fetchall() == [
            ("newissuer",)
        ]
    finally:
        connection.close()
