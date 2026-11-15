"""
Addresses and field values stay out of the log at INFO.

Audit 13.11., S-22: the geocoder logged every place and address, a
re-extraction the first hundred characters of five values. Such content can
be personal; the guide for services wants none of it in the log. What
happened (how many, which field ids) is still said.
"""

import logging

import httpx
import pytest

from src.config import Settings
from src.services import geocoding_service as geo_module
from src.services.geocoding_service import GeocodingService
from src.services.metadata_generation import log_re_extraction
from conftest import use_settings

PLACE = "Musterstraße 12, 99423 Weimar"


@pytest.fixture
def photon(monkeypatch):
    def handler(request):
        feature = {
            "geometry": {"coordinates": [11.33, 50.98]},
            "properties": {"city": "Weimar", "street": "Musterstraße"},
        }
        return httpx.Response(200, json={"features": [feature]})

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(geo_module.httpx, "AsyncClient", client)
    use_settings(monkeypatch, Settings(_env_file=None, b_api_services="direct"))


@pytest.mark.asyncio
async def test_geocoding_logs_no_place_at_info(photon, caplog):
    service = GeocodingService()
    service.RATE_LIMIT_MS = 0

    with caplog.at_level(logging.INFO):
        result = await service.enrich_metadata_with_geocoding(
            {"schema:location": [PLACE, {"name": "Haus der Familie Beispiel"}]}
        )

    assert result["schema:location"][0]["geo"]["latitude"] == 50.98
    # The agent's own lines; httpx is kept at WARNING by configure_logging.
    logged = " | ".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("src.")
    )
    for content in ("Musterstraße", "Weimar", "Beispiel", "50.98", "11.33"):
        assert content not in logged, content
    assert "2 location(s)" in logged


def test_a_re_extraction_logs_field_ids_not_values(caplog):
    with caplog.at_level(logging.INFO):
        log_re_extraction({"cclom:title": "Tagebuch von Erika Mustermann", "x": None})

    assert "cclom:title" in caplog.text
    assert "Mustermann" not in caplog.text
