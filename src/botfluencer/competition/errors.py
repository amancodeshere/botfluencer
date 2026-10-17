class RequestDeferred(Exception):
    """Normal pacing decision; heartbeat should continue without another request."""

    def __init__(self, seconds: float) -> None:
        self.seconds = max(0.01, seconds)
        super().__init__("Competition request deferred by shared pacing")


class CompetitionHTTPError(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        # Do not include server bodies, prompts, URLs, or auth headers in exceptions.
        super().__init__(f"Competition service returned HTTP {status}")


class InvalidGeneration(Exception):
    """Model output did not satisfy the validated draft contract."""
