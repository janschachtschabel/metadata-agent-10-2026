"""
Which credentials an LLM call to the B-API carries.

Since 2026-10-13 the B-API proxy can forward the caller's key to a registered
service as `Authorization: Bearer <key>` (option forwardApiKey). The agent must
pass that header on unchanged, per request, and never mix it up with another
request's key. The original way - the agent's own B_API_KEY sent as X-API-KEY -
stays available. METADATA_AGENT_B_API_AUTH_MODE selects:

    static   always the own key (X-API-KEY), an incoming header is ignored
    forward  only the forwarded header; without it the call fails clearly
    auto     the forwarded header if there is one, otherwise the own key
"""

import asyncio

import pytest
from pydantic import ValidationError

from src.config import Settings
from src.services.llm_auth import (
    b_api_auth,
    bearer_or_none,
    forwarded_authorization,
    mask_header,
)
from src.services.llm_errors import LLMCredentialsMissing

USER_KEY = "Bearer user-key-0123456789"


def _settings(mode="auto", key="own-key"):
    return Settings(_env_file=None, b_api_auth_mode=mode, b_api_key=key)


# ------------------------------------------------------------------ modes


def test_static_mode_sends_the_own_key_and_ignores_a_forwarded_header():
    with forwarded_authorization(USER_KEY):
        headers, source = b_api_auth(_settings("static"))

    assert headers == {"X-API-KEY": "own-key"}
    assert source == "static"


def test_forward_mode_passes_the_header_on_unchanged():
    with forwarded_authorization(USER_KEY):
        headers, source = b_api_auth(_settings("forward"))

    assert headers == {"Authorization": USER_KEY}
    assert source == "forwarded"


def test_forward_mode_without_header_fails_instead_of_using_the_own_key():
    with pytest.raises(LLMCredentialsMissing) as raised:
        b_api_auth(_settings("forward"))

    assert raised.value.fatal
    assert "forward" in raised.value.message


def test_auto_mode_prefers_the_forwarded_header():
    with forwarded_authorization(USER_KEY):
        headers, source = b_api_auth(_settings("auto"))

    assert headers == {"Authorization": USER_KEY}
    assert source == "forwarded"


def test_auto_mode_falls_back_to_the_own_key_without_header():
    headers, source = b_api_auth(_settings("auto"))

    assert headers == {"X-API-KEY": "own-key"}
    assert source == "static"


def test_auto_mode_without_header_and_without_key_fails_clearly():
    with pytest.raises(LLMCredentialsMissing):
        b_api_auth(_settings("auto", key=""))


def test_exactly_one_auth_header_is_ever_sent():
    """A valid Bearer plus an invalid X-API-KEY is a 401 at the B-API."""
    for mode in ("static", "forward", "auto"):
        with forwarded_authorization(USER_KEY):
            headers, _ = b_api_auth(_settings(mode))
        assert len(headers) == 1


def test_auto_is_the_default_mode():
    assert Settings(_env_file=None).b_api_auth_mode == "auto"


@pytest.mark.parametrize(
    "raw, expected", [("FORWARD", "forward"), (" static ", "static"), ("", "auto")]
)
def test_the_mode_setting_is_forgiving_about_case_blanks_and_empty_values(
    raw, expected
):
    assert Settings(_env_file=None, b_api_auth_mode=raw).b_api_auth_mode == expected


def test_an_unknown_mode_is_rejected_at_startup():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, b_api_auth_mode="proxy")


# --------------------------------------------------------- what counts as a forwarded key


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Bearer abc", "Bearer abc"),
        ("bearer abc", "bearer abc"),  # passed on as is; the B-API answers 401 itself
        ("Basic dXNlcjpwdw==", None),  # not a B-API key
        ("Bearer", None),
        ("Bearer   ", None),
        ("", None),
        (None, None),
    ],
)
def test_only_a_bearer_value_with_a_token_is_taken(value, expected):
    assert bearer_or_none(value) == expected


# ------------------------------------------------------------------ isolation


@pytest.mark.asyncio
async def test_concurrent_requests_never_see_each_others_key():
    """Each request runs in its own context; parallel field tasks inherit it."""
    settings = _settings("auto")
    seen = {}

    async def request(name, header):
        with forwarded_authorization(header):
            await asyncio.sleep(0.01)  # let the other request interleave

            async def field_task():
                await asyncio.sleep(0)
                return b_api_auth(settings)[0]

            seen[name] = await asyncio.gather(*(field_task() for _ in range(5)))

    await asyncio.gather(
        request("user", "Bearer user-key"),
        request("guest", "Bearer guest-key"),
        request("direct", None),
    )

    assert all(h == {"Authorization": "Bearer user-key"} for h in seen["user"])
    assert all(h == {"Authorization": "Bearer guest-key"} for h in seen["guest"])
    assert all(h == {"X-API-KEY": "own-key"} for h in seen["direct"])


def test_the_forwarded_key_is_gone_after_the_request():
    with forwarded_authorization(USER_KEY):
        pass

    assert b_api_auth(_settings("auto"))[1] == "static"


# ------------------------------------------------------------------ masking


def test_masking_reveals_presence_scheme_and_length_but_never_the_key():
    masked = mask_header("Bearer secret-value-123")

    assert masked == {"present": True, "scheme": "Bearer", "length": 16}
    assert "secret" not in str(masked)
    assert mask_header(None) == {"present": False}
