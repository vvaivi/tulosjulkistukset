import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
from .nasdaq import NasdaqClient
from .storage import connect, replace_events_for_disclosure, upsert_disclosures


def fetch(settings: Settings) -> tuple[int, int]:
    today = datetime.now(settings.tz).date()
    client = NasdaqClient()
    connection = connect(settings.database_path)
    disclosure_count = 0
    event_count = 0
    try:
        disclosures = client.fetch_disclosures(
            today - timedelta(days=settings.nasdaq_lookback_days),
            today + timedelta(days=settings.nasdaq_lookahead_days),
            settings.nasdaq_page_size,
            settings.nasdaq_max_pages,
        )
        disclosure_count = upsert_disclosures(connection, disclosures)
        for disclosure in disclosures:
            event_count += replace_events_for_disclosure(
                connection,
                disclosure.disclosure_id,
                client.fetch_events(disclosure),
            )
    finally:
        connection.close()
        client.close()
    return disclosure_count, event_count


def transform(settings: Settings) -> None:
    project_dir = Path(os.getenv("DBT_PROJECT_DIR", str(Path.cwd() / "dbt")))
    subprocess.run(
        [
            "dbt",
            "build",
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(project_dir),
            "--vars",
            f'{{"database_path": "{settings.database_path.resolve()}"}}',
        ],
        check=True,
    )
