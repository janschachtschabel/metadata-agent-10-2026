"""
The values of the first round are context for a correction, not a second source.

Audit 13.11., S-20: only the source was cut to make room; the block of
previous values went into the prompt whole - into each of about 50 field
calls. A body of a few megabytes in existing_metadata (the limit is 20 MB)
meant gigabytes sent to the LLM gateway on the caller's or the agent's key,
and the source cut down to nothing.
"""

from src.api.input_resolver import (
    MAX_PREVIOUS_LENGTH,
    MAX_PREVIOUS_VALUE_LENGTH,
    MAX_TEXT_LENGTH,
    PREVIOUS_VALUES_HEADER,
    with_correction,
)

SOURCE = "Die Seite. " * 2_000


def test_short_values_go_into_the_prompt_as_before():
    text, _ = with_correction(
        "Seite", {"cclom:title": "Alt", "kw": ["a", "b"], "_origins": {}}, "kürzer"
    )

    assert "  cclom:title: Alt\n  kw: a, b" in text
    assert "_origins" not in text


def test_a_huge_value_is_cut_and_the_source_stays():
    text, source = with_correction(
        SOURCE, {"cclom:general_description": "x" * 2_000_000}, "Titel kürzer"
    )

    assert len(text) <= MAX_TEXT_LENGTH
    assert source == SOURCE
    block = text.split(PREVIOUS_VALUES_HEADER)[1]
    assert block.count("x") == MAX_PREVIOUS_VALUE_LENGTH
    assert text.rstrip().endswith("Titel kürzer")


def test_many_fields_do_not_grow_the_block_without_bound():
    values = {f"feld:{i}": "y" * 500 for i in range(5_000)}

    text, source = with_correction(SOURCE, values, "Titel kürzer")

    block = text.split(PREVIOUS_VALUES_HEADER)[1]
    assert len(block) <= MAX_PREVIOUS_LENGTH + 500
    assert "weitere Felder ausgelassen" in block
    assert source == SOURCE


def test_a_huge_key_is_cut_too():
    text, _ = with_correction("Seite", {"k" * 100_000: "Wert"}, "kürzer")

    assert len(text) < 5_000
