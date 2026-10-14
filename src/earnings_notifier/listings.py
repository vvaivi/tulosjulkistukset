"""Issuer matching between Nasdaq's share listings and company disclosures."""

import re
import unicodedata
from dataclasses import dataclass


def company_key(name: str) -> str:
    value = unicodedata.normalize("NFKC", name).casefold().strip()
    value = re.sub(r"\s+(?:[abcr]|1|fdr|vaihto-osake|kanta-osake)$", "", value)
    value = re.sub(
        r"\b(?:oyj|oy|abp|ab|plc|corporation|corp|ltd|limited|as|se|publ)\b", "", value
    )
    value = re.sub(r"\bpankki\b", "bank", value)
    value = re.sub(r"\b(?:renkaat|tires)\b", "tyres", value)
    return re.sub(r"[^\w]+", "", value)


@dataclass(frozen=True)
class Listing:
    listing_id: str
    company: str
    symbol: str
    isin: str
    market: str

    @property
    def company_key(self) -> str:
        return company_key(self.company)
