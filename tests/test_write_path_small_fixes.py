"""
Small corrections of the write path (audit 14.11., list R-N, Q-06).

- A `cm:name` the caller supplies is cleaned like one derived from the title:
  Alfresco refuses the same characters in both.
- A 409 for a node that is already referenced in a collection counts as linked.
- `extended_text` is cut to the length every other source text is cut to.
- The shape check behind the extended data sees the fields of a nested body.
- The class docstring of RepositoryService names the steps in the order they
  run.
"""

import json

import pytest

from repository_fake import NODE_ID, FakeRepository, Response, install, requests
from src.api import input_resolver
from src.services import repository_metadata as metadata_module
from src.services.repository_metadata import _extended_fields
from src.services.repository_service import RepositoryService

COLLECTION = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
}


async def _upload(monkeypatch, client_class=FakeRepository, **options):
    calls = install(monkeypatch, client_class)
    options.setdefault("write_extended_data", False)
    result = await RepositoryService("user", "password").upload_metadata(
        metadata=dict(METADATA),
        check_duplicates=False,
        context="default",
        version="2.0.0",
        **options,
    )
    return result, calls


# ---------------------------------------------------------------- cm:name


@pytest.mark.parametrize(
    "supplied, written",
    [
        ('Arbeits/blatt: "Brüche"?', "Arbeits_blatt_ _Brüche__"),
        ("  Bericht 2026.  ", "Bericht 2026"),
        (["Zeile\n zwei*"], "Zeile zwei_"),
        ("x" * 300, "x" * 200),
        ("Eigener Name", "Eigener Name"),
    ],
)
async def test_a_supplied_name_is_cleaned_like_a_derived_one(
    monkeypatch, supplied, written
):
    calls = install(monkeypatch)

    await RepositoryService("user", "password").create_node(
        {"cclom:title": "Titel", "cm:name": supplied}
    )

    assert requests(calls, "POST", "/children?")[0]["json"]["cm:name"] == [written]


# -------------------------------------------------- collections: 409


class _AlreadyReferenced(FakeRepository):
    status = 409

    async def answer(self, method, url, body):
        if method == "PUT" and "/collections/" in url:
            return Response(type(self).status, {"message": "Duplicate node"})
        return await super().answer(method, url, body)


async def test_a_node_already_in_the_collection_counts_as_linked(monkeypatch):
    _AlreadyReferenced.status = 409

    result, _ = await _upload(
        monkeypatch, _AlreadyReferenced, collection_ids=[COLLECTION]
    )

    assert result["collections"] == [{"collectionId": COLLECTION, "success": True}]
    assert "error" not in result


async def test_another_refusal_of_a_collection_is_still_reported(monkeypatch):
    _AlreadyReferenced.status = 403

    result, _ = await _upload(
        monkeypatch, _AlreadyReferenced, collection_ids=[COLLECTION]
    )

    assert result["collections"][0]["success"] is False
    assert COLLECTION in result["error"]


# ------------------------------------------------------ extended_text


def test_the_limit_of_the_raw_text_is_the_one_source_texts_are_cut_to():
    assert metadata_module.MAX_EXTENDED_TEXT_LENGTH == input_resolver.MAX_TEXT_LENGTH


def _extended_write(calls):
    return next(
        c["json"] for c in requests(calls, "POST", "EXTENDED_DATA") if c["json"]
    )


async def test_a_raw_text_over_the_limit_is_cut_and_the_answer_says_so(monkeypatch):
    monkeypatch.setattr(metadata_module, "MAX_EXTENDED_TEXT_LENGTH", 1000)

    result, calls = await _upload(
        monkeypatch, write_extended_data=True, extended_text="a" * 5000
    )

    assert _extended_write(calls)["ccm:oeh_extendedText"] == ["a" * 1000]
    assert result["success"] is True
    assert "ccm:oeh_extendedText" in result["error"]
    assert "gekürzt" in result["error"]


async def test_a_raw_text_within_the_limit_is_written_whole(monkeypatch):
    result, calls = await _upload(
        monkeypatch, write_extended_data=True, extended_text="Rohtext der Seite"
    )

    assert _extended_write(calls)["ccm:oeh_extendedText"] == ["Rohtext der Seite"]
    assert "error" not in result


# ------------------------------------------- shape check, nested body

EVENT = {
    "contextName": "mds_oeh",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
}
BAD_SHAPE = {"cclom:title": "Konzert", "schema:organizer": ["TEEKAYART"]}


@pytest.mark.parametrize(
    "body",
    [
        {**EVENT, **BAD_SHAPE},
        {**EVENT, "metadata": dict(BAD_SHAPE)},  # the web component's form
    ],
)
def test_the_extended_data_is_refused_for_a_wrong_shape_in_any_body_form(body):
    with pytest.raises(ValueError, match="schema:organizer"):
        _extended_fields(body, "mds_oeh", "2.0.0", None)


def test_a_nested_body_of_the_right_shape_is_written():
    body = {**EVENT, "metadata": {"cclom:title": "Konzert"}}

    extended = _extended_fields(body, "mds_oeh", "2.0.0", None)

    assert json.loads(extended["ccm:oeh_extendedData"][0]) == {"cclom:title": "Konzert"}


# ------------------------------------------------------------------- Q-06


def test_the_class_docstring_names_the_steps_in_the_order_they_run(monkeypatch):
    doc = RepositoryService.__doc__
    named = [
        doc.index("Check for duplicates"),
        doc.index("Create the node"),
        doc.index("set the metadata"),
        doc.index("Write the extended fields"),
        doc.index("Store the preview image"),
        doc.index("Reference the node in collections"),
        doc.index("Run the review workflow steps"),
        doc.index("Read the finished node back"),
    ]
    assert named == sorted(named), doc


async def test_the_requests_go_out_in_the_order_the_docstring_names(monkeypatch):
    async def image():
        return b"\x89PNG\r\n\x1a\n", "image/png"

    calls = install(monkeypatch)
    await RepositoryService("user", "password").upload_metadata(
        metadata={**METADATA, "ccm:wwwurl": "https://example.org/b"},
        check_duplicates=True,
        context="default",
        version="2.0.0",
        write_extended_data=True,
        collection_ids=[COLLECTION],
        preview_source=image,
        return_full_node=True,
    )

    order = [
        next(i for i, c in enumerate(calls) if part in c["url"])
        for part in (
            "ngsearch",
            "/children?type=",
            "METADATA_UPDATE",
            "EXTENDED_DATA",
            "/preview",
            "/collections/",
            "/workflow",
        )
    ]
    assert order == sorted(order)
    assert calls[-1]["method"] == "GET" and f"/{NODE_ID}/metadata" in calls[-1]["url"]
