"""Small structured events without content, connection strings, or tracebacks."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("botfluencer")


def event(name: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": name, "timestamp": datetime.now(UTC).isoformat(), **fields}))


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
