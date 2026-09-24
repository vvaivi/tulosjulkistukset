from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_path: Path = Path("data/earnings.duckdb")
    timezone: str = "Europe/Helsinki"
    nasdaq_lookback_days: int = Field(450, ge=30, le=1500)
    nasdaq_lookahead_days: int = Field(550, ge=30, le=1500)
    nasdaq_page_size: int = Field(100, ge=1, le=200)
    nasdaq_max_pages: int = Field(25, ge=1, le=100)

    notifier_recipients: str = ""
    notifier_sender: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_ssl: bool = True

    @property
    def recipients(self) -> list[str]:
        return [item.strip() for item in self.notifier_recipients.split(",") if item.strip()]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
