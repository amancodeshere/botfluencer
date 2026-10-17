from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import COMPETITION_OPENS, LLM_MODEL, SDK_VERSION
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.domain import ObservationInput
from botfluencer.llm.ctn import CTNGenerator
from botfluencer.platform.ctn import CTNPlatform
from botfluencer.storage.database import Database
from botfluencer.strategies.registry import load_profiles


def offline_report(settings: Settings) -> dict[str, Any]:
    profiles = load_profiles(settings)
    return {
        "sdk_version": version("capturethenarrative"),
        "sdk_version_matches": version("capturethenarrative") == SDK_VERSION,
        "competition_opens_sydney": COMPETITION_OPENS.isoformat(),
        "competition_open": datetime.now(UTC) >= COMPETITION_OPENS,
        "team_code_configured": settings.ctn_team_code is not None,
        "assigned_side": settings.ctn_side,
        "max_total_accounts": settings.ctn_max_total_accounts,
        "max_active_accounts": settings.ctn_max_active_accounts,
        "network_enabled": settings.ctn_network_enabled,
        "platform_backend": settings.platform_backend,
        "mode": settings.mode,
        "model": LLM_MODEL,
        "model_max_tokens": settings.ctn_llm_max_tokens,
        "bots": [profile.model_dump() for profile in profiles.values()],
        "publishing_enabled": False,
    }


async def doctor(settings: Settings, online: bool, check_llm: bool) -> dict[str, Any]:
    report = offline_report(settings)
    if not online:
        return report
    if not settings.ctn_network_enabled or not report["competition_open"]:
        report["online"] = {"status": "blocked", "reason": "network_disabled_or_not_open"}
        return report
    database = Database(settings)
    transport = None
    try:
        await database.ping()
        transport = CompetitionTransport(settings, TeamPacer(database))
        posts = await CTNPlatform(transport).observe()
        report["online"] = {"status": "ok", "observations_received": len(posts)}
        if check_llm:
            # Explicitly requested smoke call; do not display model output or raw input.
            draft = await CTNGenerator(transport).generate(
                ObservationInput(
                    external_id="probe", text="A synthetic test of the competition reply format."
                )
            )
            report["llm_probe"] = {"status": "ok", "characters": len(draft.text)}
        return report
    finally:
        if transport is not None:
            await transport.close()
        await database.close()
