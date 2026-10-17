"""
A B-API address set with a trailing slash must work like one without.

Only the router address was trimmed; ".../llm/openai/" became
".../llm/openai//chat/completions", which a gateway may answer with 404 -
and the error then names the model, not the setting.
"""

from src.config import Settings


def test_every_b_api_address_loses_its_trailing_slash():
    settings = Settings(
        _env_file=None,
        b_api_openai_base="https://b-api.example/api/v1/llm/openai/",
        b_api_academiccloud_base="https://b-api.example/api/v1/llm/academiccloud/",
        b_api_router_base="https://b-api.example/api/v1/llm/router/",
    )

    assert settings.b_api_openai_base == "https://b-api.example/api/v1/llm/openai"
    assert (
        settings.b_api_academiccloud_base
        == "https://b-api.example/api/v1/llm/academiccloud"
    )
    assert settings.b_api_router_base == "https://b-api.example/api/v1/llm/router"
