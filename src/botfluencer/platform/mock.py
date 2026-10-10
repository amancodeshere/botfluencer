from pathlib import Path

from botfluencer.domain import ActionIntent, ObservationInput, Receipt, Reconciliation
from botfluencer.storage.repository import Repository


class MockPlatform:
    def __init__(self, repo: Repository, fixture: Path) -> None:
        self.repo = repo
        if fixture.stat().st_size > 1_000_000:
            raise ValueError("Synthetic fixture exceeds the 1 MB limit")
        self.items = [
            ObservationInput.model_validate_json(line)
            for line in fixture.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len({item.external_id for item in self.items}) != len(self.items):
            raise ValueError("Fixture contains duplicate observation IDs")

    async def observe(self) -> list[ObservationInput]:
        return self.items

    async def execute(self, intent: ActionIntent) -> Receipt:
        return await self.repo.mock_execute(intent)

    async def reconcile(self, intent: ActionIntent) -> Reconciliation:
        receipt = await self.repo.mock_lookup(intent.key)
        return Reconciliation(state="applied" if receipt else "not_applied", receipt=receipt)
