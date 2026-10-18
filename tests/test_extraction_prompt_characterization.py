"""
The field prompt as it was before _build_extraction_prompt was taken apart (audit M-01).

The prompt decides what the model returns, and it is part of the prompt-cache
prefix logic. Splitting the function (cyclomatic complexity 31) must not change
a single character for any field of any schema, in either language, on any
retry, with or without an existing value. The fixture holds one SHA-256 per
schema field over all its variants, written from the code before the split.
"""

import hashlib
import json
from pathlib import Path

import pytest

from src.services.llm_prompts import PromptMixin, correction_round
from src.utils.schema_loader import get_content_types, get_latest_version, load_schema

FIXTURE = Path(__file__).parent / "fixtures" / "extraction_prompt_hashes.json"
CONTEXT = "default"

TEXT_PLAIN = "Ein Workshop am 3. Mai 2026 in Weimar."
TEXT_CORRECTION = "Quelltext.\n\n--- KORREKTUR / UPDATE ---\nTitel kürzer."
EXISTING = {
    "none": None,
    "update": ["Alter Wert", {"name": "Ort"}],
    "correction": "Alter Wert",
}


def combinations():
    version = get_latest_version(CONTEXT)
    files = ["core.json"] + sorted(
        ct["schema_file"] for ct in get_content_types(CONTEXT, version)
    )
    for schema_file in files:
        for field in load_schema(CONTEXT, version, schema_file).get("fields", []):
            for language in ("de", "en"):
                for retry in (0, 1, 2):
                    for mode, existing in EXISTING.items():
                        key = (
                            f"{schema_file}|{field.get('id')}|{language}|{retry}|{mode}"
                        )
                        yield key, field, language, retry, existing, mode


def prompt_hashes() -> dict[str, str]:
    """One SHA-256 per schema field over its 18 variants (language, retry, existing value)."""
    builder = PromptMixin()
    digests: dict[str, "hashlib._Hash"] = {}
    for key, field, language, retry, existing, mode in combinations():
        text = TEXT_CORRECTION if mode == "correction" else TEXT_PLAIN
        # Retry 2 was the last attempt in the code the fixture was written from
        # (as with llm_max_retries=2); since audit L-14 the caller says so.
        # A correction round is marked by /generate (audit L-13), no longer
        # recognised in the text.
        with correction_round(mode == "correction"):
            prompt = builder._build_extraction_prompt(
                field, text, existing, language, retry, last_attempt=retry == 2
            )
        field_key = key.rsplit("|", 3)[0]
        digests.setdefault(field_key, hashlib.sha256()).update(
            f"{key}\n{prompt}\n".encode("utf-8")
        )
    return {key: digest.hexdigest() for key, digest in digests.items()}


def test_every_field_prompt_is_unchanged():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = prompt_hashes()

    assert actual.keys() == expected.keys()
    changed = [key for key in expected if actual[key] != expected[key]]
    assert changed == [], f"{len(changed)} prompts changed, e.g. {changed[:5]}"


@pytest.mark.parametrize("language", ["de", "en"])
def test_the_fixture_covers_every_branch(language):
    """Vocabulary, examples, object structure, required and optional fields occur."""
    fields = [
        c[1]
        for c in combinations()
        if c[2] == language and c[3] == 0 and c[5] == "none"
    ]
    system = [f.get("system", {}) for f in fields]

    assert any(s.get("vocabulary", {}).get("concepts") for s in system)
    assert any((s.get("items") or {}).get("datatype") == "object" for s in system)
    assert any(s.get("required") for s in system) and any(
        not s.get("required") for s in system
    )
    assert any(f.get("examples") for f in fields)
    assert any(f.get("prompt") for f in fields)
