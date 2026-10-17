from typing import Protocol

from botfluencer.domain import Draft, ObservationInput


class Generator(Protocol):
    prompt_version: str

    async def generate(self, observation: ObservationInput) -> Draft: ...
