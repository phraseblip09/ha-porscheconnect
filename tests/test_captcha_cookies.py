"""The Auth0 session cookies must survive from the password step into the captcha step."""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "porscheconnect"))

from pyporscheconnectapi.connection import Connection  # noqa: E402
from pyporscheconnectapi.exceptions import PorscheCaptchaRequiredError  # noqa: E402


def test_captcha_exception_carries_cookies():
    exc = PorscheCaptchaRequiredError(
        captcha="data:image/svg+xml;base64,AA==",
        state="s",
        code_verifier="v",
        cookies=[("auth0", "abc", "identity.porsche.com", "/")],
    )
    assert exc.cookies == [("auth0", "abc", "identity.porsche.com", "/")]


def test_connection_restores_cookies():
    client = httpx.AsyncClient()
    Connection(
        email="a@b.c",
        password="pw",
        async_client=client,
        cookies=[("auth0", "abc", "identity.porsche.com", "/"), ("did", "xyz", "identity.porsche.com", "/")],
    )
    jar = {(c.name, c.domain): c.value for c in client.cookies.jar}
    assert jar[("auth0", "identity.porsche.com")] == "abc"
    assert jar[("did", "identity.porsche.com")] == "xyz"


def test_connection_without_cookies_is_unchanged():
    client = httpx.AsyncClient()
    Connection(email="a@b.c", password="pw", async_client=client)
    assert list(client.cookies.jar) == []
