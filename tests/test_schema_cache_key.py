"""
load_schema caches per resolved version, not per spelling of it.

"latest", "2.0.0" and "v2.0.0" name the same file but were cached three times
(lru_cache keyed by the raw argument), so the 100 slots filled with copies.
"""

from src.utils.schema_loader import get_latest_version, load_schema


def test_every_spelling_of_a_version_shares_one_cache_entry():
    latest = get_latest_version("default")

    by_name = load_schema("default", "latest", "core.json")

    assert load_schema("default", latest, "core.json") is by_name
    assert load_schema("default", f"v{latest}", "core.json") is by_name
    assert load_schema("default", "LATEST", "core.json") is by_name
