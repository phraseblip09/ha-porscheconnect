#  SPDX-License-Identifier: Apache-2.0
"""Python Package for controlling Porsche Connect API."""

from __future__ import annotations

import asyncio
import logging

import httpx

from .const import ACCEPT_ENCODING, API_BASE_URL, TIMEOUT, USER_AGENT, X_CLIENT_ID
from .exceptions import PorscheExceptionError
from .oauth2 import Captcha, Credentials, OAuth2Client, OAuth2Token

_LOGGER = logging.getLogger(__name__)

# HTTP status codes that justify a retry (transient server-side issues):
# 429 (rate limit), 502/503/504 (gateway / upstream timeouts)
_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
_MAX_RETRIES = 3
_MAX_RETRY_DELAY = 30.0


def _compute_retry_delay(response: httpx.Response, attempt: int) -> float:
    """Return how many seconds to wait before retrying after a transient error.

    Prefer the server-provided Retry-After header (RFC 9110 §10.2.3) if
    provided as digit, otherwise fall back to exponential backoff (2s, 4s, 8s).
    """
    retry_after = response.headers.get("retry-after", "")
    if retry_after.isdigit():
        return min(float(retry_after), _MAX_RETRY_DELAY)
    return min((2 ** (attempt + 1)), _MAX_RETRY_DELAY)


async def log_request(request):
    """Provide formatting for http logging."""
    _LOGGER.debug("Request headers: %s", request.headers)
    _LOGGER.debug("Request method - url: %s %s", request.method, request.url)
    _LOGGER.debug("Request body: %s", request.content)


class Connection:
    """Handles authentication and connecting to the Porsche Connect API.

    :param email: Porsche Connect email
    :param password: Porsche Connect password
    :param asyncClient: httpx.AsyncClient or None
    :param token: token dict - should be a dict with access_token, refresh_token, expires_at, etc as root params
    :param leeway: time in seconds to consider token as expired before it actually expires
    """

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        captcha_code: str | None = None,
        state: str | None = None,
        async_client=None,
        token=None,
        leeway: int = 60,
        code_verifier: str | None = None,
        cookies: list | None = None,
    ) -> None:
        """Initialise the connection to the Porsche Connect API."""
        if token is None:
            token = {}
        if async_client is None:
            async_client = httpx.AsyncClient()
        self.asyncClient = async_client
        # Restore the Auth0 session of a login that was interrupted by a captcha.
        for name, value, domain, path in cookies or []:
            self.asyncClient.cookies.set(name, value, domain=domain, path=path)
        self.token_lock = asyncio.Lock()

        self.token = OAuth2Token(token)

        # Accept-Encoding is sent explicitly on every request so that the
        # server never answers with zstd. Home Assistant's shared httpx client
        # advertises zstd since 2026.11, and Porsche's zstd frames are not
        # decodable ("Unable to decompress Zstandard data: Unknown frame
        # descriptor"). Per-request headers override the client defaults.
        self.headers = {
            "User-Agent": USER_AGENT,
            "X-Client-ID": X_CLIENT_ID,
            "Accept-Encoding": ACCEPT_ENCODING,
        }

        self.oauth2_client = OAuth2Client(
            self.asyncClient,
            Credentials(email, password),
            Captcha(captcha_code, state),
            leeway,
            code_verifier=code_verifier,
        )

    async def get_token(self):
        """Return the authentication token."""
        async with self.token_lock:
            await self.oauth2_client.ensure_valid_token(self.token)
        return self.token

    async def get(self, url, params=None):
        """Make a GET request to the Porsche Connect API."""
        return await self.request("GET", url, params=params)

    async def post(self, url, data=None, json=None):
        """Make a POST request to the Porsche Connect API."""
        return await self.request("POST", url, data=data, json=json)

    async def put(self, url, data=None, json=None):
        """Make a PUT request to the Porsche Connect API."""
        return await self.request("PUT", url, data=data, json=json)

    async def delete(self, url, data=None, json=None):
        """Make a DELETE request to the Porsche Connect API."""
        return await self.request("DELETE", url, data=data, json=json)

    async def request(self, method, url, **kwargs):  # noqa: RET503 - loop body always returns or raises
        """Create a request to the Porsche Connect API."""
        async with self.token_lock:
            await self.oauth2_client.ensure_valid_token(self.token)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self.asyncClient.request(
                    method,
                    f"{API_BASE_URL}{url}",
                    headers=self.headers | {"Authorization": f"Bearer {self.token.access_token}"},
                    timeout=TIMEOUT,
                    **kwargs,
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:  # noqa: PERF203
                status = exc.response.status_code
                if status not in _RETRY_STATUS_CODES or attempt == _MAX_RETRIES:
                    raise PorscheExceptionError(
                        status,
                        response_body=exc.response.text[:1000] or None,
                        request_url=str(exc.request.url),
                    ) from exc
                delay = _compute_retry_delay(exc.response, attempt)
                _LOGGER.warning(
                    "Transient HTTP %s on %s - retrying in %.1fs (attempt %d/%d)",
                    status, url, delay, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(delay)
            except httpx.DecodingError as exc:
                # Body could not be decompressed/decoded (e.g. broken zstd frames).
                # Surface as a Porsche error so callers treat it as a failed poll,
                # not as a crash - and never as a reason to log in again.
                raise PorscheExceptionError(
                    "RESPONSE_DECODING_ERROR",
                    response_body=str(exc)[:500],
                    request_url=url,
                ) from exc
            except httpx.TransportError as exc:
                # Network-level failure (timeout, connection reset, DNS...).
                raise PorscheExceptionError(
                    "TRANSPORT_ERROR",
                    response_body=f"{type(exc).__name__}: {exc}"[:500],
                    request_url=url,
                ) from exc

    async def close(self):
        """Close the asyncClient connection."""
        await self.asyncClient.aclose()
