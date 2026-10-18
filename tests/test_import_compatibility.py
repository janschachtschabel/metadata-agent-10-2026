"""
The public names of the split modules stay importable from where they were.

3.0.0 moved most of llm_service.py and repository_service.py into smaller
modules. Code outside the repository (scripts, evaluations) imports from the
old places; every public name those modules defined in 2.1.1 is re-exported.
Names with a leading underscore were internal and are not covered.
"""

import importlib

import pytest

PUBLIC_IN_2_1_1 = {
    "src.services.repository_service": [
        "DISCARD_TIMEOUT_SECONDS",
        "EXTENDED_TYPE_TO_NEW_LRT",
        "MAX_PREVIEW_BYTES",
        "MAX_PREVIEW_REDIRECTS",
        "NODE_EDIT_WINDOW",
        "PREVIEW_USER_AGENT",
        "PreviewError",
        "RepositoryService",
        "build_auth_header",
        "derive_node_name",
        "duplicate_skipped",
        "get_repository_service",
        "node_created_at",
        "resolve_preview",
    ],
    "src.services.llm_service": [
        "ContentTypeDetection",
        "LLMService",
        "PROMPT_CACHE_PROVIDERS",
        "ProviderGate",
        "RateLimiter",
        "get_llm_service",
        "get_provider_gate",
        "reset_provider_gates",
        "retry_after_seconds",
        "router_failure",
        "uses_reasoning_parameters",
    ],
}


@pytest.mark.parametrize("module", sorted(PUBLIC_IN_2_1_1))
def test_public_names_of_2_1_1_are_still_importable(module):
    loaded = importlib.import_module(module)

    missing = [name for name in PUBLIC_IN_2_1_1[module] if not hasattr(loaded, name)]

    assert not missing, f"{module} exportiert nicht mehr: {missing}"
