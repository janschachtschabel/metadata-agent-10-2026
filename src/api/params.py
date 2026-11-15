"""
The path parameters of the API, each with its description and an example.

One place for them: the same parameter means the same in every route, and the
OpenAPI spec explains it (developer guide for services, chapter 1). The names
have the lengths of the same options in a body (audit 14.11., S-34).
"""

from typing import Annotated

from fastapi import Path

Context = Annotated[
    str,
    Path(
        max_length=64,
        description="Schema-Kontext, z. B. `default` oder `mds_oeh`. Die Liste "
        "liefert `GET /info/schemata`.",
        examples=["default"],
    ),
]
Version = Annotated[
    str,
    Path(
        max_length=32,
        description="Schema-Version des Kontexts, z. B. `2.0.0`, oder `latest` "
        "für die neueste.",
        examples=["latest"],
    ),
]
SchemaFile = Annotated[
    str,
    Path(
        max_length=300,
        description="Schemadatei eines Inhaltstyps, z. B. `event.json`, oder "
        "`core.json` für die Basisfelder.",
        examples=["event.json"],
    ),
]
NodeId = Annotated[
    str,
    Path(
        description="UUID des Knotens im edu-sharing-Repository.",
        examples=["3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"],
    ),
]
Language = Annotated[
    str,
    Path(
        max_length=8,
        description="Sprache der Oberflächentexte: `de` oder `en`.",
        examples=["de"],
    ),
]
