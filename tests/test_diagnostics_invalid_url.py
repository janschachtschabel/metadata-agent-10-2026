"""
/debug/dependencies reports an address that is no URL instead of failing (audit 14.11.2026, L-N).

httpx.InvalidURL is no httpx.HTTPError: a configured address like
"http://[::1" (an unclosed IPv6 bracket) escaped the probe, and the report
that exists to show a wrong configuration ended in a 500 because of one.
"""

import httpx
import pytest

from src.services import diagnostics

BROKEN = "http://[::1"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )


def test_the_address_is_one_httpx_refuses():
    with pytest.raises(httpx.InvalidURL) as raised:
        httpx.URL(BROKEN)

    assert not isinstance(raised.value, httpx.HTTPError)


async def test_a_probe_of_an_invalid_address_is_a_report_entry():
    entry, response = await diagnostics._probe(_client(), BROKEN)

    assert response is None
    assert entry["url"] == BROKEN
    assert entry["reachable"] is False
    assert entry["error"] == "InvalidURL"


async def test_the_other_checks_still_report():
    client = _client()

    broken, fine = (
        await diagnostics._check_reachable(client, BROKEN),
        await diagnostics._check_reachable(client, "http://repository.example"),
    )

    assert broken["error"] == "InvalidURL"
    assert fine == {
        "url": "http://repository.example",
        "reachable": True,
        "status": 200,
        "ms": fine["ms"],
    }
