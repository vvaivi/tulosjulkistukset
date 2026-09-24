from datetime import datetime, timedelta

import typer

from .config import Settings
from .emailer import send_digest
from .pipeline import fetch as fetch_pipeline
from .pipeline import transform as transform_pipeline

app = typer.Typer(no_args_is_help=True, help="Nasdaq Helsinki earnings reminder ELT pipeline")


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
) -> None:
    """Notify recipients about tomorrow's releases."""
    settings = Settings()
    tomorrow = datetime.now(settings.tz).date() + timedelta(days=1)
    count = send_digest(settings, tomorrow, dry_run=dry_run)
    typer.echo(f"Recorded {count} event notifications")


@app.command(name="run")
def run_all(dry_run: bool = typer.Option(False, help="Print email instead of sending it.")) -> None:
    """Run ingestion, dbt build and notification in order."""
    settings = Settings()
    disclosures, events = fetch_pipeline(settings)
    typer.echo(f"Loaded {disclosures} disclosures and {events} extracted events")
    transform_pipeline(settings)
    tomorrow = datetime.now(settings.tz).date() + timedelta(days=1)
    count = send_digest(settings, tomorrow, dry_run=dry_run)
    typer.echo(f"Recorded {count} event notifications")


if __name__ == "__main__":
    app()
