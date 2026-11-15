"""
One LLM call may take up to 90 s.

Long pages with a reasoning model take well over the old 60 s; docker-compose
and .env.template set 300 s, longer than the 120 s the ingress then allowed for a
whole /generate. 90 s leaves a buffer below that limit (decided 2026-10-16).
"""

from src.config import Settings
from src.services.llm_service import LLMService
from conftest import use_settings


def test_the_default_is_ninety_seconds():
    assert Settings(_env_file=None).request_timeout == 90


def test_the_llm_client_reads_for_that_long_but_connects_fast(monkeypatch):
    cfg = Settings(_env_file=None, b_api_key="own-key")
    use_settings(monkeypatch, cfg)

    timeout = LLMService(llm_provider="b-api-openai").http_client.timeout

    assert timeout.read == 90
    assert timeout.connect == 10
