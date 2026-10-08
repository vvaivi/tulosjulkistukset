from datetime import date
from unittest.mock import patch

from typer.testing import CliRunner

from earnings_notifier.cli import app


def test_notify_explicit_date_dry_run() -> None:
    with patch("earnings_notifier.cli.send_digest", return_value=0) as send:
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
