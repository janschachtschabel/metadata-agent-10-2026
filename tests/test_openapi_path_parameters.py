"""
Every path parameter of the public API says what it is and gives an example.

Developer guide for services, chapter 1: each parameter explained. Audit
13.11., API-15: none of the path parameters (context, version, schema_file,
node_id, lang) had a description; generated clients and the B-API's API
documentation showed bare names.
"""

from fastapi.testclient import TestClient

from src import main


def test_every_path_parameter_is_described_with_an_example():
    with TestClient(main.app) as client:
        spec = client.get("/openapi.json").json()

    undocumented = []
    seen = 0
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                if parameter["in"] != "path":
                    continue
                seen += 1
                schema = parameter.get("schema", {})
                has_example = (
                    parameter.get("example") is not None
                    or parameter.get("examples")
                    or schema.get("examples")
                )
                if not parameter.get("description") or not has_example:
                    undocumented.append(f"{method.upper()} {path}: {parameter['name']}")

    assert seen >= 8
    assert undocumented == []
