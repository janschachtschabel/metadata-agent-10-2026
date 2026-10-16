"""
Schema names and versions are identifiers, never filesystem paths.

load_schema() used to join the version and the schema file name straight into
a path below src/schemata. Both arrive from requests (JSON body fields, URL path
segments), so '../' read any JSON file the process could open, and the error for
a missing schema returned the absolute server path to the caller.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.utils.schema_loader import SCHEMATA_PATH, load_schema


@pytest.mark.parametrize("version", ["2.0.0", "v2.0.0", "latest", "1.8.1"])
def test_known_versions_and_files_still_load(version):
    assert load_schema("default", version, "core.json")["fields"]


@pytest.mark.parametrize(
    "schema_file",
    [
        "../../../static/widget/assets/i18n/de.json",
        "..\\..\\..\\static\\widget\\assets\\i18n\\de.json",
        "sub/core.json",
        "/etc/passwd.json",
        "C:\\core.json",
        "core.json/",
        "core",
        "",
    ],
)
def test_a_schema_file_that_is_not_a_plain_name_is_refused(schema_file):
    with pytest.raises(ValueError, match="Invalid schema"):
        load_schema("default", "2.0.0", schema_file)


# 'Invalid schema …', not 'Schema not found': most of these paths do not exist,
# so a missing check would still raise — just with the wrong reason.
@pytest.mark.parametrize(
    "version, schema_file",
    [
        # Resolves to src/static/widget/assets/i18n/de.json, which exists.
        ("2.0.0/../../../static/widget/assets/i18n", "de.json"),
        ("2.0.0/../../../static/widget/assets/i18n", "core.json"),
        ("../2.0.0", "core.json"),
        ("2.0.0\\..\\..", "core.json"),
        ("2.0", "core.json"),
        ("", "core.json"),
    ],
)
def test_a_version_that_is_not_x_y_z_is_refused(version, schema_file):
    with pytest.raises(ValueError, match="Invalid schema"):
        load_schema("default", version, schema_file)


def test_a_missing_schema_does_not_reveal_the_server_path():
    with pytest.raises(ValueError) as error:
        load_schema("default", "9.9.9", "core.json")

    assert str(SCHEMATA_PATH) not in str(error.value)


def test_the_schema_endpoint_does_not_reveal_the_server_path():
    with TestClient(main.app) as client:
        response = client.get("/info/schema/default/9.9.9/core.json")

    assert response.status_code == 404
    assert str(SCHEMATA_PATH) not in response.text
    assert "schemata" not in response.text
