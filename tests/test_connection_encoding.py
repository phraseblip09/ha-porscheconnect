"""Tests for the zstd workaround and error mapping in Connection.request."""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "porscheconnect"))

from pyporscheconnectapi.connection import Connection  # noqa: E402
from pyporscheconnectapi.exceptions import PorscheExceptionError  # noqa: E402

TOKEN = {"access_token": "abc", "refresh_token": "r", "expires_at": 4102444800}


class _FakeClient:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    async def request(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url, headers))
        if self.exc:
            raise self.exc

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True}

        return R()

    async def aclose(self):
        pass


def test_requests_never_offer_zstd():
    client = _FakeClient()
    conn = Connection("a@b.c", "pw", async_client=client, token=dict(TOKEN))
    assert asyncio.run(conn.get("/connect/v1/vehicles")) == {"ok": True}
    headers = client.calls[0][2]
    assert headers["Accept-Encoding"] == "gzip, deflate"
    assert "zstd" not in headers["Accept-Encoding"]
    assert headers["Authorization"] == "Bearer abc"


def test_decoding_error_becomes_porsche_error_without_relogin():
    client = _FakeClient(exc=httpx.DecodingError("Unable to decompress Zstandard data"))
    conn = Connection("a@b.c", "pw", async_client=client, token=dict(TOKEN))
    with pytest.raises(PorscheExceptionError) as exc:
        asyncio.run(conn.get("/connect/v1/vehicles"))
    assert exc.value.message.startswith("RESPONSE_DECODING_ERROR")
    assert len(client.calls) == 1  # one API call, no login traffic
