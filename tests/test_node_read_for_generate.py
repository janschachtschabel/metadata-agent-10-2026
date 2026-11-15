"""
Reading a node as the source of /generate (audit 14.11., R-06, R-11, R-12, R-N).

- R-06: the node's repository-internal properties (workflow, VCARDs, the
  extended data of the last upload) became part of the record, and the next
  upload wrote them into ccm:oeh_extendedData again: it grew with every round.
  Only what a schema of the context declares is read, and the extended data
  never holds ccm:oeh_extended* keys.
- R-11: a 403/404 of the textContent call means "no stored text", not "node
  not found": the fallback to title and description runs.
- R-12: a repository URL without /rest reads nodes as it writes them.
- R-N: no more of a textContent answer is read than the text limit needs.
"""

import json

import httpx
import pytest

from src.api import input_resolver
from src.services import input_source_service as source_module
from src.services import metadata_generation as generation
from src.services.input_source_service import InputSourceService
from src.services.repository_metadata import _extended_fields

NODE_ID = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
CTX, VER, SCHEMA = "default", "2.0.0", "learning_material.json"
TYPE_URI = "http://w3id.org/openeduhub/vocabs/contentTypes/learning_material"

# A node as the repository returns it after an upload by this service.
PROPERTIES = {
    "cclom:title": ["Bruchrechnung"],
    "cclom:general_description": ["Arbeitsblatt"],
    "ccm:wwwurl": ["https://example.org/b"],
    "ccm:taxonid": ["http://w3id.org/openeduhub/vocabs/discipline/380"],
    "ccm:taxonid_DISPLAYNAME": ["Mathematik"],
    "cm:author": ["Erika Mustermann"],
    "ccm:wf_status": ["200_tocheck"],
    "ccm:wf_protocol": ['{"editor":"service","status":"200_tocheck"}'],
    "ccm:ph_users": ["service-account"],
    "ccm:ph_history": ['{"user":"service-account","action":"ADD"}'],
    "ccm:lifecyclecontributer_author": ["BEGIN:VCARD\nFN:Erika Mustermann\nEND:VCARD"],
    "ccm:lifecyclecontributer_authorFN": ["Erika Mustermann"],
    "ccm:lifecyclecontributer_authorVCARD_GIVENNAME": ["Erika"],
    "ccm:oeh_extendedType": [TYPE_URI],
    "ccm:oeh_extendedText": ["x" * 20000],
    "ccm:oeh_extendedData": [json.dumps({"cclom:title": "Bruchrechnung"})],
    "ccm:linktype": ["USER_GENERATED"],
    "ccm:create_version": ["true"],
    "cm:created": ["1760000000000"],
    "cm:name": ["example.org_b"],
    "sys:node-uuid": [NODE_ID],
    "virtual:permalink": ["https://repo/x"],
}


def _service(handler) -> InputSourceService:
    service = InputSourceService()
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return service


def _node(text_response):
    """A repository that knows the node; `text_response` answers /textContent."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path.endswith("/textContent"):
            return text_response(request)
        return httpx.Response(200, json={"node": {"properties": PROPERTIES}})

    service = _service(handler)
    service.requested = requested
    return service


# ------------------------------------------------------------------- R-06


def test_only_what_the_schemata_declare_becomes_metadata():
    record = InputSourceService()._convert_node_properties_to_metadata(PROPERTIES)

    assert record == {
        "cclom:title": "Bruchrechnung",
        "cclom:general_description": "Arbeitsblatt",
        "ccm:wwwurl": "https://example.org/b",
        "ccm:taxonid": "http://w3id.org/openeduhub/vocabs/discipline/380",
        "cm:author": "Erika Mustermann",
    }


def test_a_property_stored_under_its_repository_name_is_read_under_the_schemas():
    # Schema 1.8.1 calls the field oeh:new_lrt; the node holds ccm:oeh_lrt.
    lrt = "http://w3id.org/openeduhub/vocabs/new_lrt/36e68792"
    convert = InputSourceService()._convert_node_properties_to_metadata

    assert convert({"ccm:oeh_lrt": [lrt]}, "default", "1.8.1") == {"oeh:new_lrt": lrt}
    assert convert({"ccm:oeh_lrt": [lrt]}, "default", "2.0.0") == {"ccm:oeh_lrt": lrt}


def test_the_extended_data_never_holds_extended_fields():
    body = {
        "metadataset": SCHEMA,
        "cclom:title": "Bruchrechnung",
        "ccm:oeh_extendedType": TYPE_URI,
        "ccm:oeh_extendedData": "{}",
        "ccm:oeh_extendedText": "Rohtext",
    }

    data = json.loads(_extended_fields(body, CTX, VER, None)["ccm:oeh_extendedData"][0])

    assert data == {"cclom:title": "Bruchrechnung"}


def test_reading_generating_and_uploading_again_keeps_the_extended_data_stable():
    # The auditor's round trip: node -> /generate record -> extended data.
    service = InputSourceService()
    properties = dict(PROPERTIES)
    sizes = []
    for _ in range(4):
        existing = service._convert_node_properties_to_metadata(properties)
        record, _ = generation.merge_extracted(
            existing, {"cclom:title": "Bruchrechnung"}, None
        )
        record = generation.order_by_schema(record, CTX, VER, SCHEMA, True)
        body = {"contextName": CTX, "schemaVersion": VER, "metadataset": SCHEMA}
        data = _extended_fields({**body, **record}, CTX, VER, None)[
            "ccm:oeh_extendedData"
        ][0]
        sizes.append(len(data))
        properties["ccm:oeh_extendedData"] = [data]

    assert len(set(sizes)) == 1, sizes
    assert not [key for key in json.loads(data) if key.startswith("ccm:wf_")]
    assert "ccm:lifecyclecontributer_author" not in json.loads(data)


# ------------------------------------------------------------------- R-11


@pytest.mark.parametrize("status", [403, 404])
async def test_a_refused_text_content_means_no_stored_text(status):
    service = _node(lambda request: httpx.Response(status, json={"message": "x"}))

    data = await service.fetch_from_node_id(NODE_ID)

    assert data.text == "Bruchrechnung\n\nArbeitsblatt"


@pytest.mark.parametrize("status", [401, 500])
async def test_another_failure_of_the_text_content_is_still_an_error(status):
    service = _node(lambda request: httpx.Response(status, json={"message": "x"}))

    with pytest.raises(httpx.HTTPStatusError):
        await service.fetch_from_node_id(NODE_ID)


@pytest.mark.parametrize("status", [403, 404])
async def test_a_refused_metadata_read_is_still_the_missing_node(status):
    service = _service(lambda request: httpx.Response(status, json={"message": "x"}))

    with pytest.raises(httpx.HTTPStatusError) as refused:
        await service.fetch_from_node_id(NODE_ID)

    assert refused.value.response.status_code == status


# ------------------------------------------------------------------- R-12


@pytest.mark.parametrize(
    "configured",
    [
        "https://repo.example/edu-sharing",
        "https://repo.example/edu-sharing/",
        "https://repo.example/edu-sharing/rest",
        "https://repo.example/edu-sharing/rest/",
    ],
)
async def test_a_repository_url_with_or_without_rest_reads_the_node(
    monkeypatch, configured
):
    service = _node(lambda request: httpx.Response(200, json={"text": "Volltext"}))
    monkeypatch.setattr(service.settings, "repository_url", configured)

    await service.fetch_from_node_id(NODE_ID)

    base = "https://repo.example/edu-sharing/rest/node/v1/nodes/-home-"
    assert service.requested == [
        f"{base}/{NODE_ID}/metadata?propertyFilter=-all-",
        f"{base}/{NODE_ID}/textContent",
    ]


# ------------------------------------------------- R-N: bounded text read


def test_the_text_limit_is_the_one_the_api_cuts_source_texts_to():
    assert source_module.MAX_NODE_TEXT_LENGTH == input_resolver.MAX_TEXT_LENGTH


class _Counted(httpx.AsyncByteStream):
    """A body of `total` bytes of JSON text, counting what is read of it."""

    def __init__(self, total: int):
        self.total = total
        self.read = 0

    async def __aiter__(self):
        yield b'{"html": null, "text": "'
        while self.read < self.total:
            self.read += 65536
            yield b"a" * 65536
        yield b'", "raw": "r"}'


async def test_a_huge_stored_text_is_cut_while_it_is_read(monkeypatch):
    monkeypatch.setattr(source_module, "MAX_NODE_TEXT_LENGTH", 100_000)
    monkeypatch.setattr(source_module, "MAX_NODE_TEXT_BYTES", 600_000)
    body = _Counted(50_000_000)
    service = _node(lambda request: httpx.Response(200, stream=body))

    data = await service.fetch_from_node_id(NODE_ID)

    assert data.text == "a" * 100_000
    assert body.read < 1_000_000, "the rest of the answer is not read"
    assert any("gekürzt" in warning for warning in data.warnings)


async def test_a_text_within_the_limit_is_read_whole_and_without_warning():
    service = _node(lambda request: httpx.Response(200, json={"text": "Volltext"}))

    data = await service.fetch_from_node_id(NODE_ID)

    assert data.text == "Volltext"
    assert data.warnings == []


@pytest.mark.parametrize(
    "prefix, expected",
    [
        ('{"text": "Zeile 1\\nZeile \\u00e4', "Zeile 1\nZeile ä"),
        ('{"text": "abc\\', "abc"),  # cut inside an escape
        ('{"text": "abc\\u00', "abc"),
        ('{"text": "ab\\"c", "html": "<p>', 'ab"c'),
        ('{"text": null, "html": "<p>Absatz', "<p>Absatz"),
        ('{"raw": "roh", "html": "<p>', "<p>"),  # text, then html, then raw
        ('{"other": 1', None),
    ],
)
def test_the_text_of_a_cut_off_answer(prefix, expected):
    assert source_module.text_of_cut_json(prefix) == expected
