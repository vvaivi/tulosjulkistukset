from datetime import date, datetime, timedelta
from typing import Annotated

import typer

from .config import Settings
from .emailer import send_digest
from .pipeline import fetch as fetch_pipeline
from .pipeline import transform as transform_pipeline

app = typer.Typer(no_args_is_help=True, help="Nasdaq Helsinki earnings reminder ELT pipeline")


def _notify(settings: Settings, target_date: str | None, dry_run: bool) -> None:
    if target_date is None:
        selected_date = datetime.now(settings.tz).date() + timedelta(days=1)
    else:
        try:
            selected_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise typer.BadParameter("Use YYYY-MM-DD", param_hint="--date") from exc
    typer.echo(f"Checking earnings releases for {selected_date}")
    count = send_digest(settings, selected_date, dry_run=dry_run)
    if dry_run:
        typer.echo("Dry run completed; no notifications sent or recorded")
    else:
        typer.echo(f"Recorded {count} event notifications for {selected_date}")


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
    target_date: Annotated[str | None, typer.Option("--date", help="Release date (YYYY-MM-DD).")]
    = None,
) -> None:
    """Notify recipients about releases on the selected date (default: tomorrow)."""
    _notify(Settings(), target_date, dry_run)


@app.command(name="run")
def run_all(
    dry_run: bool = typer.Option(False, help="Print email instead of sending it."),
    target_date: Annotated[str | None, typer.Option("--date", help="Release date (YYYY-MM-DD).")]
    = None,
) -> None:
    """Run ingestion, dbt build and notification in order."""
    settings = Settings()
    disclosures, events = fetch_pipeline(settings)
    typer.echo(f"Loaded {disclosures} disclosures and {events} extracted events")
    transform_pipeline(settings)
    _notify(settings, target_date, dry_run)


if __name__ == "__main__":
    app()
