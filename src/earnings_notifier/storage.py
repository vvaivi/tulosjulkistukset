from collections.abc import Iterable
from pathlib import Path

import duckdb

from .models import Disclosure, EarningsEvent


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    connection.execute("CREATE SCHEMA IF NOT EXISTS raw")
    connection.execute("CREATE SCHEMA IF NOT EXISTS ops")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS raw.disclosures (
            disclosure_id BIGINT PRIMARY KEY,
            company VARCHAR NOT NULL,
            headline VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            published_at TIMESTAMPTZ NOT NULL,
            source_url VARCHAR NOT NULL,
            language VARCHAR NOT NULL,
            raw_json JSON NOT NULL,
            ingested_at TIMESTAMPTZ DEFAULT current_timestamp
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS raw.earnings_events (
            event_id VARCHAR PRIMARY KEY,
            disclosure_id BIGINT NOT NULL,
            company VARCHAR NOT NULL,
            event_date DATE NOT NULL,
            event_type VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            source_url VARCHAR NOT NULL,
            extracted_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.sent_notifications (
            notification_key VARCHAR PRIMARY KEY,
            event_date DATE NOT NULL,
            recipient VARCHAR NOT NULL,
            sent_at TIMESTAMPTZ DEFAULT current_timestamp
        )
        """
    )
    return connection


def upsert_disclosures(connection: duckdb.DuckDBPyConnection, rows: Iterable[Disclosure]) -> int:
    count = 0
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO raw.disclosures
            (disclosure_id, company, headline, market, category, published_at,
             source_url, language, raw_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, current_timestamp)
            """,
            [
                row.disclosure_id,
                row.company,
                row.headline,
                row.market,
                row.category,
                row.published_at,
                row.source_url,
                row.language,
                row.raw_json,
            ],
        )
        count += 1
    return count


def upsert_events(connection: duckdb.DuckDBPyConnection, rows: Iterable[EarningsEvent]) -> int:
    count = 0
    for row in rows:
        connection.execute(
            """
            INSERT OR REPLACE INTO raw.earnings_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row.event_id,
                row.disclosure_id,
                row.company,
                row.event_date,
                row.event_type,
                row.description,
                row.source_url,
                row.extracted_at,
            ],
        )
        count += 1
    return count


def replace_events_for_disclosure(
    connection: duckdb.DuckDBPyConnection,
    disclosure_id: int,
    rows: Iterable[EarningsEvent],
) -> int:
    """Atomically reconcile parsed events when parser logic or a disclosure changes."""
    materialized_rows = list(rows)
    connection.begin()
    try:
        connection.execute(
            "DELETE FROM raw.earnings_events WHERE disclosure_id = ?", [disclosure_id]
        )
        count = upsert_events(connection, materialized_rows)
        connection.commit()
        return count
    except Exception:
        connection.rollback()
        raise
