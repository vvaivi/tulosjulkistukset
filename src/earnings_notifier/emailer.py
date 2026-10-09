import hashlib
import logging
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from html import escape

import duckdb
import httpx

from .config import Settings
from .prices import PriceClient, PriceDataError, PriceReport


def pending_events(
    connection: duckdb.DuckDBPyConnection,
    target_date: date,
    recipient: str,
    *,
    performance: bool = False,
) -> list[dict[str, str]]:
    table = "marts.earnings" if performance else "marts.upcoming_earnings"
    suffix = "|performance" if performance else ""
    rows = connection.execute(
        f"""
        SELECT event_id, company, event_date::VARCHAR, event_type, description, source_url
        FROM {table}
        WHERE event_date = ?
          AND NOT EXISTS (
              SELECT 1 FROM ops.sent_notifications sent
              WHERE sent.notification_key = sha256(event_id || '|' || ?)
          )
        ORDER BY company, event_type
        """,
        [target_date, recipient + suffix],
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
                print(f"No pending earnings notifications for {target_date} to {recipient}")
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


def send_performance_digest(settings: Settings, target_date: date, dry_run: bool = False) -> int:
    """Send completed release-day prices independently of the advance reminder."""
    if not settings.recipients and not dry_run:
        raise ValueError("NOTIFIER_RECIPIENTS is required")
    recipients = settings.recipients or ["dry-run@example.com"]
    connection = duckdb.connect(str(settings.database_path))
    client = PriceClient(settings.price_symbols)
    reports: dict[str, PriceReport | None] = {}
    sent_count = 0
    try:
        for recipient in recipients:
            events = pending_events(connection, target_date, recipient, performance=True)
            ready = []
            for event in events:
                company = event["company"]
                if company not in reports:
                    try:
                        reports[company] = client.report(company, target_date)
                    except (httpx.HTTPError, PriceDataError, KeyError, ValueError) as exc:
                        logging.warning("Price report deferred for %s: %s", company, exc)
                        reports[company] = None
                if reports[company] is not None:
                    ready.append((event, reports[company]))
            if not ready:
                print(f"No ready price reports for {target_date} to {recipient}")
                continue
            message = build_performance_message(
                settings.notifier_sender or settings.smtp_username, recipient, ready
            )
            if dry_run:
                print(message.get_body(preferencelist=("plain",)).get_content())
                continue
            _send_smtp(settings, message)
            for event, _ in ready:
                key = hashlib.sha256(
                    f"{event['event_id']}|{recipient}|performance".encode()
                ).hexdigest()
                connection.execute(
                    "INSERT INTO ops.sent_notifications (notification_key, event_date, recipient) "
                    "VALUES (?, ?, ?)",
                    [key, target_date, recipient],
                )
                sent_count += 1
    finally:
        client.close()
        connection.close()
    return sent_count


def build_performance_message(
    sender: str, recipient: str, reports: list[tuple[dict[str, str], PriceReport]]
) -> EmailMessage:
    event_date = reports[0][0]["event_date"]
    message = EmailMessage()
    message["Subject"] = f"Tulosjulkistusten kurssimuutokset ({event_date}): {len(reports)} kpl"
    message["From"] = sender
    message["To"] = recipient
    explanation = (
        "Julkistuspäivä: päätöskurssin muutos edellisestä pörssipäivästä. "
        "Edeltävät jaksot päättyvät julkistusta edeltävään pörssipäivään. "
        "Vertailupäivän ollessa vapaapäivä käytetään edeltävää pörssipäivää. "
        "Muutokset eivät sisällä osinkoja."
    )
    lines = [f"{event_date} tulosjulkistusten kurssimuutokset:", explanation, ""]
    sections = [f"<p>{escape(explanation)}</p>"]
    for event, report in reports:
        title = f"{event['company']} ({event['event_type']}, {report.symbol})"
        closing = f"Päätöskurssi: {report.close:.2f} {report.currency}"
        lines.extend([title, closing])
        rows = []
        for change in report.changes:
            value = (
                f"{change.percent:+.2f} %" if change.percent is not None else "Ei kurssihistoriaa"
            )
            dates = f"{change.start_date} – {change.end_date}" if change.start_date else "–"
            lines.append(f"  {change.label}: {value} ({dates})")
            rows.append(
                f"<tr><td>{escape(change.label)}</td><td>{escape(value)}</td>"
                f"<td>{escape(dates)}</td></tr>"
            )
        lines.extend([f"  Kurssilähde: {report.source_url}", f"  {event['source_url']}", ""])
        sections.append(
            f"<h2>{escape(title)}</h2><p>{escape(closing)}</p>"
            "<table><tr><th>Jakso</th><th>Muutos</th><th>Vertailupäivät</th></tr>"
            + "".join(rows)
            + f'</table><p><a href="{escape(report.source_url)}">Yahoo Finance</a> · '
            + f'<a href="{escape(event["source_url"])}">Nasdaq-tiedote</a></p>'
        )
    message.set_content("\n".join(lines))
    message.add_alternative("".join(sections), subtype="html")
    return message


def build_message(sender: str, recipient: str, events: list[dict[str, str]]) -> EmailMessage:
    event_date = events[0]["event_date"]
    message = EmailMessage()
    message["Subject"] = f"Tulosjulkistukset ({event_date}): {len(events)} kpl"
    message["From"] = sender
    message["To"] = recipient
    lines = [f"{event_date} julkaistavat tulokset:", ""]
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
        f"<p>{escape(event_date)} julkaistavat tulokset:</p><ul>" + "".join(items) + "</ul>",
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
