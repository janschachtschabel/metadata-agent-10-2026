"""
Context, version and schema of a request - resolved in one place.

Every endpoint that names a schema reads the three the same way: 'latest' (or
nothing) is the context's default version, one leading 'v' is allowed, a schema
is a file name or the URI of a content type, and what does not exist is a 404
that says which of them. Five routers resolved 'latest' inline and had drifted
apart: /validate answered an unknown context with 200, /detect-content-type
named a version no context has (audit 14.11., S-37, S-38, Q-08).
"""

from typing import Optional

from fastapi import HTTPException

from ..utils.schema_loader import (
    load_schema,
    resolve_schema_file_or_uri,
    resolve_version,
)


def concrete_version(context: str, version: Optional[str]) -> str:
    """
    The version a request means - an option left empty is 'latest'; 404 for an
    unknown context or what is no version.
    """
    try:
        return resolve_version(context, version or "latest")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def resolve_schema(
    context: str,
    version: Optional[str],
    schema_file: Optional[str],
    when_auto: Optional[str] = None,
) -> tuple[str, str]:
    """
    (concrete version, schema file) of a request, or its refusal.

    400 for a schema URI no content type has; 404 for a context, version or
    schema file that does not exist. For 'auto' (or nothing) the schema file is
    `when_auto` - what the endpoint falls back to - or stays 'auto' where the
    endpoint detects it later; the context's core.json then shows that context
    and version exist.
    """
    version = concrete_version(context, version)
    if not schema_file or schema_file == "auto":
        schema_file = when_auto or "auto"
    else:
        try:
            schema_file = resolve_schema_file_or_uri(schema_file, context, version)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        load_schema(
            context, version, "core.json" if schema_file == "auto" else schema_file
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return version, schema_file
