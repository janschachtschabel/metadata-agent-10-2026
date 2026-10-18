"""
Vocabulary values the caller or the node brings are not matched again (final audit 18.10.).

_post_process normalised every value of the record, also existing_metadata and
the node's own properties. Closed vocabularies drop what is no concept, so a
legacy concept the repository still holds vanished from the answer without a
word - and with 1,000 invalid caller values the fuzzy matching blocked /generate
for 3.4 s. The caller's and the node's values are now matched exactly, as in
2.1.1 (URI, value, label or altLabel in any language -> the concept), without
fuzzy matching, and what matches no concept is kept unchanged. The values the
LLM extracted in this run keep the full matching; datatype normalisation
(numbers, dates) stays as in 2.1.1.
"""

import time

import pytest

from src.services import field_normalizer as field_normalizer_module
from src.services.metadata_service import MetadataService

PHYSIK = "http://w3id.org/openeduhub/vocabs/discipline/460"
LEGACY = "http://w3id.org/openeduhub/vocabs/discipline/legacy-999"


def _service(monkeypatch, extracted):
    service = MetadataService()

    async def extract_fields_parallel(**kwargs):
        return {
            "values": dict(extracted),
            "errors": [],
            "first_error": None,
            "fatal_error": None,
            "skipped": 0,
        }

    monkeypatch.setattr(
        service.llm_service, "extract_fields_parallel", extract_fields_parallel
    )
    return service


async def _run(service, existing):
    result = await service.generate_metadata(
        text="Ein Text über Physik.",
        schema_file="event.json",
        version="2.0.0",
        existing_metadata=existing,
        enable_geocoding=False,
    )
    return result["metadata"]


@pytest.mark.asyncio
async def test_a_legacy_concept_of_the_caller_is_kept(monkeypatch):
    service = _service(monkeypatch, {"cclom:title": "Titel"})

    metadata = await _run(service, {"ccm:taxonid": [PHYSIK, LEGACY]})

    assert metadata["ccm:taxonid"] == [PHYSIK, LEGACY]


@pytest.mark.asyncio
async def test_many_invalid_caller_values_are_not_fuzzy_matched(monkeypatch):
    calls = {"count": 0}
    original = field_normalizer_module.FieldNormalizer._find_fuzzy_match

    def counting(self, value, concepts):
        calls["count"] += 1
        return original(self, value, concepts)

    monkeypatch.setattr(
        field_normalizer_module.FieldNormalizer, "_find_fuzzy_match", counting
    )
    service = _service(monkeypatch, {"cclom:title": "Titel"})
    values = [f"Unbekannt {i}" for i in range(1000)]

    started = time.perf_counter()
    metadata = await _run(service, {"ccm:taxonid": values})
    elapsed = time.perf_counter() - started

    assert calls["count"] == 0
    assert metadata["ccm:taxonid"] == values
    assert elapsed < 1.0, f"{elapsed:.2f} s for 1,000 caller values"


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["Physik", "physik ", "Physics"])
async def test_a_caller_label_becomes_the_concept_uri_as_in_2_1_1(monkeypatch, label):
    service = _service(monkeypatch, {"cclom:title": "Titel"})

    metadata = await _run(service, {"ccm:taxonid": [label, LEGACY]})

    assert metadata["ccm:taxonid"] == [PHYSIK, LEGACY]


@pytest.mark.asyncio
async def test_a_caller_typo_is_kept_not_fuzzy_matched(monkeypatch):
    service = _service(monkeypatch, {"cclom:title": "Titel"})

    metadata = await _run(service, {"ccm:taxonid": ["Pysik"]})

    assert metadata["ccm:taxonid"] == ["Pysik"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sent, kept", [("Danish", "da"), ("xx-legacy", "xx-legacy")])
async def test_a_caller_language_label_becomes_its_value(monkeypatch, sent, kept):
    # A value-keyed vocabulary (concepts {"value": "da"}), single-valued.
    service = _service(monkeypatch, {"cclom:title": "Titel"})

    metadata = await _run(service, {"cclom:general_language": sent})

    assert metadata["cclom:general_language"] == kept


@pytest.mark.asyncio
async def test_extracted_values_are_still_matched_to_the_vocabulary(monkeypatch):
    service = _service(monkeypatch, {"ccm:taxonid": ["Physik", "Quatschwert"]})

    metadata = await _run(service, {})

    assert metadata["ccm:taxonid"] == [PHYSIK]


@pytest.mark.asyncio
async def test_caller_numbers_are_still_normalised_as_in_2_1_1(monkeypatch):
    service = _service(monkeypatch, {"cclom:title": "Titel"})

    metadata = await _run(service, {"schema:maximumAttendeeCapacity": "200"})

    assert metadata["schema:maximumAttendeeCapacity"] == 200
