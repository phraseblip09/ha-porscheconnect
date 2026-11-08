"""Tests for the login backoff that protects the account from captcha lockout."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "porscheconnect"))

from pyporscheconnectapi.exceptions import PorscheLoginBackoffError  # noqa: E402
from pyporscheconnectapi.oauth2 import _LoginBackoff  # noqa: E402


def test_first_login_is_allowed():
    backoff = _LoginBackoff()
    backoff.check("a@b.c")  # must not raise


def test_failure_blocks_next_login_and_escalates():
    backoff = _LoginBackoff()
    assert backoff.record_failure("a@b.c") == 60
    with pytest.raises(PorscheLoginBackoffError) as exc:
        backoff.check("a@b.c")
    assert exc.value.failures == 1
    assert 0 < exc.value.retry_in <= 61
    assert backoff.record_failure("a@b.c") == 120
    assert backoff.record_failure("a@b.c") == 240


def test_backoff_is_capped():
    backoff = _LoginBackoff()
    for _ in range(12):
        wait = backoff.record_failure("a@b.c")
    assert wait == 3600


def test_success_and_reset_clear_state():
    backoff = _LoginBackoff()
    backoff.record_failure("a@b.c")
    backoff.record_success("a@b.c")
    backoff.check("a@b.c")
    backoff.record_failure("a@b.c")
    backoff.reset("a@b.c")
    backoff.check("a@b.c")


def test_accounts_are_independent():
    backoff = _LoginBackoff()
    backoff.record_failure("a@b.c")
    backoff.check("other@b.c")
