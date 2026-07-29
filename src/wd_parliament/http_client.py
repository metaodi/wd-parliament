"""A small, polite HTTP client shared by every network caller.

It sets the required User-Agent, throttles requests, and retries transient
failures with exponential backoff. The Wikidata Query Service reads through
:meth:`HttpClient.get_json`; ``parliament.py`` instead borrows the underlying
:attr:`HttpClient.session` so that ``swissparlpy``'s OData calls carry the same
User-Agent and connection pool.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        request_delay: float = 1.0,
        timeout: int = 60,
        max_retries: int = 4,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.user_agent = user_agent
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._last_request_ts = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        wait = self.request_delay - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def get_json(self, url: str, params: dict, accept: Optional[str] = None) -> dict:
        """GET ``url`` and return the parsed JSON body, with retries."""
        headers = {"Accept": accept} if accept else {}
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
                # 429/5xx are worth retrying; other 4xx are not.
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(
                        f"{resp.status_code} for {url}", response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                backoff = 2 ** (attempt + 1)
                log.warning(
                    "Request to %s failed (attempt %d/%d): %s; retrying in %ds",
                    url,
                    attempt + 1,
                    self.max_retries,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
        raise RuntimeError(
            f"Request to {url} failed after {self.max_retries} attempts"
        ) from last_exc
