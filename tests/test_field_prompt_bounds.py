"""
The current value of a field is bounded in its prompt (audit 14.11.2026, S-35).

The rest of S-20: a caller's existing_metadata went into the field prompt in
full - 5,000,000 characters gave a message of 5,001,083, once per field call
and retry. It is cut like the values of the first round in a correction block
(2,000 characters, then " …").

L-N: _existing_value_lines took the page text and never used it.
"""

import inspect
import json

import pytest

from src.api.input_resolver import MAX_PREVIOUS_VALUE_LENGTH
from src.services import llm_prompts
from src.services.llm_prompts import MAX_CURRENT_VALUE_LENGTH, PromptMixin

FIELD = {
    "id": "cclom:general_description",
    "label": {"de": "Beschreibung"},
    "system": {"datatype": "string"},
}
# Everything of the prompt that is not the value: task, datatype, rules, format.
FRAME = 1_000


def _prompt(existing_value, correction=False):
    with llm_prompts.correction_round(correction):
        return PromptMixin()._build_extraction_prompt(
            FIELD, "Der Text.", existing_value, "de"
        )


def test_the_bound_is_the_one_of_the_correction_block():
    assert MAX_CURRENT_VALUE_LENGTH == MAX_PREVIOUS_VALUE_LENGTH == 2_000


@pytest.mark.parametrize("correction", [False, True])
@pytest.mark.parametrize(
    "existing_value",
    [
        "x" * 5_000_000,
        ["y" * 500] * 10_000,
        {"name": "z" * 1_000_000},
        [{"name": "Ort", "address": {"streetAddress": "a" * 100_000}}],
    ],
    ids=["string", "list", "object", "nested"],
)
def test_a_huge_current_value_does_not_grow_the_prompt(existing_value, correction):
    prompt = _prompt(existing_value, correction)

    assert len(prompt) <= MAX_CURRENT_VALUE_LENGTH + len(" …") + FRAME
    assert " …\n" in prompt


def test_the_cut_value_keeps_its_first_2000_characters():
    prompt = _prompt("x" * 5_000_000)

    line = next(line for line in prompt.splitlines() if "AKTUELLER WERT" in line)
    assert line == '📋 AKTUELLER WERT: "' + "x" * MAX_CURRENT_VALUE_LENGTH + " …"


@pytest.mark.parametrize(
    "existing_value",
    ["Alter Titel", ["Alter Wert", {"name": "Ort"}], 0, False, "x" * 2_000],
)
def test_a_value_within_the_bound_is_shown_in_full(existing_value):
    prompt = _prompt(existing_value)

    assert (
        f"AKTUELLER WERT: {json.dumps(existing_value, ensure_ascii=False)}\n" in prompt
    )


def test_the_value_lines_take_no_page_text():
    parameters = inspect.signature(llm_prompts._existing_value_lines).parameters

    assert list(parameters) == ["existing_value", "label", "de"]
