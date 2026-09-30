"""Controlled errors shared by all tools.

server.py turns these into clean HTTP/MCP errors.
"""


class TriageError(Exception):
    """An error whose message is safe to show to API clients."""

    def __init__(
        self,
        public_message: str,
        status_code: int = 502,
    ):
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code