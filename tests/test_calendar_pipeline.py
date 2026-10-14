from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment

from earnings_notifier.config import Settings
from earnings_notifier.emailer import send_digest
from earnings_notifier.listings import Listing
from earnings_notifier.models import Disclosure
from earnings_notifier.nasdaq import parse_disclosure_html
from earnings_notifier.storage import (
    connect,
    replace_events_for_disclosure,
    replace_listings,
    upsert_disclosures,
)


def test_duell_and_nordea_reach_reminder_through_dbt_models(tmp_path: Path, capsys) -> None:
    settings = Settings(
        _env_file=None, database_path=tmp_path / "earnings.duckdb",
        notifier_recipients="reader@example.test",
    )
    target = date.today() + timedelta(days=1)
    connection = connect(settings.database_path)
    replace_listings(connection, [
        Listing("1", "Duell Oyj", "DUELL", "FI123", "First North Finland"),
        Listing("2", "Nordea Bank Abp", "NDA FI", "FI456", "Main Market, Helsinki"),
        Listing("3", "SSAB A", "SSABAH", "SE123", "Main Market, Helsinki"),
        Listing("4", "Telia Company", "TELIA1", "SE456", "Main Market, Helsinki"),
    ])
    for company, market, fixture, disclosure_id in (
        ("Duell Oyj", "First North Finland", "duell.html", 1393803),
        ("Nordea Bank Abp", "Main Market, Stockholm", "nordea.html", 1390993),
        ("Swedish issuer", "Main Market, Stockholm", "nordea.html", 3),
        ("SSAB AB", "Main Market, Stockholm", "nordea.html", 4),
        ("Telia Company AB", "Main Market, Stockholm", "nordea.html", 5),
    ):
        disclosure = Disclosure(
            disclosure_id, company, "Calendar", market, "Financial Calendar",
            datetime(2025, 10, 1, tzinfo=UTC), "https://example.test/calendar", "en", "{}",
        )
        events = parse_disclosure_html(disclosure, Path("tests/fixtures", fixture).read_text())
        reminder = next(event for event in events if event.event_date == date(2026, 10, 15))
        upsert_disclosures(connection, [disclosure])
        replace_events_for_disclosure(
            connection, disclosure_id, [replace(reminder, event_date=target)]
        )

    environment = Environment()
    environment.globals.update(
        source=lambda schema, table: f"{schema}.{table}",
        ref=lambda model: f"marts.{model}",
    )
    connection.execute("CREATE SCHEMA marts")
    for model, relative_path in (
        ("stg_earnings_events", "staging/stg_earnings_events.sql"),
        ("earnings", "marts/earnings.sql"),
        ("upcoming_earnings", "marts/upcoming_earnings.sql"),
    ):
        sql = environment.from_string(Path("dbt/models", relative_path).read_text()).render()
        connection.execute(f"CREATE VIEW marts.{model} AS {sql}")
    connection.close()

    with patch("earnings_notifier.emailer._send_smtp") as smtp:
        assert send_digest(settings, target, dry_run=True) == 0
        output = capsys.readouterr().out
        assert "Duell Oyj" in output
        assert "Nordea Bank Abp" in output
        assert "Swedish issuer" not in output
        assert "SSAB AB" in output
        assert "Telia Company AB" in output
        smtp.assert_not_called()
        assert send_digest(settings, target) == 4
        assert send_digest(settings, target) == 0
        smtp.assert_called_once()
