"""
A fault in the agent during content-type detection is logged with its traceback.

The generic `except Exception` turned it into a "reason" string for the
keyword fallback and nothing reached the log - a bug in the agent looked like
an unreadable model answer. The fallback stays; the log now shows the fault.
"""

import logging

from src.services.llm_service import LLMService

CONTENT_TYPES = [{"schema_file": "event.json", "label": {"de": "Veranstaltung"}}]


async def test_an_agent_fault_keeps_the_fallback_and_is_logged(caplog):
    service = LLMService.__new__(LLMService)  # no client needed: the call faults

    async def broken_call(**kwargs):
        raise KeyError("bug in the agent")

    service._call_llm = broken_call
    service._get_system_prompt = lambda language: "system"

    with caplog.at_level(logging.ERROR, logger="src.services.llm_content_type"):
        detection = await service.detect_content_type("Text", CONTENT_TYPES)

    assert detection.schema_file is None
    assert "KeyError" in detection.problem
    records = [r for r in caplog.records if r.name == "src.services.llm_content_type"]
    assert records and records[0].exc_info is not None
