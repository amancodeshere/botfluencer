"""Finite reviewed policy for bounded CTN engagement actions."""

import hashlib
import re

from botfluencer.red.policy import REPLIES
from botfluencer.red.world import validate_source_url

# Engagement replies reuse the team's reviewed Vote Yes civic language. Drafts
# cannot pass arbitrary generated prose through this action path.
TEAM_REPLY_TEXTS = REPLIES

_NEGATION_OR_OPPOSITION = re.compile(
    r"\b(?:no|not|never|cannot|can't|cant|don't|dont|doesn't|doesnt|isn't|isnt|"
    r"aren't|arent|won't|wont|oppose\w*|against|reject\w*|anti)\b",
    re.IGNORECASE,
)
_INJECTION_OR_TEMPLATE = re.compile(
    r"(?:<\|[^|]{1,80}\|>|\[\[|\]\]|```|###|\b(?:ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b|"
    r"(?:system|developer|assistant)\s+(?:prompt|message|instruction)\b|"
    r"(?:ignore|disregard|forget|override)\s+(?:the\s+)?(?:previous|prior|above|all)\s+"
    r"(?:instructions?|rules?|prompts?)\b|instructions?\b|prompt\s+injection\b|"
    r"jailbreak\b|tool\s+call\b|act\s+as\b|"
    r"you\s+are\s+(?:an?\s+)?(?:assistant|bot|model)\b))",
    re.IGNORECASE,
)
_URL = re.compile(
    r"(?:https?://|www\.)[^\s<>]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>]*)?",
    re.IGNORECASE,
)
_POSITIVE_OPENING = re.compile(
    r"^\s*(?:i\s+)?(?:support|back|favor|favour|vote\s+yes(?:\s+on)?|"
    r"yes\s+vote(?:\s+for)?)\b",
    re.IGNORECASE,
)
_REVIEWED_TOPICS = re.compile(
    r"\b(?:h2d2|referendum|kingstonia|shared\s+spaces?|public\s+spaces?)\b",
    re.IGNORECASE,
)
_YES_POSITION = re.compile(
    r"\b(?:yes\s+(?:vote|side|campaign)|(?:vote|voting|back|support|favor|favour)\s+yes)\b",
    re.IGNORECASE,
)


def is_reviewed_team_reply(text: str) -> bool:
    return text in TEAM_REPLY_TEXTS


def body_digest(text: str) -> str:
    """Hash the exact UTF-8 body used when a durable target was reviewed."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_reviewed_yes_target(text: str) -> bool:
    """Fail closed on ambiguous, quoted, injected, or linked NPC content."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized or len(normalized) > 500 or "@" in normalized:
        return False
    if any(quote in normalized for quote in ('"', "'", "“", "”", "‘", "’", "`", ">")):
        return False
    if _NEGATION_OR_OPPOSITION.search(normalized) or _INJECTION_OR_TEMPLATE.search(normalized):
        return False
    if (
        not _POSITIVE_OPENING.search(normalized)
        or not _REVIEWED_TOPICS.search(normalized)
        or not _YES_POSITION.search(normalized)
    ):
        return False
    for match in _URL.finditer(normalized):
        value = match.group().rstrip(".,;:!?)]}")
        try:
            validate_source_url(value)
        except ValueError:
            return False
    return True
