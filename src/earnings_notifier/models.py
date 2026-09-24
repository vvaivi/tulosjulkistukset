from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Disclosure:
    disclosure_id: int
    company: str
    headline: str
    market: str
    category: str
    published_at: datetime
    source_url: str
    language: str
    raw_json: str


@dataclass(frozen=True)
class EarningsEvent:
    event_id: str
    disclosure_id: int
    company: str
    event_date: date
    event_type: str
    description: str
    source_url: str
    extracted_at: datetime
