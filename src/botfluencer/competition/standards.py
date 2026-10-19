"""Verified from official CTN 2026 guide and capturethenarrative 0.5.0 source."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

SDK_VERSION = "0.5.0"
SOCIAL_URL = "https://social.legitreal.com/api"
NEWS_URL = "https://sites.legitreal.com"
LLM_URL = "https://llm.legitreal.com/v1"
LLM_MODEL = "qwen2.5-3b-instruct"
COMPETITION_OPENS = datetime(2026, 10, 19, 17, tzinfo=ZoneInfo("Australia/Sydney"))

# Conservative bootstrap pacing only. Server headers and Retry-After update it.
# Shared team allowances, not per-account allowances; auth is per network address.
INITIAL_INTERVAL_SECONDS = {
    "feed": 6.0,
    "other_read": 0.2,
    "publish": 20.0,
    "like": 2.0,
    "follow": 2.0,
    "view": 2.0,
    "post_report": 20.0,
    "account_report": 20.0,
    "llm": 20.0,
    "news": 10.0,  # local conservative policy; no official news quota is documented
}


def require_open(now: datetime | None = None) -> None:
    if (now or datetime.now(UTC)) < COMPETITION_OPENS:
        raise RuntimeError("Competition access opens 19 October 2026 at 17:00 Australia/Sydney")
