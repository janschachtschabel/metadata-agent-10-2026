"""
Two small normalizations that wrote wrong values.

- CC0 exists only as version 1.0; the default "4.0" for every CC key made the
  repository record a licence version that was never published.
- Opening hours like "Mo-Fr" matched "mo" first and became Monday only.
"""

import pytest

from src.services.output_normalizer import OutputNormalizer
from src.services.repository_licenses import transform_license


@pytest.mark.parametrize(
    "key, version", [("CC_0", "1.0"), ("CC0", "1.0"), ("CC_BY", "4.0")]
)
def test_the_default_version_matches_the_licence(key, version):
    normalized = {"ccm:commonlicense_key": [key]}

    transform_license(normalized, {})

    assert normalized["ccm:commonlicense_cc_version"] == [version]


@pytest.mark.parametrize(
    "day, expected",
    [
        ("Mo-Fr", ["MO", "TU", "WE", "TH", "FR"]),
        ("Montag bis Freitag", ["MO", "TU", "WE", "TH", "FR"]),
        ("Mo–Mi", ["MO", "TU", "WE"]),
        ("Samstag", ["SA"]),
        (["Sa", "So"], ["SA", "SU"]),
        (["Mo-Mi", "Fr"], ["MO", "TU", "WE", "FR"]),
    ],
)
def test_a_day_range_becomes_every_day_in_it(day, expected):
    (hours,) = OutputNormalizer()._normalize_opening_hours([{"dayOfWeek": day}])

    assert hours["dayOfWeek"] == expected


def test_an_array_field_stays_a_list_even_without_multiple():
    # core.json declares ccm:oeh_extendedType as datatype array, multiple false;
    # the normalizer used to unwrap it into a bare string.
    from src.services.field_normalizer import FieldNormalizer

    uri = "http://w3id.org/openeduhub/vocabs/contentTypes/event"
    schema = {"datatype": "array", "multiple": False, "items": {"datatype": "uri"}}

    assert FieldNormalizer().normalize_field_value([uri], schema) == [uri]
