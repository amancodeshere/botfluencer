"""Curated fictional civic arguments, without personal profiling or model instructions."""

import hashlib
from datetime import datetime, timedelta

from botfluencer.domain import Draft, ObservationInput

TOPIC_TERMS = (
    "h2d2",
    "vaccine",
    "vaccination",
    "referendum",
    "mandate",
    "vote yes",
    "vote no",
)

REPLIES = (
    "I support Yes for Kingstonia's shared spaces. Protecting access also means clear "
    "exemptions, fair checks and public review. What safeguards would make that "
    "approach work fairly?",
    "My view is Yes, with accountability: publish the rules, explain exemptions clearly "
    "and review the policy in public. Shared spaces should stay accessible and safer "
    "together.",
    "A Yes vote can come with demanding transparent rules and fair treatment. I favour "
    "preparation for H2D2, alongside clear exemptions and proper scrutiny of how checks "
    "work.",
    "I favour Yes. The strongest version of this policy pairs protection in shared "
    "spaces with privacy, clear exemptions and public oversight. Those safeguards "
    "deserve discussion too.",
    "For me, Yes means preparing Kingstonia's public spaces before H2D2 arrives. It "
    "also means asking for transparent enforcement, fair access and regular public "
    "review.",
    "I support Yes while taking practical concerns seriously. Clear exemptions, privacy "
    "protections and understandable rules would make the policy fairer for everyone "
    "using shared spaces.",
    "A fair Yes policy needs more than a slogan: explain the checks, protect privacy "
    "and make exemptions clear. I think preparation and accountability can go together.",
    "I'm on the Yes side. Questions about fairness deserve answers, and I favour public "
    "oversight and clear exemptions alongside preparing our shared spaces for H2D2.",
)


def eligible(
    observation: ObservationInput,
    npc_ids: frozenset[int],
    now: datetime,
    *,
    allow_threads: bool = False,
) -> bool:
    data = observation.metadata
    if observation.source != "social" or data.get("author_id") not in npc_ids:
        return False
    if data.get("kind") not in ({"post", "reply"} if allow_threads else {"post"}):
        return False
    if data.get("kind") == "post" and (
        data.get("reply_to_post_id") is not None
        or data.get("root_post_id") not in (None, data.get("post_id"))
    ):
        return False
    try:
        created = datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))
        if created.utcoffset() is None or not timedelta(0) <= now - created <= timedelta(hours=4):
            return False
    except (KeyError, ValueError, TypeError):
        return False
    # Topic selection uses public text only, never inferred traits or private views.
    return any(word in observation.text.lower() for word in TOPIC_TERMS)


def draft_for(observation: ObservationInput) -> Draft:
    index = int(hashlib.sha256(observation.external_id.encode()).hexdigest(), 16) % len(REPLIES)
    return Draft(target_id=observation.external_id, text=REPLIES[index])


# Editorial lenses describe the team's community desks, not invented residents,
# occupations, demographic identities or medical expertise.
EDITORIAL_LENSES = (
    "public accountability",
    "accessible shared spaces",
    "clear exemptions",
    "privacy and proportionality",
    "practical implementation",
    "public questions",
    "fair enforcement",
    "community discussion",
    "transparent decisions",
    "policy review",
)
EDITORIAL_STYLES = (
    "plain-language explainer",
    "short civic question",
    "source-led observation",
    "practical proposal",
    "respectful conversation",
)
EDITORIAL_ROLES = tuple(
    f"Community desk {index + 1:02d}: {lens}; {style}"
    for index, (lens, style) in enumerate(
        (lens, style) for style in EDITORIAL_STYLES for lens in EDITORIAL_LENSES
    )
)


def role_for(account: str, order: int | None = None) -> str:
    """Assign stable editorial variety; ordered accounts have fifty unique desks."""
    index = int(hashlib.sha256(account.encode()).hexdigest(), 16) if order is None else order
    if index < 0:
        raise ValueError("Account order must be nonnegative")
    return EDITORIAL_ROLES[index % len(EDITORIAL_ROLES)]
