import unittest

from tools.security import RateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_enforces_window_limit_and_returns_retry_delay(self):
        limiter = RateLimiter(limit=2, window=60)

        self.assertEqual(limiter.check("client"), (True, 1, 0))
        self.assertEqual(limiter.check("client"), (True, 0, 0))
        allowed, remaining, retry_after = limiter.check("client")

        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)
        self.assertGreater(retry_after, 0)

    def test_tracks_clients_independently(self):
        limiter = RateLimiter(limit=1, window=60)

        self.assertTrue(limiter.check("client-a")[0])
        self.assertTrue(limiter.check("client-b")[0])


if __name__ == "__main__":
    unittest.main()
