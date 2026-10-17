import hashlib

from botfluencer.domain import Draft, ObservationInput

MOCK_PROMPT = "Produce a synthetic acknowledgement for operational testing. No external model call."
PROMPT_VERSION = hashlib.sha256(MOCK_PROMPT.encode()).hexdigest()


class MockGenerator:
    prompt_version = PROMPT_VERSION

    async def generate(self, observation: ObservationInput) -> Draft:
        return Draft(
            target_id=observation.external_id,
            text="Synthetic acknowledgement for runtime verification.",
        )
