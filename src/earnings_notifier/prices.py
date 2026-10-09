"""Daily closing-price changes from Yahoo Finance (excluding dividends)."""

import calendar
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class PriceDataError(ValueError):
    """No unambiguous instrument or completed release-day price is available."""


@dataclass(frozen=True)
class PriceChange:
    label: str
    start_date: date | None
    end_date: date
    percent: float | None


@dataclass(frozen=True)
class PriceReport:
    symbol: str
    currency: str
    close: float
    changes: tuple[PriceChange, ...]

    @property
    def source_url(self) -> str:
        return f"https://finance.yahoo.com/quote/{self.symbol}/history/"


def _company_name(name: str) -> str:
    return re.sub(r"\W+", "", re.sub(r"\b(oyj|oy|plc|abp)\b", "", name.casefold()))


def _months_before(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def calculate_report(
    symbol: str, currency: str, closes: dict[date, float], release_date: date
) -> PriceReport:
    prices = {day: value for day, value in closes.items() if math.isfinite(value) and value > 0}
    previous = sorted(day for day in prices if day < release_date)
    if release_date not in prices or not previous:
        raise PriceDataError(f"Completed closing prices missing for {symbol} on {release_date}")
    before = previous[-1]
    if (release_date - before).days > 7:
        raise PriceDataError(f"Previous closing price is stale for {symbol}")

    def change(label: str, target: date, end: date) -> PriceChange:
        available = [day for day in prices if day <= target]
        start = max(available) if available else None
        if start is None or (target - start).days > 7:
            return PriceChange(label, None, end, None)
        return PriceChange(label, start, end, (prices[end] / prices[start] - 1) * 100)

    changes = [
        change("Julkistuspäivä", before, release_date),
        change("1 vk", before - timedelta(days=7), before),
    ]
    for label, months in (("1 kk", 1), ("3 kk", 3), ("6 kk", 6), ("1 v", 12)):
        changes.append(change(label, _months_before(before, months), before))
    return PriceReport(symbol, currency, prices[release_date], tuple(changes))


class PriceClient:
    def __init__(self, symbols: dict[str, str] | None = None) -> None:
        self.symbols = {
            _company_name(company): symbol for company, symbol in (symbols or {}).items()
        }
        self.client = httpx.Client(
            base_url="https://query2.finance.yahoo.com",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _get_json(self, path: str, params: dict) -> dict:
        response = self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def resolve_symbol(self, company: str) -> str:
        normalized = _company_name(company)
        if normalized in self.symbols:
            return self.symbols[normalized]
        payload = self._get_json(
            "/v1/finance/search", {"q": company, "quotesCount": 20, "newsCount": 0}
        )
        matches = {
            quote["symbol"]
            for quote in payload.get("quotes", [])
            if quote.get("quoteType") == "EQUITY"
            and quote.get("symbol", "").endswith(".HE")
            and normalized
            in {
                _company_name(quote.get("shortname") or ""),
                _company_name(quote.get("longname") or ""),
            }
        }
        if len(matches) != 1:
            raise PriceDataError(f"Set PRICE_SYMBOLS for {company}: found {sorted(matches)}")
        symbol = matches.pop()
        self.symbols[normalized] = symbol
        return symbol

    def report(self, company: str, release_date: date) -> PriceReport:
        symbol = self.resolve_symbol(company)
        start = _months_before(release_date, 12) - timedelta(days=14)
        end = release_date + timedelta(days=1)
        payload = self._get_json(
            f"/v8/finance/chart/{symbol}",
            {
                "period1": int(datetime.combine(start, datetime.min.time(), UTC).timestamp()),
                "period2": int(datetime.combine(end, datetime.min.time(), UTC).timestamp()),
                "interval": "1d",
            },
        )
        chart = payload.get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise PriceDataError(f"Price history unavailable for {symbol}")
        result = chart["result"][0]
        meta = result["meta"]
        tz = ZoneInfo(meta["exchangeTimezoneName"])
        closes = {
            datetime.fromtimestamp(timestamp, tz).date(): float(close)
            for timestamp, close in zip(
                result.get("timestamp", []),
                result["indicators"]["quote"][0].get("close", []),
                strict=True,
            )
            if close is not None
        }

        market_time = meta.get("regularMarketTime")
        next_session = meta.get("currentTradingPeriod", {}).get("regular", {}).get("start")
        full_day_price = meta.get("fulldayPrice")
        if (
            release_date not in closes
            and market_time is not None
            and next_session is not None
            and full_day_price is not None
            and datetime.fromtimestamp(market_time, tz).date() == release_date
            and datetime.fromtimestamp(next_session, tz).date() > release_date
            and datetime.now(tz).date() > release_date
        ):
            closes[release_date] = float(full_day_price)
        return calculate_report(symbol, meta["currency"], closes, release_date)
