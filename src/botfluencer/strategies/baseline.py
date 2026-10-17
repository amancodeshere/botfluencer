import hashlib
import json

from botfluencer.domain import Draft, ObservationInput
from botfluencer.llm.base import Generator

STRATEGY_VERSION = "synthetic-baseline-v1"


def action_key(
    namespace: str,
    account: str,
    external_id: str,
    prompt_version: str,
    strategy_version: str = STRATEGY_VERSION,
) -> str:
    identity = [namespace, account, external_id, strategy_version, prompt_version]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


class SyntheticStrategy:
    version = STRATEGY_VERSION
    uses_generator = True

    async def decide(self, observation: ObservationInput, generator: Generator) -> Draft | None:
        return await generator.generate(observation)


class NoopStrategy:
    version = "noop-v1"
    uses_generator = False

    async def decide(self, observation: ObservationInput, generator: Generator) -> Draft | None:
        return None
