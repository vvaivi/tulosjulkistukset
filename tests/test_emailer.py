from datetime import date
from pathlib import Path
from unittest.mock import patch

from earnings_notifier.config import Settings
from earnings_notifier.emailer import build_message, send_digest, send_performance_digest
from earnings_notifier.prices import PriceDataError, calculate_report
from earnings_notifier.storage import connect


def test_build_message_contains_company_and_source() -> None:
    message = build_message(
        "sender@example.com",
        "reader@example.com",
        [
            {
                "event_id": "1",
                "company": "Test Oyj",
                "event_date": "2027-04-24",
                "event_type": "interim",
                "description": "Interim report",
                "source_url": "https://example.test/source",
            }
        ],
    )

    assert "Test Oyj" in message.get_body(preferencelist=("plain",)).get_content()
    assert message["To"] == "reader@example.com"


def test_dry_run_does_not_prevent_sending_and_repeat_send_is_deduplicated(
    tmp_path: Path,
    capsys,
) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "earnings.duckdb",
        notifier_recipients="reader@example.test",
        notifier_sender="sender@example.test",
    )
    connection = connect(settings.database_path)
    connection.execute("CREATE SCHEMA marts")
    connection.execute("""
        CREATE TABLE marts.upcoming_earnings AS SELECT
        'admicom-q3' AS event_id, 'Admicom Oyj' AS company,
        DATE '2026-10-08' AS event_date, 'interim' AS event_type,
        'Interim report Q3' AS description, 'https://example.test/admicom' AS source_url
    """)
    connection.close()

    target = date(2026, 10, 8)
    with patch("earnings_notifier.emailer._send_smtp") as smtp:
        assert send_digest(settings, target, dry_run=True) == 0
        smtp.assert_not_called()
        assert "Admicom Oyj" in capsys.readouterr().out
        assert send_digest(settings, target) == 1
        smtp.assert_called_once()
        assert send_digest(settings, target) == 0
        smtp.assert_called_once()


def test_price_report_is_independent_dry_run_and_deduplicated(tmp_path: Path, capsys) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "earnings.duckdb",
        notifier_recipients="reader@example.test",
        notifier_sender="sender@example.test",
    )
    connection = connect(settings.database_path)
    connection.execute("CREATE SCHEMA marts")
    connection.execute("""
        CREATE TABLE marts.earnings AS SELECT
        'admicom-q3' AS event_id, 'Admicom Oyj' AS company,
        DATE '2026-10-08' AS event_date, 'interim' AS event_type,
        'Interim report Q3' AS description, 'https://example.test/admicom' AS source_url
    """)
    connection.execute("CREATE VIEW marts.upcoming_earnings AS SELECT * FROM marts.earnings")
    connection.close()
    target = date(2026, 10, 8)
    report = calculate_report(
        "ADMCM.HE",
        "EUR",
        {
            date(2026, 10, 7): 26.85,
            date(2026, 10, 8): 30.2,
        },
        target,
    )
    with (
        patch("earnings_notifier.emailer._send_smtp") as smtp,
        patch("earnings_notifier.emailer.PriceClient") as client,
    ):
        client.return_value.report.return_value = report
        assert send_digest(settings, target) == 1
        assert send_performance_digest(settings, target, dry_run=True) == 0
        output = capsys.readouterr().out
        assert "Admicom Oyj" in output
        assert "+12.48 %" in output
        assert "Ei kurssihistoriaa" in output
        assert smtp.call_count == 1
        assert send_performance_digest(settings, target) == 1
        assert smtp.call_count == 2
        message = smtp.call_args.args[1]
        assert "+12.48 %" in message.get_body(preferencelist=("html",)).get_content()
        assert send_performance_digest(settings, target) == 0
        assert smtp.call_count == 2


def test_failed_price_fetch_is_not_marked_sent_and_can_be_retried(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "earnings.duckdb",
        notifier_recipients="reader@example.test",
    )
    connection = connect(settings.database_path)
    connection.execute("CREATE SCHEMA marts")
    connection.execute("""
        CREATE TABLE marts.earnings AS SELECT
        'admicom-q3' AS event_id, 'Admicom Oyj' AS company,
        DATE '2026-10-08' AS event_date, 'interim' AS event_type,
        '' AS description, 'https://example.test/admicom' AS source_url
    """)
    connection.close()
    target = date(2026, 10, 8)
    with (
        patch("earnings_notifier.emailer._send_smtp") as smtp,
        patch("earnings_notifier.emailer.PriceClient") as client,
    ):
        client.return_value.report.side_effect = PriceDataError("Missing release-day price")
        assert send_performance_digest(settings, target) == 0
        smtp.assert_not_called()
        client.return_value.report.side_effect = None
        client.return_value.report.return_value = calculate_report(
            "ADMCM.HE",
            "EUR",
            {
                date(2026, 10, 7): 26.85,
                date(2026, 10, 8): 30.2,
            },
            target,
        )
        assert send_performance_digest(settings, target) == 1
        smtp.assert_called_once()
