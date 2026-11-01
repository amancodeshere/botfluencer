"""Audited operator-only renewal of an expired red campaign window."""

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select

from botfluencer.storage.models import Action, Control, Outcome, RedCampaign, RedMandateRevision


async def authorize_window(store: Any, *, end: datetime, reason: str) -> dict[str, Any]:
    """Append a same-namespace mandate revision while the worker is stopped.

    Acquiring the namespace advisory lock proves no worker currently owns the
    namespace. The transaction then requires an explicitly paused, expired,
    drained campaign. Existing action, outcome, reservation and usage history is
    retained, and the total attempt cap cannot change.
    """
    if end.utcoffset() is None:
        raise ValueError("Mandate end must include an explicit timezone")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValueError("An explicit bounded operator reason is required")

    async with store.db.leadership(store.namespace):
        async with store.db.sessions.begin() as session:
            campaign = await session.scalar(
                select(RedCampaign).where(RedCampaign.scope == store.scope).with_for_update()
            )
            control = await session.get(Control, store.namespace)
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            if campaign is None or control is None:
                raise ValueError("An existing campaign and namespace control are required")
            if not control.paused:
                raise ValueError("Pause the campaign before authorizing a new mandate")
            if campaign.ends_at > now:
                raise ValueError("The current mandate has not expired")
            if end <= now or end > now + timedelta(hours=48):
                raise ValueError("New mandate end must be future and no more than 48 hours away")
            if campaign.namespace != store.namespace:
                raise ValueError("Mandate renewal cannot change the campaign namespace")
            in_flight = await session.scalar(
                select(Action.id)
                .where(Action.namespace == store.namespace, Action.status == "in_flight")
                .limit(1)
            )
            if in_flight is not None:
                raise ValueError("Resolve or recover in-flight actions before renewal")

            queued = list(
                await session.scalars(
                    select(Action)
                    .where(Action.namespace == store.namespace, Action.status == "queued")
                    .with_for_update()
                )
            )
            for action in queued:
                action.status = "failed"
                session.add(
                    Outcome(
                        action_id=action.id,
                        attempt=action.attempts,
                        kind="mandate_expired",
                        payload={"reason": "Prior mandate expired before renewal"},
                    )
                )

            revision = RedMandateRevision(
                scope=store.scope,
                previous_starts_at=campaign.starts_at,
                previous_ends_at=campaign.ends_at,
                starts_at=now,
                ends_at=end,
                previous_max_attempts=campaign.max_attempts,
                max_attempts=campaign.max_attempts,
                reason=reason.strip(),
            )
            session.add(revision)
            campaign.starts_at = now
            campaign.ends_at = end
            await session.flush()
            return {
                "revision_id": revision.id,
                "namespace": campaign.namespace,
                "starts_at": now,
                "ends_at": end,
                "max_attempts": campaign.max_attempts,
                "expired_queued_actions": len(queued),
            }
