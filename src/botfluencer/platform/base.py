from typing import Protocol

from botfluencer.domain import ActionIntent, ObservationInput, Receipt, Reconciliation


class SafeRetry(Exception):
    """Adapter guarantees the request was rejected before a side effect occurred."""

    def __init__(self, retry_after_seconds: float = 0) -> None:
        super().__init__("Request rejected before execution")
        self.retry_after_seconds = max(0, retry_after_seconds)


class PermanentRejection(Exception):
    """The request was definitively rejected and should not be retried."""


class Platform(Protocol):
    async def observe(self) -> list[ObservationInput]: ...

    async def execute(self, intent: ActionIntent) -> Receipt: ...

    async def reconcile(self, intent: ActionIntent) -> Reconciliation: ...
