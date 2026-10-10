"""Request security helpers shared by REST and MCP endpoints."""
import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    """Simple in-memory sliding window per client. Fine for one process (not multi-worker)."""

    def __init__(self, limit: int, window: int = 60):
        self.limit, self.window = limit, window
        self.hits = defaultdict(deque)
        self.lock = Lock()

    def check(self, key: str) -> tuple[bool, int, int]:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, 0, int(self.window - (now - q[0])) + 1
            q.append(now)
            return True, self.limit - len(q), 0
