"""
Unit tests for RevenueCatClient — pager, 429 retry, and non-retryable error mapping.

Uses unittest.mock to intercept requests.Session.get so no real network calls are made.
time.sleep is patched throughout so tests run instantly.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from client.revenuecat_client import BASE_URL, RevenueCatClient, RevenueCatClientError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int, body: dict, headers: dict | None = None) -> MagicMock:
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.reason = "OK" if status_code < 400 else "Error"
    resp.json.return_value = body
    resp.headers = headers or {}
    return resp


def _list_body(items: list[dict], next_page: str | None = None) -> dict:
    return {"object": "list", "items": items, "next_page": next_page, "url": "https://example.com"}


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestPagination(unittest.TestCase):
    """
    Scenario 1: two-page response.

    Page 1 carries next_page pointing to a full URL; page 2 has next_page=null.
    Assert all items from both pages are merged into a single list.
    """

    def test_two_page_accumulation(self) -> None:
        page1_items = [{"id": "a"}, {"id": "b"}]
        page2_items = [{"id": "c"}]

        page2_url = f"{BASE_URL}/projects?starting_after=b&limit=100"

        page1 = _make_response(200, _list_body(page1_items, next_page=page2_url))
        page2 = _make_response(200, _list_body(page2_items, next_page=None))

        client = RevenueCatClient(api_key="sk_test")

        with patch.object(client._session, "get", side_effect=[page1, page2]) as mock_get:
            result = client.list_projects()

        self.assertEqual(result, page1_items + page2_items)
        self.assertEqual(mock_get.call_count, 2)

        # First call uses BASE_URL path with limit param; second uses the next_page URL verbatim.
        first_call_kwargs = mock_get.call_args_list[0]
        self.assertIn(f"{BASE_URL}/projects", first_call_kwargs[0])

        second_call_kwargs = mock_get.call_args_list[1]
        # Second call hits next_page URL directly — no extra params injected.
        self.assertIn(page2_url, second_call_kwargs[0])
        # params should be None for follow-up pages (cursor already in the URL).
        self.assertIsNone(second_call_kwargs[1].get("params"))


class TestRateLimitRetry(unittest.TestCase):
    """
    Scenario 2: 429 then 200.

    The client should sleep for the backoff_ms period then retry and return the successful result.
    time.sleep is patched so the test is instant.
    """

    @patch("time.sleep")
    def test_429_then_200_retries_and_succeeds(self, mock_sleep: MagicMock) -> None:
        backoff_ms = 500
        rate_limit_body = {
            "type": "rate_limit_error",
            "message": "Too many requests",
            "retryable": True,
            "backoff_ms": backoff_ms,
        }
        items = [{"id": "proj1"}]

        resp_429 = _make_response(429, rate_limit_body, headers={"Retry-After": "1"})
        resp_200 = _make_response(200, _list_body(items, next_page=None))

        client = RevenueCatClient(api_key="sk_test")

        with patch.object(client._session, "get", side_effect=[resp_429, resp_200]):
            result = client.list_projects()

        self.assertEqual(result, items)
        # sleep was called once for the 429 backoff — with backoff_ms / 1000.
        mock_sleep.assert_called_once_with(backoff_ms / 1000.0)


class TestNonRetryableError(unittest.TestCase):
    """
    Scenario 3: 401 authentication_error with retryable: false.

    The client must raise RevenueCatClientError immediately without retrying.
    """

    @patch("time.sleep")
    def test_401_raises_immediately_no_retry(self, mock_sleep: MagicMock) -> None:
        auth_error_body = {
            "object": "error",
            "type": "authentication_error",
            "message": "Invalid API key",
            "retryable": False,
            "doc_url": "https://errors.rev.cat/authentication_error",
        }
        resp_401 = _make_response(401, auth_error_body)

        client = RevenueCatClient(api_key="sk_bad")

        with patch.object(client._session, "get", side_effect=[resp_401]) as mock_get:
            with self.assertRaises(RevenueCatClientError) as ctx:
                client.list_projects()

        exc = ctx.exception
        self.assertEqual(exc.error_type, "authentication_error")
        self.assertFalse(exc.retryable)
        self.assertEqual(exc.status_code, 401)

        # Exactly one HTTP call — no retry.
        self.assertEqual(mock_get.call_count, 1)
        # sleep must not have been called.
        mock_sleep.assert_not_called()


class TestRetryable4xx(unittest.TestCase):
    """
    Scenario 4: a non-429 4xx whose body explicitly says retryable: true must be retried.

    Before the fix the guard was `if not error.retryable or status < 500: raise`, so any 4xx
    (even retryable: true) raised immediately — only 429 was ever retried. The corrected guard
    `if not error.retryable and status < 500: raise` lets a body-flagged retryable 4xx retry.
    """

    @patch("time.sleep")
    def test_retryable_409_then_200_retries_and_succeeds(self, mock_sleep: MagicMock) -> None:
        retryable_409_body = {
            "object": "error",
            "type": "conflict",
            "message": "Resource is temporarily locked",
            "retryable": True,
        }
        items = [{"id": "proj1"}]
        resp_409 = _make_response(409, retryable_409_body)
        resp_200 = _make_response(200, _list_body(items, next_page=None))

        client = RevenueCatClient(api_key="sk_test")

        with patch.object(client._session, "get", side_effect=[resp_409, resp_200]) as mock_get:
            result = client.list_projects()

        self.assertEqual(result, items)
        # Two HTTP calls: the 409 was retried (not raised immediately).
        self.assertEqual(mock_get.call_count, 2)
        # Backoff slept once before the retry.
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    def test_non_retryable_400_still_raises_immediately(self, mock_sleep: MagicMock) -> None:
        """A 4xx with retryable: false must still fail immediately — no over-retrying."""
        bad_request_body = {
            "object": "error",
            "type": "invalid_request",
            "message": "Bad parameter",
            "retryable": False,
        }
        resp_400 = _make_response(400, bad_request_body)

        client = RevenueCatClient(api_key="sk_test")

        with patch.object(client._session, "get", side_effect=[resp_400]) as mock_get:
            with self.assertRaises(RevenueCatClientError) as ctx:
                client.list_projects()

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
