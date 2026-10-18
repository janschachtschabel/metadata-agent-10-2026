"""
Settings that cannot work are refused at start, not on every call.

METADATA_AGENT_LLM_MAX_RETRIES=0 left the transport zero attempts: every LLM
call failed as "HTTP None" (audit 18.10. final). The value counts the attempts
of one call (1 = no repetition) and the extra calls for an invalid value.
"""

import pytest
from pydantic import ValidationError

from src.config import Settings


@pytest.mark.parametrize("value", [0, -1])
def test_an_llm_call_needs_at_least_one_attempt(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_max_retries=value)


def test_one_attempt_is_allowed():
    assert Settings(_env_file=None, llm_max_retries=1).llm_max_retries == 1
