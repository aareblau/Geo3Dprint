import unittest
from unittest.mock import call, patch

import http_utils


class FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class HttpUtilsTests(unittest.TestCase):
    def setUp(self):
        http_utils._next_request_at = 0.0

    def test_request_retries_429_and_uses_retry_after(self):
        first = FakeResponse(429, {"Retry-After": "3"})
        second = FakeResponse(200)
        session = FakeSession([first, second])

        with (
            patch("http_utils.time.monotonic", return_value=100.0),
            patch("http_utils.time.sleep") as sleep,
        ):
            response = http_utils.request("GET", "https://example.test", session=session)

        self.assertIs(response, second)
        self.assertTrue(first.closed)
        self.assertEqual(len(session.calls), 2)
        self.assertIn(call(3.0), sleep.mock_calls)

    def test_request_sets_default_user_agent(self):
        response = FakeResponse(200)
        session = FakeSession([response])

        with (
            patch("http_utils.time.monotonic", return_value=100.0),
            patch("http_utils.time.sleep"),
        ):
            http_utils.request("POST", "https://example.test", session=session)

        headers = session.calls[0][2]["headers"]
        self.assertEqual(headers["User-Agent"], http_utils.DEFAULT_USER_AGENT)


if __name__ == "__main__":
    unittest.main()
