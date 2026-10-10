import hashlib
import json

STRATEGY_VERSION = "synthetic-baseline-v1"


def action_key(namespace: str, account: str, external_id: str, prompt_version: str) -> str:
    identity = [namespace, account, external_id, STRATEGY_VERSION, prompt_version]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
