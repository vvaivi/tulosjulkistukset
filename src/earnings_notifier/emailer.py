import hashlib
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from html import escape

import duckdb

from .config import Settings


def pending_events(
    connection: duckdb.DuckDBPyConnection, target_date: date, recipient: str
) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT event_id, company, event_date::VARCHAR, event_type, description, source_url
        FROM marts.upcoming_earnings
        WHERE event_date = ?
          AND NOT EXISTS (
              SELECT 1 FROM ops.sent_notifications sent
              WHERE sent.notification_key = sha256(event_id || '|' || ?)
          )
        ORDER BY company, event_type
        """,
        [target_date, recipient],
    ).fetchall()
    columns = ["event_id", "company", "event_date", "event_type", "description", "source_url"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def send_digest(settings: Settings, target_date: date, dry_run: bool = False) -> int:
    if not settings.recipients:
        if dry_run:
            settings.notifier_recipients = "dry-run@example.com"
        else:
            raise ValueError("NOTIFIER_RECIPIENTS is required")

    connection = duckdb.connect(str(settings.database_path))
    sent_count = 0
    try:
        for recipient in settings.recipients:
            events = pending_events(connection, target_date, recipient)
            if not events:
                continue
            message = build_message(
                settings.notifier_sender or settings.smtp_username, recipient, events
            )
            if dry_run:
                print(message.as_string())
                continue
            _send_smtp(settings, message)
            for event in events:
                key = hashlib.sha256(f"{event['event_id']}|{recipient}".encode()).hexdigest()
                connection.execute(
                    "INSERT INTO ops.sent_notifications (notification_key, event_date, recipient) "
                    "VALUES (?, ?, ?)",
                    [key, target_date, recipient],
                )
                sent_count += 1
    finally:
        connection.close()
    return sent_count


def build_message(sender: str, recipient: str, events: list[dict[str, str]]) -> EmailMessage:
    event_date = events[0]["event_date"]
    message = EmailMessage()
    message["Subject"] = f"Huomisen tulosjulkistukset ({event_date}): {len(events)} kpl"
    message["From"] = sender
    message["To"] = recipient
    lines = [f"Huomenna {event_date} julkaistavat tulokset:", ""]
    items = []
    for event in events:
        lines.extend([f"- {event['company']} ({event['event_type']})", f"  {event['source_url']}"])
        items.append(
            f"<li><strong>{escape(event['company'])}</strong> "
            f"({escape(event['event_type'])}) – "
            f'<a href="{escape(event["source_url"])}">Nasdaq-tiedote</a></li>'
        )
    message.set_content("\n".join(lines))
    message.add_alternative(
        f"<p>Huomenna {escape(event_date)} julkaistavat tulokset:</p><ul>"
        + "".join(items)
        + "</ul>",
        subtype="html",
    )
    return message


def _send_smtp(settings: Settings, message: EmailMessage) -> None:
    if not all((settings.smtp_host, settings.smtp_username, settings.smtp_password)):
        raise ValueError("SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD are required")
    context = ssl.create_default_context()
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls(context=context)
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
