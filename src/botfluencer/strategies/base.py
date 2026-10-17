from typing import Protocol

from botfluencer.domain import Draft, ObservationInput
from botfluencer.llm.base import Generator


class Strategy(Protocol):
    version: str
    uses_generator: bool

    async def decide(self, observation: ObservationInput, generator: Generator) -> Draft | None: ...
