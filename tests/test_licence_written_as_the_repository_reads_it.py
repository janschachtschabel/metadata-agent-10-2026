"""
A licence is written in the form the repository resolves, or kept as text
(audit 14.11., R-09 and list R-N).

- The retired spellings ('CC BY-SA', 'CC0') passed the validation and were
  stored verbatim; the repository does not read them as a licence
  (WLO-REPO-FELDER.md, "nur die Unterstrich-Form zählt").
- A ported licence link ('/3.0/de/') lost its jurisdiction: key and version
  cannot hold it, so the link stays in ccm:custom_license.
- Any URL that ended in a key was read as a vocabulary URI
  ('…/nutzungsbedingungen/PDM' became the public domain mark).
- When the bulk write is refused and the fields go out one by one, key and
  version are written together or not at all.
"""

import pytest

from repository_fake import FakeRepository, Response, install, requests
from src.services.repository_licenses import (
    VALID_LICENSE_KEYS,
    license_from_custom,
    transform_license,
)
from src.services.repository_service import RepositoryService

VOCABS = "http://w3id.org/openeduhub/vocabs"
KEY, VERSION = "ccm:commonlicense_key", "ccm:commonlicense_cc_version"
CUSTOM = "ccm:custom_license"


def _written(original: dict) -> dict:
    normalized = {key: [value] for key, value in original.items()}
    transform_license(normalized, original)
    return normalized


# ------------------------------------------------------ retired spellings


@pytest.mark.parametrize(
    "retired, resolving",
    [
        ("CC0", "CC_0"),
        ("CC BY", "CC_BY"),
        ("CC BY-SA", "CC_BY_SA"),
        ("CC BY-ND", "CC_BY_ND"),
        ("CC BY-NC", "CC_BY_NC"),
        ("CC BY-NC-SA", "CC_BY_NC_SA"),
        ("CC BY-NC-ND", "CC_BY_NC_ND"),
        ("cc by-sa", "CC_BY_SA"),
        (" CC-BY-SA ", "CC_BY_SA"),
    ],
)
def test_a_retired_spelling_is_written_as_the_key_the_repository_resolves(
    retired, resolving
):
    assert _written({KEY: retired})[KEY] == [resolving]
    assert retired not in VALID_LICENSE_KEYS or retired == resolving


def test_the_key_keeps_the_version_that_came_with_it():
    assert _written({KEY: "CC BY-SA", VERSION: "3.0"}) == {
        KEY: ["CC_BY_SA"],
        VERSION: ["3.0"],
    }


@pytest.mark.parametrize("key", ["CC BY-SA 4.0", "Namensnennung", "GPL"])
def test_a_key_that_is_none_is_still_dropped(key):
    assert KEY not in _written({KEY: key})


# -------------------------------------------------------- ported licences


@pytest.mark.parametrize(
    "link, key, version",
    [
        ("https://creativecommons.org/licenses/by-sa/3.0/de/", "CC_BY_SA", "3.0"),
        ("https://creativecommons.org/licenses/by-nc-sa/3.0/de", "CC_BY_NC_SA", "3.0"),
        ("http://creativecommons.org/licenses/by/2.0/uk/legalcode", "CC_BY", "2.0"),
        ("https://creativecommons.org/licenses/by/3.0/igo/", "CC_BY", "3.0"),
    ],
)
def test_a_ported_licence_keeps_its_link(link, key, version):
    # Key and version cannot say "Germany": the link stays for the editors.
    assert _written({CUSTOM: link}) == {
        KEY: [key],
        VERSION: [version],
        CUSTOM: [link],
    }
    assert license_from_custom(link)[1] is False


@pytest.mark.parametrize(
    "link",
    [
        "https://creativecommons.org/licenses/by-sa/4.0/",
        "https://creativecommons.org/licenses/by-sa/4.0/deed.de",
        "https://creativecommons.org/licenses/by-sa/4.0/legalcode",
        "https://creativecommons.org/licenses/by-sa/4.0/legalcode.de",
        "https://creativecommons.org/licenses/by-sa/3.0/deed.en",
    ],
)
def test_an_unported_licence_link_is_replaced_by_key_and_version(link):
    written = _written({CUSTOM: link})

    assert CUSTOM not in written
    assert written[KEY] == ["CC_BY_SA"]


# ----------------------------------------------------- vocabulary URIs


@pytest.mark.parametrize(
    "uri, expected",
    [
        (f"{VOCABS}/licenses/CC_BY_SA_40", {KEY: ["CC_BY_SA"], VERSION: ["4.0"]}),
        ("https://w3id.org/openeduhub/vocabs/license/PDM", {KEY: ["PDM"]}),
        (f"{VOCABS}/licenses/OTHER", {KEY: ["CUSTOM"]}),
    ],
)
def test_a_uri_of_the_openeduhub_vocabulary_becomes_the_key(uri, expected):
    assert _written({CUSTOM: uri}) == expected


@pytest.mark.parametrize(
    "link",
    [
        "https://example.org/nutzungsbedingungen/PDM",
        "https://example.org/terms/NONE",
        "https://example.org/CC_BY_SA_40",
        "https://evil.example/w3id.org/openeduhub/vocabs/licenses/CC_BY_40",
    ],
)
def test_another_link_that_ends_in_a_key_stays_a_custom_licence(link):
    assert _written({CUSTOM: link}) == {CUSTOM: [link], KEY: ["CUSTOM"]}


# -------------------------------------- key and version go out together

METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    KEY: "CC_BY_SA",
    VERSION: "3.0",
}


class _BulkRefused(FakeRepository):
    """Refuses the write of all fields at once and any write naming `refused`."""

    refused: str = ""

    async def answer(self, method, url, body):
        if "METADATA_UPDATE" in url and (len(body) > 2 or type(self).refused in body):
            return Response(400, {"message": "nein"})
        return await super().answer(method, url, body)


async def _upload(monkeypatch, refused):
    _BulkRefused.refused = refused
    calls = install(monkeypatch, _BulkRefused)
    result = await RepositoryService("user", "password").upload_metadata(
        metadata=dict(METADATA),
        check_duplicates=False,
        context="default",
        version="2.0.0",
        write_extended_data=False,
    )
    return result, [c["json"] for c in requests(calls, "POST", "METADATA_UPDATE")]


@pytest.mark.parametrize("refused", [KEY, VERSION])
async def test_a_refused_half_of_the_licence_takes_the_other_half_with_it(
    monkeypatch, refused
):
    result, writes = await _upload(monkeypatch, refused)

    assert {KEY: ["CC_BY_SA"], VERSION: ["3.0"]} in writes, "one request for both"
    assert not [w for w in writes if set(w) in ({KEY}, {VERSION})], "never one alone"
    assert result["success"] is True
    assert {e["field_id"] for e in result["field_errors"]} == {KEY, VERSION}
    assert len(result["field_errors"]) == 2
    assert all("Lizenz" in e["error"] for e in result["field_errors"])


async def test_an_accepted_licence_is_written_in_one_request(monkeypatch):
    result, writes = await _upload(monkeypatch, refused="cclom:nichts")

    assert {KEY: ["CC_BY_SA"], VERSION: ["3.0"]} in writes
    assert result["fields_written"] == 3
    assert "field_errors" not in result
