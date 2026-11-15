"""
/upload/verify compares what the write path wrote, in every body form
(audit 14.11., R-07, R-08).

- The nested form ({"metadata": {...}, "metadataset": ...}), which the web
  component sends, was compared as if it had no fields: 0 matches, 0
  mismatches, whatever the node held.
- A correct upload showed differences that were none: a `ccm:custom_license`
  the write path had turned into key and version was "missing", `oeh:new_lrt`
  (written as `ccm:oeh_lrt`) "not written" under schema 2.0.0, and nine of the
  fourteen upload options counted as fields.
"""

import pytest

from repository_fake import FakeRepository, Response, install
from src.api.routers import upload as upload_router
from src.services.repository_diff import compute_diff, properties_to_flat
from src.services.repository_licenses import transform_license
from src.services.repository_service import RepositoryService
from src.services.repository_support import UPLOAD_OPTION_KEYS
from src.services.repository_values import (
    normalize_for_repo,
    transform_author_to_vcard,
    transform_lrt,
)
from src.utils.schema_loader import get_repo_fields

CTX, VER, SCHEMA = "default", "2.0.0", "learning_material.json"
NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
ARBEITSBLATT = (
    "http://w3id.org/openeduhub/vocabs/new_lrt/36e68792-6159-481d-a97b-2c00901f4f78"
)
MARKERS = {"contextName": CTX, "schemaVersion": VER, "metadataset": SCHEMA}
FIELDS = {"cclom:title": "Bruchrechnung", "ccm:wwwurl": "https://example.org/b"}
OTHER_NODE = {"cclom:title": "Etwas ganz anderes", "ccm:wwwurl": FIELDS["ccm:wwwurl"]}


def _statuses(diff):
    return {entry["field_id"]: entry["status"] for entry in diff}


# ------------------------------------------------------------------- R-07


@pytest.mark.parametrize(
    "nested",
    [
        {**MARKERS, "metadata": dict(FIELDS)},  # markers beside the fields
        {"metadata": {**MARKERS, **FIELDS}},  # markers among them (web component)
        {"metadata": {**MARKERS, "metadata": dict(FIELDS)}},  # nested twice
    ],
)
def test_the_nested_form_gives_the_same_diff_as_the_flat_one(nested):
    flat = compute_diff({**MARKERS, **FIELDS}, OTHER_NODE, CTX, VER)

    assert compute_diff(nested, OTHER_NODE, CTX, VER) == flat
    assert flat[1]["mismatch"] == 1 and flat[1]["match"] == 1


class _Node(FakeRepository):
    properties: dict = {}

    async def answer(self, method, url, body):
        return Response(200, {"node": {"properties": type(self).properties}})


async def _verify(expected, properties):
    _Node.properties = properties
    return await RepositoryService("user", "password").verify_node(
        NODE_ID, expected_metadata=expected, context="default", version="latest"
    )


async def test_verify_finds_the_mismatch_in_a_nested_body(monkeypatch):
    install(monkeypatch, _Node)
    properties = {key: [value] for key, value in OTHER_NODE.items()}

    flat = await _verify({**MARKERS, **FIELDS}, properties)
    # The router reads context and version beside `metadata`; here they are
    # inside it, as the web component sends them.
    nested = await _verify({"metadata": {**MARKERS, **FIELDS}}, properties)

    assert nested["summary"] == flat["summary"]
    assert nested["diff"] == flat["diff"]
    assert nested["summary"]["mismatch"] == 1


# ------------------------------------------------------------------- R-08


def _written(fields: dict) -> dict:
    """The properties the write path puts on the node for these fields."""
    normalized = normalize_for_repo(fields, get_repo_fields(CTX, VER, SCHEMA))
    transform_license(normalized, fields)
    transform_author_to_vcard(normalized)
    transform_lrt(normalized, fields)
    return normalized


def test_a_correct_upload_shows_no_difference():
    fields = {
        **FIELDS,
        "ccm:custom_license": "https://creativecommons.org/licenses/by-sa/4.0/",
        "oeh:new_lrt": [ARBEITSBLATT],
    }
    written = _written(fields)

    diff, summary = compute_diff(
        {**MARKERS, **fields}, properties_to_flat(written), CTX, VER, written
    )

    assert summary["missing_in_repo"] == 0, diff
    assert summary["not_written"] == 0, diff
    assert summary["mismatch"] == 0, diff
    assert summary["extra_in_repo"] == 0, diff
    assert _statuses(diff)["ccm:custom_license"] == "match"
    assert _statuses(diff)["oeh:new_lrt"] == "match"


def test_a_licence_link_written_as_another_key_is_a_mismatch():
    diff, _ = compute_diff(
        {
            **MARKERS,
            "ccm:custom_license": "https://creativecommons.org/licenses/by-sa/4.0/",
        },
        {"ccm:commonlicense_key": "CC_BY", "ccm:commonlicense_cc_version": "4.0"},
        CTX,
        VER,
    )

    assert _statuses(diff)["ccm:custom_license"] == "mismatch"


def test_a_licence_text_that_is_not_on_the_node_is_still_missing():
    diff, _ = compute_diff(
        {**MARKERS, "ccm:custom_license": "Alle Rechte vorbehalten"}, {}, CTX, VER
    )

    assert _statuses(diff)["ccm:custom_license"] == "missing_in_repo"


def test_the_old_lrt_name_is_looked_up_under_the_property_it_is_written_as():
    diff, summary = compute_diff(
        {**MARKERS, "oeh:new_lrt": [ARBEITSBLATT]}, {}, CTX, VER
    )

    # Not on the node: a loss, not "never eligible".
    assert _statuses(diff)["oeh:new_lrt"] == "missing_in_repo"
    assert summary["not_written"] == 0


def test_no_upload_option_counts_as_a_field():
    options = {key: "x" for key in upload_router._UPLOAD_OPTIONS}

    diff, summary = compute_diff({**MARKERS, **FIELDS, **options}, FIELDS, CTX, VER)

    assert summary["not_written"] == 0, diff
    assert set(_statuses(diff)) == set(FIELDS)


def test_the_option_list_is_the_one_the_upload_route_lifts_out():
    assert set(UPLOAD_OPTION_KEYS) == set(upload_router._UPLOAD_OPTIONS)
