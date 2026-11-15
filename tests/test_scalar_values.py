"""
Booleans, URLs and coordinates on both normalisation paths (audit 14.11.2026, Q-13, Q-15, Q-16).

- Q-13: the boolean and the coordinate normalisation of the model answer path
  had no test (llm_values at 62 % coverage).
- Q-15: the model path and FieldNormalizer each had a copy of the URL and the
  boolean rule; the boolean copies disagreed about numbers (7 was true on one
  path, 1 was no boolean on the other). One implementation, one rule: 1 and 0
  are true and false, any other number is no boolean.
- Q-16: the datatypes `url` and `duration` are declared by no schema; their
  branches are gone.
"""

import json
import math
from pathlib import Path

import pytest

from src.services import field_normalizer, llm_auth, llm_values
from src.services.field_normalizer import FieldNormalizer
from src.services.llm_dates_numbers import DateNumberMixin
from src.services.llm_values import ValueNormalizerMixin
from src.services.output_normalizer import OutputNormalizer
from src.utils.scalar_values import normalize_boolean, normalize_url

SCHEMATA = Path(__file__).resolve().parents[1] / "src" / "schemata"


class _Values(ValueNormalizerMixin, DateNumberMixin):
    """The value normalisation of LLMService without its settings and client."""


def _model(value, datatype="string", field_id="x:field"):
    return _Values()._normalize_value(
        value, {"id": field_id, "system": {"datatype": datatype}}
    )


def _caller(value, datatype="string"):
    return FieldNormalizer().normalize_field_value(value, {"datatype": datatype})


# ------------------------------------------------------------ booleans

BOOLEANS = [
    (True, True),
    (False, False),
    ("ja", True),
    ("Yes", True),
    (" wahr ", True),
    ("TRUE", True),
    ("1", True),
    ("nein", False),
    ("No", False),
    ("falsch", False),
    ("false", False),
    ("0", False),
    (1, True),
    (0, False),
    (1.0, True),
    (0.0, False),
    (2, None),
    (7, None),
    (-1, None),
    (0.5, None),
    (float("nan"), None),
    ("vielleicht", None),
    ("2", None),
    ({"value": True}, None),
]


@pytest.mark.parametrize("value, expected", BOOLEANS, ids=repr)
def test_a_boolean_is_read_the_same_on_both_paths(value, expected):
    assert normalize_boolean(value) is expected
    assert _model(value, "boolean") is expected
    assert _caller(value, "boolean") is expected


def test_a_list_of_boolean_answers_is_read_entry_by_entry():
    assert _model(["ja", 0, "x"], "boolean") == [True, False, None]


# ------------------------------------------------------------ URLs

URLS = [
    ("https://example.org/a", "https://example.org/a"),
    ("HTTP://example.org", "HTTP://example.org"),
    ("example.org", "https://example.org"),
    (" www.example.org/pfad ", "https://www.example.org/pfad"),
    ("kein link", "kein link"),
    ("mailto:a@example.org", "mailto:a@example.org"),
    (7, None),
    (["example.org"], None),
]


@pytest.mark.parametrize("value, expected", URLS, ids=repr)
def test_a_url_is_read_the_same_on_both_paths(value, expected):
    assert normalize_url(value) == expected
    if not isinstance(value, list):
        assert _model(value, "uri") == expected
        assert _caller(value, "uri") == expected


# ------------------------------------------------------------ coordinates

COORDINATES = [
    ("geo:latitude", 50.97, 50.97),
    ("geo:latitude", "50,9793301", 50.9793301),
    ("geo:latitude", "50.9793301°", 50.9793301),
    ("geo:latitude", " 50.97 ", 50.97),
    ("geo:latitude", 50.123456789, 50.1234568),
    ("geo:latitude", 0, 0.0),
    ("geo:latitude", "0", 0.0),
    ("geo:latitude", 90, 90.0),
    ("geo:latitude", -90, -90.0),
    ("geo:latitude", 91, None),
    ("geo:latitude", "-90.5", None),
    ("geo:latitude", "Nord", None),
    ("geo:latitude", "", None),
    ("geo:latitude", {"lat": 50}, None),
    ("geo:latitude", True, None),
    ("geo:latitude", float("nan"), None),
    ("geo:latitude", "nan", None),
    ("geo:latitude", "inf", None),
    ("geo:longitude", 11.33, 11.33),
    ("geo:longitude", "11,3297921", 11.3297921),
    ("geo:longitude", 180, 180.0),
    ("geo:longitude", -180, -180.0),
    ("geo:longitude", 181, None),
    ("geo:longitude", 120, 120.0),
    ("schema:Latitude", "50,5", 50.5),
]


@pytest.mark.parametrize("field_id, value, expected", COORDINATES, ids=repr)
def test_a_coordinate_from_the_model_is_a_number_in_range(field_id, value, expected):
    result = _model(value, "string", field_id)

    assert result == expected
    assert result is None or (isinstance(result, float) and math.isfinite(result))


def test_a_list_of_coordinates_is_read_entry_by_entry():
    assert _model([50.9, "x", "11,3"], "string", "geo:latitude") == [50.9, None, 11.3]


def test_a_field_that_only_sounds_like_a_coordinate_is_left_alone():
    assert _model("200", "string", "schema:maximumAttendeeCapacity") == "200"


def test_a_number_field_named_latitude_is_a_number_first():
    # The datatype decides before the name: no range check on a number field.
    assert _model("91", "number", "geo:latitude") == 91.0


# ------------------------------------------------------------ Q-16 dead code


def _datatypes(fields):
    for field in fields or []:
        system = field.get("system") or {}
        items = system.get("items") or {}
        yield system.get("datatype")
        yield items.get("datatype")
        for variant in [
            *(items.get("variants") or []),
            *(system.get("variants") or []),
        ]:
            yield from _datatypes(variant.get("fields"))
        yield from _datatypes(field.get("fields"))


def test_no_schema_declares_the_datatypes_that_lost_their_branch():
    declared = set()
    for path in SCHEMATA.glob("*/v*/*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(schema, dict):
            declared.update(_datatypes(schema.get("fields")))

    assert "uri" in declared
    assert not declared & {"url", "duration"}


def test_the_dead_code_is_gone():
    assert not hasattr(FieldNormalizer, "_normalize_duration")
    assert not hasattr(OutputNormalizer, "_normalize_person_array")
    assert not hasattr(llm_auth, "AUTH_MODES")
    # One copy of each scalar rule: neither class has its own any more.
    for owner in (FieldNormalizer, ValueNormalizerMixin):
        assert not hasattr(owner, "_normalize_url")
        assert not hasattr(owner, "_normalize_boolean")
    for module in (field_normalizer, llm_values):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert '"url"' not in source and '"duration"' not in source
