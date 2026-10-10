import hashlib
from typing import Protocol

from botfluencer.domain import Draft, ObservationInput

MOCK_PROMPT = "Produce a synthetic acknowledgement for operational testing. No external model call."
PROMPT_VERSION = hashlib.sha256(MOCK_PROMPT.encode()).hexdigest()


class Generator(Protocol):
    async def generate(self, observation: ObservationInput) -> Draft: ...


class MockGenerator:
    async def generate(self, observation: ObservationInput) -> Draft:
        return Draft(
            target_id=observation.external_id,
            text="Synthetic acknowledgement for runtime verification.",
        )
