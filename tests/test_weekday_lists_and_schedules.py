"""
Opening hours and schedules as models and callers write them (audit 14.11.2026).

- L-43: several days in one string ("Mo, Mi, Fr", "Mo-Do, Sa", "Sa/So",
  "Montag und Mittwoch") collapsed to the first day; English short forms
  ("Mon", "Mon-Fri") were not read at all.
- L-30: text longer than any weekday entry stays as written (the runtime side
  is in test_regex_runtime.py).
- L-N: entries of the opening hours or the schedule that are no objects were
  dropped without a word - now with a line in processing.warnings; a schedule
  date in German spelling ("15.03.2025 14:00") gave none of the derived fields.
"""

import pytest

from src.services.metadata_service import MetadataService
from src.services.output_normalizer import OutputNormalizer


def _days(day):
    (hours,) = OutputNormalizer()._normalize_opening_hours([{"dayOfWeek": day}])
    return hours["dayOfWeek"]


@pytest.mark.parametrize(
    "day, expected",
    [
        ("Mo, Mi, Fr", ["MO", "WE", "FR"]),
        ("Mo-Do, Sa", ["MO", "TU", "WE", "TH", "SA"]),
        ("Sa/So", ["SA", "SU"]),
        ("Montag und Mittwoch", ["MO", "WE"]),
        ("Mo + Di", ["MO", "TU"]),
        ("Fr, Mo", ["FR", "MO"]),
        ("Mo-Mi, Di-Do", ["MO", "TU", "WE", "TH"]),
        ("Monday, Wednesday and Friday", ["MO", "WE", "FR"]),
        ("Montag, Dienstag, Mittwoch, Donnerstag, Freitag, Samstag und Sonntag",)
        + (["MO", "TU", "WE", "TH", "FR", "SA", "SU"],),
    ],
)
def test_a_list_of_days_in_one_string_gives_every_day(day, expected):
    assert _days(day) == expected


@pytest.mark.parametrize(
    "day, expected",
    [
        ("Mon", ["MO"]),
        ("Tue", ["TU"]),
        ("Tues", ["TU"]),
        ("wed", ["WE"]),
        ("Thu", ["TH"]),
        ("Thurs.", ["TH"]),
        ("FRI", ["FR"]),
        ("Sat", ["SA"]),
        ("Sun", ["SU"]),
        ("Mon-Fri", ["MO", "TU", "WE", "TH", "FR"]),
        ("Sat - Sun", ["SA", "SU"]),
        ("Mon, Wed, Fri", ["MO", "WE", "FR"]),
    ],
)
def test_english_short_forms_are_read(day, expected):
    assert _days(day) == expected


@pytest.mark.parametrize(
    "day, expected",
    [
        # One day and something else: the day, as before.
        ("Montag, 10 Uhr", ["MO"]),
        ("https://schema.org/Monday", ["MO"]),
        ("schema:Friday", ["FR"]),
        ("Dienstags", ["TU"]),
        # No weekday at all: as written.
        ("täglich", ["täglich"]),
        ("Sonderöffnung", ["Sonderöffnung"]),
        ("The sun terrace", ["The sun terrace"]),
        ("a, b", ["a, b"]),
    ],
)
def test_what_is_no_list_of_days_is_read_as_before(day, expected):
    assert _days(day) == expected


def test_text_longer_than_any_weekday_entry_stays_as_written():
    day = "Montag " + "und danach lange nichts " * 10

    assert len(day) > OutputNormalizer.MAX_DAY_TEXT
    assert _days(day) == [day]


# ------------------------------------------------------------ non-objects


@pytest.mark.parametrize(
    "field_id", ["schema:openingHoursSpecification", "schema:eventSchedule"]
)
def test_entries_that_are_no_objects_are_named_in_a_warning(field_id):
    warnings: list[str] = []
    metadata = {field_id: [{"opens": "09:00"}, "Samstag 10 Uhr", 7]}

    result = OutputNormalizer().normalize_output(metadata, warnings)

    assert len(result[field_id]) == 1
    assert warnings == [f"{field_id}: 2 Wert(e) ohne Objektform nicht übernommen."]


def test_a_list_of_objects_gives_no_warning():
    warnings: list[str] = []
    metadata = {
        "schema:openingHoursSpecification": [{"dayOfWeek": "Mo"}],
        "schema:eventSchedule": [{"startDate": "2025-03-15"}],
    }

    OutputNormalizer().normalize_output(metadata, warnings)

    assert warnings == []


@pytest.mark.asyncio
async def test_the_warning_reaches_the_processing_block(monkeypatch):
    service = MetadataService()

    async def extract_fields_parallel(**kwargs):
        return {"values": {"cclom:title": "Titel"}, "errors": [], "skipped": 0}

    monkeypatch.setattr(
        service.llm_service, "extract_fields_parallel", extract_fields_parallel
    )

    result = await service.generate_metadata(
        text="Ein Text.",
        schema_file="event.json",
        version="2.0.0",
        existing_metadata={"schema:eventSchedule": [{"startDate": "2025-03-15"}, 7]},
        enable_geocoding=False,
    )

    assert result["metadata"]["schema:eventSchedule"][0]["startDate"] == "2025-03-15"
    assert (
        "schema:eventSchedule: 1 Wert(e) ohne Objektform nicht übernommen."
        in result["processing"]["warnings"]
    )


# ------------------------------------------------------------ German dates


def _schedule(entry):
    (normalized,) = OutputNormalizer()._normalize_event_schedule([entry])
    return normalized


def test_a_german_start_date_with_time_fills_the_derived_fields():
    assert _schedule({"startDate": "15.03.2025 14:00"}) == {
        "startDate": "2025-03-15",
        "endDate": "2025-03-15",
        "byMonth": [3],
        "byMonthDay": [15],
        "byDay": ["SA"],
        "byHour": [14],
    }


def test_a_german_start_date_without_time_names_no_hour():
    assert _schedule({"startDate": "15. März 2025"}) == {
        "startDate": "2025-03-15",
        "endDate": "2025-03-15",
        "byMonth": [3],
        "byMonthDay": [15],
        "byDay": ["SA"],
    }


def test_a_german_end_date_is_written_like_the_start():
    normalized = _schedule({"startDate": "2025-03-15", "endDate": "16.03.2025 18:00"})

    assert normalized["endDate"] == "2025-03-16"


@pytest.mark.parametrize("start", ["30.02.2026", "irgendwann im März", "2025-03-15"])
def test_a_date_that_cannot_be_read_stays_as_written(start):
    assert _schedule({"startDate": start})["startDate"] == start
