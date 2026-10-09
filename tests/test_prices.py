import json
from datetime import UTC, date, datetime

import httpx
import pytest

from earnings_notifier.prices import PriceClient, PriceDataError, calculate_report


def test_periods_end_before_release_and_weekends_use_previous_close() -> None:
    report = calculate_report(
        "ADMCM.HE",
        "EUR",
        {
            date(2025, 10, 7): 20,
            date(2026, 4, 7): 25,
            date(2026, 7, 7): 30,
            date(2026, 9, 7): 40,
            date(2026, 9, 30): 45,
            date(2026, 10, 7): 50,
            date(2026, 10, 8): 55,
            date(2026, 10, 9): 100,
        },
        date(2026, 10, 8),
    )
    assert report.changes[0].percent == pytest.approx(10)
    assert report.changes[1].percent == pytest.approx(150)
    assert report.changes[-1].percent == pytest.approx(100 / 9)
    assert all(change.end_date == date(2026, 10, 7) for change in report.changes[1:])
    weekend = calculate_report(
        "TEST.HE",
        "EUR",
        {
            date(2026, 9, 4): 40,
            date(2026, 10, 2): 45,
            date(2026, 10, 5): 50,
        },
        date(2026, 10, 5),
    )
    assert weekend.changes[0].start_date == date(2026, 10, 2)
    assert weekend.changes[4].percent is None  # No sufficiently recent Sep 2 close.
    weekend_target = calculate_report(
        "TEST.HE",
        "EUR",
        {date(2026, 9, 4): 40, date(2026, 10, 5): 50, date(2026, 10, 6): 55},
        date(2026, 10, 6),
    )
    assert weekend_target.changes[4].start_date == date(2026, 9, 4)
    assert weekend_target.changes[4].percent == pytest.approx(25)


def test_missing_release_price_is_not_replaced_with_previous_day() -> None:
    with pytest.raises(PriceDataError):
        calculate_report("TEST.HE", "EUR", {date(2026, 10, 7): 50}, date(2026, 10, 8))


def test_short_history_and_invalid_values() -> None:
    report = calculate_report(
        "TEST.HE",
        "EUR",
        {
            date(2026, 10, 6): float("nan"),
            date(2026, 10, 7): 50,
            date(2026, 10, 8): 55,
        },
        date(2026, 10, 8),
    )
    assert all(change.percent is None for change in report.changes[1:])


def test_month_end_and_leap_year() -> None:
    report = calculate_report(
        "TEST.HE",
        "EUR",
        {
            date(2023, 2, 28): 10,
            date(2024, 1, 29): 20,
            date(2024, 2, 29): 30,
            date(2024, 3, 1): 33,
        },
        date(2024, 3, 1),
    )
    assert report.changes[1].start_date == date(2023, 2, 28)
    assert report.changes[4].start_date == date(2024, 1, 29)


def test_symbol_resolution_requires_exact_helsinki_equity() -> None:
    client = PriceClient()
    client.client.close()
    quotes = [
        {"quoteType": "EQUITY", "symbol": "N3Q.F", "longname": "Admicom Oyj"},
        {"quoteType": "EQUITY", "symbol": "ADMCM.HE", "longname": "Admicom Oyj"},
    ]
    client.client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"quotes": quotes})),
        base_url="https://example.test",
    )
    try:
        assert client.resolve_symbol("Admicom Oyj") == "ADMCM.HE"
        with pytest.raises(PriceDataError):
            client.resolve_symbol("Another Oyj")
        quotes.append({"quoteType": "EQUITY", "symbol": "ADMCM-B.HE", "longname": "Admicom"})
        client.symbols.clear()
        with pytest.raises(PriceDataError):
            client.resolve_symbol("Admicom")
    finally:
        client.close()


def test_completed_full_day_price_can_fill_pending_historical_candle() -> None:
    def timestamp(day: date) -> int:
        return int(datetime.combine(day, datetime.min.time(), UTC).timestamp())

    payload = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "EUR",
                        "exchangeTimezoneName": "Europe/Helsinki",
                        "regularMarketTime": timestamp(date(2026, 10, 8)) + 15 * 3600,
                        "fulldayPrice": 30.2,
                        "currentTradingPeriod": {
                            "regular": {"start": timestamp(date(2026, 10, 9))}
                        },
                    },
                    "timestamp": [timestamp(date(2026, 10, 7)), timestamp(date(2026, 10, 8))],
                    "indicators": {"quote": [{"close": [26.85, None]}]},
                }
            ]
        }
    }
    client = PriceClient({"Admicom Oyj": "ADMCM.HE"})
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload))
        ),
        base_url="https://example.test",
    )
    try:
        report = client.report("Admicom", date(2026, 10, 8))
        assert report.changes[0].percent == pytest.approx(12.4767225)
        payload["chart"]["result"][0]["meta"]["currentTradingPeriod"]["regular"]["start"] = (
            timestamp(date(2026, 10, 8))
        )
        with pytest.raises(PriceDataError):
            client.report("Admicom", date(2026, 10, 8))
    finally:
        client.close()
