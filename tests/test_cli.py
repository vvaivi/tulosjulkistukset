from datetime import UTC, date, datetime
from unittest.mock import patch

from typer.testing import CliRunner

from earnings_notifier.cli import app


def test_notify_explicit_date_dry_run() -> None:
    with (
        patch("earnings_notifier.cli.send_digest", return_value=0) as send,
        patch("earnings_notifier.cli.send_performance_digest", return_value=0),
    ):
        result = CliRunner().invoke(app, ["notify", "--date", "2026-10-08", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert send.call_args.args[1] == date(2026, 10, 8)
    assert send.call_args.kwargs == {"dry_run": True}
    assert "no notifications sent or recorded" in result.output


def test_notify_invalid_date() -> None:
    with patch("earnings_notifier.cli.send_digest") as send:
        result = CliRunner().invoke(app, ["notify", "--date", "invalid", "--dry-run"])
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output
    send.assert_not_called()


def test_notify_explicit_report_date() -> None:
    with (
        patch("earnings_notifier.cli.send_digest", return_value=0),
        patch("earnings_notifier.cli.send_performance_digest", return_value=0) as send,
    ):
        result = CliRunner().invoke(app, ["notify", "--report-date", "2026-10-08", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert send.call_args.args[1] == date(2026, 10, 8)
    assert send.call_args.kwargs == {"dry_run": True}


def test_invalid_report_date_is_checked_before_sending() -> None:
    for value in ("invalid", "9999-12-31"):
        with patch("earnings_notifier.cli.send_digest") as send:
            result = CliRunner().invoke(app, ["notify", "--report-date", value])
        assert result.exit_code != 0
        send.assert_not_called()


def test_default_dates_are_tomorrow_and_yesterday() -> None:
    with (
        patch("earnings_notifier.cli.datetime") as clock,
        patch("earnings_notifier.cli.send_digest", return_value=0) as reminder,
        patch("earnings_notifier.cli.send_performance_digest", return_value=0) as report,
    ):
        clock.now.return_value = datetime(2026, 10, 9, 8, tzinfo=UTC)
        result = CliRunner().invoke(app, ["notify", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert reminder.call_args.args[1] == date(2026, 10, 10)
    assert report.call_args.args[1] == date(2026, 10, 8)
