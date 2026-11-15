"""Models of /info/schemata: the contexts, versions and schema files on offer."""

from pydantic import BaseModel

from .common import LocalizedString


class SchemaInfo(BaseModel):
    """Information about a schema."""

    file: str
    profile_id: str
    label: LocalizedString
    groups: list[str]
    field_count: int


class ContextInfo(BaseModel):
    """Information about a context."""

    name: str
    display_name: str
    versions: list[str]
    default_version: str


class SchemataInfoResponse(BaseModel):
    """Response with available schemata information."""

    contexts: list[ContextInfo]
    default_context: str
