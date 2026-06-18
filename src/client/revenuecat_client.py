"""
RevenueCat v2 API client.

Design notes:
- Uses `requests` directly rather than `keboola.http_client.HttpClient`.
  HttpClient wraps urllib3.Retry with a fixed status_forcelist and creates a new Session per
  request.  It does not honour the RevenueCat-specific `Retry-After` / `backoff_ms` fields on 429
  responses, cannot distinguish `retryable: false` 4xx from retriable 5xx, and its exhausted-retry
  exception (MaxRetryError from urllib3) would surface as an unhandled exit-2 unless caught and
  re-wrapped.  Because we need all three — body-driven 429 backoff, body-driven retryable flag,
  and a single typed error surface — it is simpler and safer to own the retry loop here with plain
  `requests`.
- Requests are issued sequentially (no threads/async).  RevenueCat returns 429 for concurrent
  requests against the same resource, so the client must not parallelize.
"""

from __future__ import annotations

import logging
import random
import time

import requests

logger = logging.getLogger(__name__)

# Connect timeout / read timeout in seconds.  10 s to establish a TCP connection is generous for a
# hosted API; 60 s read timeout covers large paginated responses under rate-limit back-pressure.
DEFAULT_TIMEOUT: tuple[int, int] = (10, 60)

BASE_URL = "https://api.revenuecat.com/v2"

# Maximum number of attempts (1 initial + N-1 retries) for any single request.
MAX_ATTEMPTS = 5

# Page size — RevenueCat caps limit at 100; requesting more silently returns 100.
PAGE_LIMIT = 100


class RevenueCatClientError(Exception):
    """
    Typed error raised for every API-level failure.

    Callers (component.py) inspect `retryable` and `error_type` to decide whether to raise a
    `UserException` (exit 1) or let the error propagate as an unexpected failure (exit 2).
    The client never raises `UserException` directly — that mapping is the component's job.
    """

    def __init__(
        self,
        error_type: str,
        message: str,
        retryable: bool,
        doc_url: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        self.doc_url = doc_url
        self.status_code = status_code

    def __repr__(self) -> str:
        return (
            f"RevenueCatClientError(error_type={self.error_type!r}, "
            f"message={self.message!r}, retryable={self.retryable}, "
            f"status_code={self.status_code})"
        )


def _parse_error_body(response: requests.Response) -> dict:
    """Attempt to parse a JSON error body; return an empty dict on failure."""
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {}


def _error_from_response(response: requests.Response) -> RevenueCatClientError:
    """Build a RevenueCatClientError from an HTTP error response."""
    body = _parse_error_body(response)
    return RevenueCatClientError(
        error_type=body.get("type", "unknown_error"),
        message=body.get("message", response.reason or "Unknown error"),
        retryable=bool(body.get("retryable", False)),
        doc_url=body.get("doc_url"),
        status_code=response.status_code,
    )


class RevenueCatClient:
    """
    Thin HTTP client for the RevenueCat REST API v2.

    Instantiate once and reuse across calls.  The client is NOT thread-safe; callers must not
    share an instance across threads.  Per-customer requests are always issued sequentially.
    """

    def __init__(self, api_key: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> dict:
        """
        Issue a single GET request with retry logic for 429 and 5xx / retryable errors.

        Returns the parsed JSON body on success.
        Raises RevenueCatClientError on terminal failure (retries exhausted or non-retryable error).
        Wraps any requests.RequestException as RevenueCatClientError so nothing escapes untyped.
        """
        attempt = 0
        last_error: RevenueCatClientError | None = None

        while attempt < MAX_ATTEMPTS:
            attempt += 1
            try:
                response = self._session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            except requests.RequestException as exc:
                # Network-level failure (connection refused, DNS, timeout, etc.)
                raise RevenueCatClientError(
                    error_type="network_error",
                    message=str(exc),
                    retryable=True,
                ) from exc

            # Log rate-limit headers at DEBUG for observability — never log auth headers.
            self._log_rate_limit_headers(response)

            if response.ok:
                return response.json()

            if response.status_code == 429:
                last_error = _error_from_response(response)
                if attempt >= MAX_ATTEMPTS:
                    break
                wait_s = self._compute_429_wait(response)
                logger.info(
                    "429 rate limit on attempt %d/%d; backing off %.1f s",
                    attempt,
                    MAX_ATTEMPTS,
                    wait_s,
                )
                time.sleep(wait_s)
                continue

            # 5xx or any response with retryable: true
            error = _error_from_response(response)

            if not error.retryable and response.status_code < 500:
                # 4xx with retryable: false → immediate failure; do not retry.
                # A 4xx that the body explicitly marks retryable:true (not just 429) still retries.
                raise error

            # 5xx or retryable: true → exponential backoff with jitter
            last_error = error
            if attempt >= MAX_ATTEMPTS:
                break

            wait_s = self._compute_backoff_wait(attempt)
            logger.info(
                "HTTP %d on attempt %d/%d (retryable); backing off %.1f s",
                response.status_code,
                attempt,
                MAX_ATTEMPTS,
                wait_s,
            )
            time.sleep(wait_s)

        # Retries exhausted — surface the final error. last_error is always set if we exit the
        # loop via break, but guard explicitly so this survives `python -O` (which strips asserts).
        if last_error is None:
            raise RevenueCatClientError(
                error_type="unknown_error",
                message=f"Request to {url} failed after {MAX_ATTEMPTS} attempts with no captured error.",
                retryable=False,
            )
        raise last_error

    @staticmethod
    def _compute_429_wait(response: requests.Response) -> float:
        """
        Compute sleep duration for a 429 response.

        Preference order:
        1. `backoff_ms` from the JSON body (RevenueCat-specific field, most precise).
        2. `Retry-After` header (seconds, RFC 7231).
        3. Fallback: 1 second + small jitter.
        """
        body = _parse_error_body(response)
        backoff_ms = body.get("backoff_ms")
        if isinstance(backoff_ms, (int, float)) and backoff_ms > 0:
            return backoff_ms / 1000.0

        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass

        # Fallback with jitter
        return 1.0 + random.uniform(0, 0.5)  # noqa: S311

    @staticmethod
    def _compute_backoff_wait(attempt: int) -> float:
        """
        Capped exponential backoff with jitter for 5xx / retryable errors.

        Formula: min(cap, base * 2^(attempt-1)) + jitter[0, 1).
        """
        base = 1.0
        cap = 30.0
        jitter = random.uniform(0, 1)  # noqa: S311
        return min(cap, base * (2 ** (attempt - 1))) + jitter

    @staticmethod
    def _log_rate_limit_headers(response: requests.Response) -> None:
        usage = response.headers.get("revenuecat-rate-limit-current-usage")
        limit = response.headers.get("revenuecat-rate-limit-current-limit")
        if usage is not None or limit is not None:
            logger.debug(
                "RevenueCat rate limit: usage=%s limit=%s",
                usage,
                limit,
            )

    def _paginate(self, path: str, params: dict | None = None) -> list[dict]:
        """
        Generic cursor-following pager.

        Issues GET `path` with `limit=PAGE_LIMIT` merged into `params`.  Follows `next_page`
        (a fully-formed URL already carrying the cursor) until it is None.  The first request
        hits `path`; subsequent requests hit the `next_page` URL verbatim — do NOT re-append
        limit or starting_after to a next_page URL (the cursor is already embedded in it).

        Returns the concatenated list of items from all pages.
        """
        merged_params = {"limit": PAGE_LIMIT}
        if params:
            merged_params.update(params)

        url: str | None = f"{BASE_URL}{path}"
        page_num = 0
        all_items: list[dict] = []

        # Explicit stop condition — never while True.
        while url is not None:
            page_num += 1
            body = self._get(url, params=merged_params if page_num == 1 else None)
            items = body.get("items", [])
            all_items.extend(items)
            next_page: str | None = body.get("next_page")
            logger.debug(
                "Page %d fetched %d items (running total: %d) from %s",
                page_num,
                len(items),
                len(all_items),
                path,
            )
            url = next_page  # None terminates the loop

        logger.info("Pagination complete: %d total items from %s", len(all_items), path)
        return all_items

    # ------------------------------------------------------------------
    # Public API — one method per endpoint group
    # ------------------------------------------------------------------

    def test_connection(self) -> None:
        """
        Probe `GET /projects?limit=1` to validate credentials.

        Raises RevenueCatClientError on any failure.  The sync-action handler catches this and
        maps it to a clean success/failure result without letting exceptions escape.
        """
        url = f"{BASE_URL}/projects"
        self._get(url, params={"limit": 1})

    def list_projects(self) -> list[dict]:
        """GET /projects — returns all projects visible to the API key."""
        return self._paginate("/projects")

    def list_apps(self, project_id: str) -> list[dict]:
        """GET /projects/{project_id}/apps"""
        return self._paginate(f"/projects/{project_id}/apps")

    def list_products(self, project_id: str) -> list[dict]:
        """GET /projects/{project_id}/products"""
        return self._paginate(f"/projects/{project_id}/products")

    def list_entitlements(self, project_id: str) -> list[dict]:
        """GET /projects/{project_id}/entitlements"""
        return self._paginate(f"/projects/{project_id}/entitlements")

    def list_offerings(self, project_id: str) -> list[dict]:
        """GET /projects/{project_id}/offerings"""
        return self._paginate(f"/projects/{project_id}/offerings")

    def list_packages(self, project_id: str, offering_id: str) -> list[dict]:
        """
        GET /projects/{project_id}/offerings/{offering_id}/packages

        Packages are nested under offerings — the project-level /packages path returns 404.
        """
        return self._paginate(f"/projects/{project_id}/offerings/{offering_id}/packages")

    def list_customers(self, project_id: str) -> list[dict]:
        """GET /projects/{project_id}/customers — the enumerable spine of the customer domain."""
        return self._paginate(f"/projects/{project_id}/customers")

    def list_customer_active_entitlements(self, project_id: str, customer_id: str) -> list[dict]:
        """
        GET /projects/{project_id}/customers/{customer_id}/active_entitlements

        Path is /active_entitlements, NOT /entitlements (which returns 404).
        """
        return self._paginate(f"/projects/{project_id}/customers/{customer_id}/active_entitlements")

    def list_customer_subscriptions(self, project_id: str, customer_id: str) -> list[dict]:
        """GET /projects/{project_id}/customers/{customer_id}/subscriptions"""
        return self._paginate(f"/projects/{project_id}/customers/{customer_id}/subscriptions")

    def list_customer_purchases(self, project_id: str, customer_id: str) -> list[dict]:
        """GET /projects/{project_id}/customers/{customer_id}/purchases"""
        return self._paginate(f"/projects/{project_id}/customers/{customer_id}/purchases")

    def list_customer_invoices(self, project_id: str, customer_id: str) -> list[dict]:
        """GET /projects/{project_id}/customers/{customer_id}/invoices"""
        return self._paginate(f"/projects/{project_id}/customers/{customer_id}/invoices")
