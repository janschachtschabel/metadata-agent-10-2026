"""
Run every test against the same, known configuration.

Settings are read from the process environment and from `.env`, and `src.main`
builds its settings at import time. Without this, a developer's exported
METADATA_AGENT_* values, a local `.env`, or real API keys decide whether a test
passes — and real keys would reach code under test. This runs before any test
module imports `src`, so even import-time settings see the clean environment.
"""

import os

# Credentials the settings read without the METADATA_AGENT_ prefix (aliases)
# plus the variants a developer environment typically holds.
_UNPREFIXED = (
    "B_API_KEY",
    "B_API_KEY_PROD",
    "B_API_KEY_STAGING",
    "OPENAI_API_KEY",
    "WLO_GUEST_USERNAME",
    "WLO_GUEST_PASSWORD",
)

for _name in list(os.environ):
    if _name.startswith("METADATA_AGENT_") or _name in _UNPREFIXED:
        del os.environ[_name]

# A placeholder, never a real key: code paths that need *a* static key get one,
# and nothing in the test run can authenticate anywhere.
os.environ["B_API_KEY"] = "test-b-api-key"

from src.config import Settings  # noqa: E402

# A developer's .env must not leak in either.
Settings.model_config["env_file"] = None
