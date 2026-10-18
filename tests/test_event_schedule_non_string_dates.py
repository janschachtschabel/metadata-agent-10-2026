"""
A startDate or endDate that is no string in schema:eventSchedule.

re.match on a number or list raised TypeError, and _post_process skipped the
whole output normalisation (opening hours, access services, ...) with one
warning. Such a date is left as it is; everything else is still shaped.
"""

import pytest

from src.services.output_normalizer import OutputNormalizer


@pytest.mark.parametrize(
    "entry",
    [
        {"startDate": 20260512},
        {"startDate": ["2026-05-12T10:00"]},
        {"startDate": "2026-05-12T10:00", "endDate": ["2026-05-12T12:00"]},
    ],
)
def test_a_non_string_date_does_not_stop_the_output_normalisation(entry):
    metadata = {
        "schema:eventSchedule": [entry],
        "schema:accessService": ["Barrierefreier Zugang"],
    }

    result = OutputNormalizer().normalize_output(metadata)

    assert result["schema:accessService"] == [
        {"serviceType": "Barrierefreiheit", "description": "Barrierefreier Zugang"}
    ]
    assert len(result["schema:eventSchedule"]) == 1


def test_a_string_start_date_still_fills_the_schedule():
    result = OutputNormalizer().normalize_output(
        {"schema:eventSchedule": [{"startDate": "2026-05-12T10:00"}]}
    )

    entry = result["schema:eventSchedule"][0]
    assert entry["startDate"] == "2026-05-12"
    assert entry["byDay"] == ["TU"]
    assert entry["byHour"] == [10]
