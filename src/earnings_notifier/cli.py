from datetime import date, datetime, timedelta
from typing import Annotated

import typer

from .config import Settings
from .emailer import send_digest, send_performance_digest
from .pipeline import fetch as fetch_pipeline
from .pipeline import transform as transform_pipeline

app = typer.Typer(no_args_is_help=True, help="Nasdaq Helsinki earnings reminder ELT pipeline")


def _notify(
    settings: Settings, target_date: str | None, dry_run: bool, report_date: str | None = None
) -> None:
    today = datetime.now(settings.tz).date()
    if target_date is None:
        selected_date = today + timedelta(days=1)
    else:
        try:
            selected_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise typer.BadParameter("Use YYYY-MM-DD", param_hint="--date") from exc
    try:
        selected_report_date = (
            date.fromisoformat(report_date) if report_date else today - timedelta(days=1)
        )
    except ValueError as exc:
        raise typer.BadParameter("Use YYYY-MM-DD", param_hint="--report-date") from exc
    if selected_report_date >= today:
        raise typer.BadParameter(
            "Price report date must be before today", param_hint="--report-date"
        )
    typer.echo(f"Checking earnings releases for {selected_date}")
    count = send_digest(settings, selected_date, dry_run=dry_run)
    typer.echo(f"Checking post-release price changes for {selected_report_date}")
    report_count = send_performance_digest(settings, selected_report_date, dry_run=dry_run)
    if dry_run:
        typer.echo("Dry run completed; no notifications sent or recorded")
    else:
        typer.echo(f"Recorded {count} event notifications for {selected_date}")
        typer.echo(f"Recorded {report_count} price reports for {selected_report_date}")


@app.command()
def fetch() -> None:
    """Fetch and parse Nasdaq financial-calendar disclosures."""
    disclosures, events = fetch_pipeline(Settings())
    typer.echo(f"Loaded {disclosures} disclosures and {events} extracted events")


@app.command()
def transform() -> None:
    """Build and test the dbt models."""
    transform_pipeline(Settings())


@app.command()
def notify(
    dry_run: bool = typer.Option(False, help="Print email instead of sending it."),
    target_date: Annotated[
        str | None, typer.Option("--date", help="Release date (YYYY-MM-DD).")
    ] = None,
    report_date: Annotated[
        str | None,
        typer.Option("--report-date", help="Price report release date (default: yesterday)."),
    ] = None,
) -> None:
    """Send tomorrow's reminders and yesterday's post-release price reports."""
    _notify(Settings(), target_date, dry_run, report_date)


@app.command(name="run")
def run_all(
    dry_run: bool = typer.Option(False, help="Print email instead of sending it."),
    target_date: Annotated[
        str | None, typer.Option("--date", help="Release date (YYYY-MM-DD).")
    ] = None,
    report_date: Annotated[
        str | None,
        typer.Option("--report-date", help="Price report release date (default: yesterday)."),
    ] = None,
) -> None:
    """Run ingestion, dbt build and notification in order."""
    settings = Settings()
    disclosures, events = fetch_pipeline(settings)
    typer.echo(f"Loaded {disclosures} disclosures and {events} extracted events")
    transform_pipeline(settings)
    _notify(settings, target_date, dry_run, report_date)


if __name__ == "__main__":
    app()
