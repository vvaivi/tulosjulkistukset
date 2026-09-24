import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from html import unescape
from typing import Any

import dateparser
import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .models import Disclosure, EarningsEvent

API_URL = "https://api.news.eu.nasdaq.com/news/query.action"
REPORT_TERMS = (
    "financial result",
    "financial results",
    "financial statement release",
    "financial statements bulletin",
    "interim report",
    "half-year report",
    "half-year financial report",
    "half year report",
    "half year financial report",
    "business review",
    "osavuosikatsaus",
    "puolivuosikatsaus",
    "liiketoimintakatsaus",
    "tilinpäätöstiedote",
    "bokslutskommuniké",
    "delårsrapport",
    "halvårsrapport",
)
EXCLUDED_TERMS = (
    "annual report",
    "vuosikertomus",
    "årsredovisning",
    "annual general meeting",
    "yhtiökokous",
    "bolagsstämma",
    "silent period",
    "closed period",
)
DATE_RE = re.compile(
    r"(?:\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b|"
    r"\b\d{4}-\d{1,2}-\d{1,2}\b|"
    r"\b\d{1,2}\s+[A-Za-zÀ-ÖØ-öø-ÿ]+\s+\d{4}\b)",
    re.IGNORECASE,
)


class NasdaqClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "helsinki-earnings-notifier/0.1 (+personal data project)"},
        )

    def close(self) -> None:
        self.client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        response = self.client.get(API_URL, params=params)
        response.raise_for_status()
        return response.json()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _get_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def fetch_disclosures(
        self,
        from_date: date,
        to_date: date,
        page_size: int = 100,
        max_pages: int = 25,
    ) -> list[Disclosure]:
        disclosures: dict[int, Disclosure] = {}
        for page in range(max_pages):
            params = {
                "type": "handleResponse",
                "showAttachments": "true",
                "showCnsSpecific": "true",
                "showCompany": "true",
                "countResults": "true",
                "freeText": "",
                "company": "",
                "market": "Main Market, Helsinki",
                "cnscategory": "Financial Calendar",
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
                "globalGroup": "exchangeNotice",
                "globalName": "NordicMainMarkets",
                "displayLanguage": "en",
                "language": "en",
                "timeZone": "EET",
                "dateMask": "yyyy-MM-dd HH:mm:ss",
                "limit": page_size,
                "start": page * page_size,
                "dir": "DESC",
            }
            payload = self._get_json(params)
            items = payload.get("results", {}).get("item", []) or []
            if not isinstance(items, list):
                items = [items]
            for item in items:
                # Nasdaq's market query occasionally returns other Nordic markets.
                if "helsinki" not in str(item.get("market", "")).lower():
                    continue
                if str(item.get("cnsCategory", "")).lower() != "financial calendar":
                    continue
                disclosure = _to_disclosure(item)
                disclosures[disclosure.disclosure_id] = disclosure
            if len(items) < page_size:
                break
        return list(disclosures.values())

    def fetch_events(self, disclosure: Disclosure) -> list[EarningsEvent]:
        html = self._get_text(disclosure.source_url)
        return parse_disclosure_html(disclosure, html)


def _to_disclosure(item: dict[str, Any]) -> Disclosure:
    published = datetime.strptime(item["published"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return Disclosure(
        disclosure_id=int(item["disclosureId"]),
        company=str(item.get("company", "")).strip(),
        headline=str(item.get("headline", "")).strip(),
        market=str(item.get("market", "")).strip(),
        category=str(item.get("cnsCategory", "")).strip(),
        published_at=published,
        source_url=str(item["messageUrl"]),
        language=str(item.get("language", "en")),
        raw_json=json.dumps(item, ensure_ascii=False, sort_keys=True),
    )


def parse_disclosure_html(disclosure: Disclosure, html: str) -> list[EarningsEvent]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#view-body") or soup
    candidates = _candidate_lines(body)
    events: dict[str, EarningsEvent] = {}
    now = datetime.now(UTC)

    for line in candidates:
        lowered = line.casefold()
        if not any(_contains_term(lowered, term) for term in REPORT_TERMS):
            continue
        if any(_contains_term(lowered, term) for term in EXCLUDED_TERMS):
            continue
        event_type = classify_event(line)
        raw_date = _select_publication_date(line)
        if raw_date is None:
            continue
        parsed = dateparser.parse(
            raw_date,
            languages=["en", "fi", "sv"],
            settings={"DATE_ORDER": "DMY", "STRICT_PARSING": True},
        )
        if parsed is not None:
            event_date = parsed.date()
            event_id = hashlib.sha256(
                f"{disclosure.disclosure_id}|{event_date}|{event_type}|{line}".encode()
            ).hexdigest()[:24]
            events[event_id] = EarningsEvent(
                event_id=event_id,
                disclosure_id=disclosure.disclosure_id,
                company=disclosure.company,
                event_date=event_date,
                event_type=event_type,
                description=line[:1000],
                source_url=disclosure.source_url,
                extracted_at=now,
            )
    return list(events.values())


def _candidate_lines(body: Any) -> Iterable[str]:
    seen: set[str] = set()
    elements = list(body.select("li, p, tr"))
    # Some releases use plain divs instead of semantic paragraphs. Only use leaf
    # divs so a wrapper cannot duplicate every date contained by its children.
    elements.extend(
        element for element in body.select("div") if not element.select_one("li, p, tr, div")
    )
    for element in elements:
        text = unescape(" ".join(element.get_text(" ", strip=True).split()))
        if text and len(text) <= 1500 and text not in seen:
            seen.add(text)
            yield text


def classify_event(text: str) -> str:
    value = text.casefold()
    if any(term in value for term in ("half-year", "half year", "puolivuosi", "halvår")):
        return "half_year"
    if any(term in value for term in ("financial result", "statement", "tilinpäätös", "bokslut")):
        return "full_year"
    if "business review" in value or "liiketoimintakatsaus" in value:
        return "business_review"
    return "interim"


def _contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def _select_publication_date(line: str) -> str | None:
    matches = list(DATE_RE.finditer(line))
    if not matches:
        return None

    def score(match: re.Match[str]) -> int:
        before = line[max(0, match.start() - 60) : match.start()].casefold()
        after = line[match.end() : match.end() + 10]
        value = 4 if after.lstrip().startswith(":") else 0
        if re.search(r"(?:publish(?:ed)?|publication|release|reporting)\s+(?:on\s+)?$", before):
            value += 4
        elif re.search(r"\bon\s+$", before):
            value += 2
        if re.search(
            r"(?:period ending|period ended|previously|formerly|from)\s+(?:on\s+)?$", before
        ):
            value -= 6
        return value

    return max(matches, key=score).group()
