"""Schema loader utility for loading and caching schema definitions."""

import logging
import json
import re
from pathlib import Path
from typing import Any, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)


SCHEMATA_PATH = Path(__file__).parent.parent / "schemata"

# What a schema file name and a resolved version may look like (see load_schema).
_SCHEMA_FILE = re.compile(r"[A-Za-z0-9_-]+\.json")
_SCHEMA_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


@lru_cache(maxsize=1)
def load_context_registry() -> dict[str, Any]:
    """Load the context registry."""
    registry_path = SCHEMATA_PATH / "context-registry.json"
    with open(registry_path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=10)
def load_manifest(context: str) -> dict[str, Any]:
    """Load manifest for a context."""
    registry = load_context_registry()
    context_info = registry.get("contexts", {}).get(context)
    if not context_info:
        raise ValueError(f"Unknown context: {context}")

    context_path = context_info.get("path", context)
    manifest_path = SCHEMATA_PATH / context_path / "manifest.json"

    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_version(context: str, version: str) -> str:
    """Resolve 'latest' to actual version number. Strips 'v' prefix if present."""
    if version.lower() == "latest":
        return get_latest_version(context)
    # Strip 'v' prefix — directory path already adds it
    return version.lstrip("v")


def schema_file_of(metadata: dict[str, Any]) -> str:
    """The schema a record names: `metadataset`, legacy `_schema.file`, else learning material."""
    return metadata.get("metadataset") or metadata.get("_schema", {}).get(
        "file", "learning_material.json"
    )


def load_schema(context: str, version: str, schema_file: str) -> dict[str, Any]:
    """Load a specific schema definition."""
    # Version and file name arrive from requests. They are identifiers, never
    # path fragments — '../' would otherwise read any JSON file on the server.
    if not isinstance(schema_file, str) or not _SCHEMA_FILE.fullmatch(schema_file):
        raise ValueError(f"Invalid schema file name: {schema_file!r}")

    # Resolve 'latest' to actual version
    resolved_version = resolve_version(context, version)
    if not _SCHEMA_VERSION.fullmatch(resolved_version):
        raise ValueError(f"Invalid schema version: {version!r}")

    # Cached per resolved version: "latest", "2.0.0" and "v2.0.0" are one file.
    return _load_schema_file(context, resolved_version, schema_file)


@lru_cache(maxsize=100)
def _load_schema_file(
    context: str, resolved_version: str, schema_file: str
) -> dict[str, Any]:
    """The schema file of a validated name and resolved version (load_schema)."""
    registry = load_context_registry()
    context_info = registry.get("contexts", {}).get(context)
    if not context_info:
        raise ValueError(f"Unknown context: {context}")

    context_path = context_info.get("path", context)
    schema_path = SCHEMATA_PATH / context_path / f"v{resolved_version}" / schema_file

    # The message reaches API callers: name the schema, not the server path.
    if not schema_path.resolve().is_relative_to(SCHEMATA_PATH.resolve()):
        raise ValueError(f"Invalid schema path: {schema_file!r}")
    if not schema_path.is_file():
        raise ValueError(
            f"Schema not found: {schema_file} ({context}, version {resolved_version})"
        )

    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_latest_version(context: str = "default") -> str:
    """
    Get the latest (default) version for a context.

    Checks for:
    1. Version marked as isDefault in manifest
    2. defaultVersion in context-registry
    3. Highest semantic version number as fallback
    """
    try:
        registry = load_context_registry()
        context_info = registry.get("contexts", {}).get(context, {})
        manifest = load_manifest(context)
        versions = manifest.get("versions", {})

        # 1. Check for version with isDefault=true
        for version, info in versions.items():
            if info.get("isDefault", False):
                return version

        # 2. Use defaultVersion from context-registry
        if context_info.get("defaultVersion"):
            return context_info["defaultVersion"]

        # 3. Fallback: highest semantic version
        if versions:

            def _parse_version(v: str) -> list[int]:
                try:
                    return [int(x) for x in v.split(".")]
                except ValueError:
                    return [0]

            sorted_versions = sorted(versions.keys(), key=_parse_version, reverse=True)
            return sorted_versions[0]

        return "1.0.0"
    except Exception:
        return "1.8.1"  # Hardcoded fallback


def get_available_contexts() -> list[dict[str, Any]]:
    """Get list of available contexts with their versions."""
    registry = load_context_registry()
    contexts = []

    for context_name, context_info in registry.get("contexts", {}).items():
        manifest = load_manifest(context_name)
        versions = list(manifest.get("versions", {}).keys())
        default_version = get_latest_version(context_name)

        contexts.append(
            {
                "name": context_name,
                "display_name": context_info.get("name", context_name),
                "versions": versions,
                "default_version": default_version,
            }
        )

    return contexts


def get_available_schemas(context: str, version: str) -> list[dict[str, Any]]:
    """Get list of available schemas for a context/version."""
    # Resolve 'latest' to actual version
    resolved_version = resolve_version(context, version)

    manifest = load_manifest(context)
    version_info = manifest.get("versions", {}).get(resolved_version)

    if not version_info:
        raise ValueError(f"Unknown version: {version} (resolved: {resolved_version})")

    schemas = []
    for schema_file in version_info.get("schemas", []):
        try:
            schema = load_schema(context, version, schema_file)
            schemas.append(
                {
                    "file": schema_file,
                    "profile_id": schema.get("profileId", ""),
                    "label": schema.get(
                        "label", {"de": schema_file, "en": schema_file}
                    ),
                    "groups": [g.get("id") for g in schema.get("groups", [])],
                    "field_count": len(schema.get("fields", [])),
                }
            )
        except Exception as e:
            logger.warning(f"⚠️ Schema {schema_file} not listed, not loadable: {e}")
            continue

    return schemas


def _profile_id(context: str, version: str, schema_file: str) -> Optional[str]:
    """The edu-sharing profile of a content type (its schema's profileId), or None."""
    try:
        return load_schema(context, version, schema_file).get("profileId") or None
    except ValueError as e:
        logger.warning(f"⚠️ No profile id for {schema_file}: {e}")
        return None


def get_content_types(context: str, version: str) -> list[dict[str, Any]]:
    """Get content types from core.json for schema detection."""
    try:
        core_schema = load_schema(context, version, "core.json")

        # Find the content type field (ccm:oeh_extendedType or similar)
        for field in core_schema.get("fields", []):
            vocab = field.get("system", {}).get("vocabulary", {})
            concepts = vocab.get("concepts", [])

            content_types = []
            for concept in concepts:
                if concept.get("schema_file"):
                    content_types.append(
                        {
                            "uri": concept.get("uri", ""),
                            "label": concept.get("label", {}),
                            "schema_file": concept.get("schema_file"),
                            "profile_id": _profile_id(
                                context, version, concept["schema_file"]
                            ),
                        }
                    )

            if content_types:
                return content_types

        return []
    except Exception:
        return []


def get_content_type_prompt(
    context: str, version: str, language: str = "de"
) -> Optional[str]:
    """Get the prompt text for the content type field from core.json."""
    try:
        core_schema = load_schema(context, version, "core.json")
        for field in core_schema.get("fields", []):
            vocab = field.get("system", {}).get("vocabulary", {})
            if vocab.get("concepts") and any(
                c.get("schema_file") for c in vocab["concepts"]
            ):
                prompt = field.get("prompt", {})
                return prompt.get(language) or prompt.get("de") or prompt.get("en")
        return None
    except Exception:
        return None


def resolve_schema_file_from_uri(uri: str, context: str, version: str) -> Optional[str]:
    """
    Resolve a vocab URI to the corresponding schema_file name.

    Example: 'http://w3id.org/openeduhub/vocabs/contentTypes/event' -> 'event.json'
    Returns None if no match found.
    """
    content_types = get_content_types(context, version)
    for ct in content_types:
        if ct.get("uri") == uri:
            return ct.get("schema_file")
    return None


def get_content_type_uri(schema_file: str, context: str, version: str) -> Optional[str]:
    """
    Get the vocab URI for a given schema_file.

    Example: 'event.json' -> 'http://w3id.org/openeduhub/vocabs/contentTypes/event'
    Returns None if no URI is defined for this schema_file.
    """
    content_types = get_content_types(context, version)
    for ct in content_types:
        if ct.get("schema_file") == schema_file:
            return ct.get("uri") or None
    return None


def resolve_schema_file_or_uri(
    schema_file_or_uri: str, context: str, version: str
) -> str:
    """
    Accept either a schema_file name (e.g. 'event.json') or a vocab URI
    (e.g. 'http://w3id.org/openeduhub/vocabs/contentTypes/event') and return the schema_file name.

    If the input looks like a URI (starts with 'http'), it is resolved.
    Otherwise it is returned as-is.
    """
    if schema_file_or_uri.startswith("http://") or schema_file_or_uri.startswith(
        "https://"
    ):
        resolved = resolve_schema_file_from_uri(schema_file_or_uri, context, version)
        if resolved:
            return resolved
        raise ValueError(f"No schema_file found for URI: {schema_file_or_uri}")
    return schema_file_or_uri


def detect_schema_from_text(text: str, context: str, version: str) -> str:
    """Detect the most appropriate schema based on text content."""
    text_lower = text.lower()

    # Keywords for schema detection
    schema_keywords = {
        "event.json": [
            "veranstaltung",
            "event",
            "workshop",
            "seminar",
            "konferenz",
            "tagung",
            "webinar",
            "schulung",
            "kurs",
            "fortbildung",
            "datum",
            "uhrzeit",
            "anmeldung",
            "teilnehmer",
        ],
        "person.json": [
            "person",
            "autor",
            "author",
            "referent",
            "dozent",
            "lehrer",
            "professor",
            "experte",
            "speaker",
        ],
        "organization.json": [
            "organisation",
            "organization",
            "firma",
            "company",
            "verein",
            "institution",
            "hochschule",
            "universität",
            "schule",
            "unternehmen",
        ],
        "education_offer.json": [
            "bildungsangebot",
            "kursangebot",
            "lehrgang",
            "ausbildung",
            "studiengang",
            "weiterbildung",
            "zertifikat",
            "abschluss",
        ],
        "learning_material.json": [
            "material",
            "arbeitsblatt",
            "unterrichtsmaterial",
            "lernmaterial",
            "ressource",
            "dokument",
            "präsentation",
            "video",
            "podcast",
            "buch",
            "artikel",
        ],
        "tool_service.json": [
            "tool",
            "software",
            "app",
            "anwendung",
            "dienst",
            "service",
            "plattform",
            "programm",
        ],
    }

    # Count keyword matches
    scores = {}
    for schema_file, keywords in schema_keywords.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[schema_file] = score

    # Return schema with highest score, or default to learning_material
    if scores:
        return max(scores, key=lambda name: scores[name])

    return "learning_material.json"


def get_schema_fields(
    context: str, version: str, schema_file: str
) -> list[dict[str, Any]]:
    """Get all fields from a schema with their configurations."""
    schema = load_schema(context, version, schema_file)
    return schema.get("fields", [])


def get_ai_fillable_fields(
    context: str, version: str, schema_file: str
) -> list[dict[str, Any]]:
    """Get only AI-fillable fields from a schema."""
    fields = get_schema_fields(context, version, schema_file)
    return [f for f in fields if f.get("system", {}).get("ai_fillable", True)]


def get_repo_fields(
    context: str, version: str, schema_file: Optional[str] = None
) -> set[str]:
    """
    Get all field IDs that have repo_field=true from core.json + optional special schema.

    Args:
        context: Schema context (e.g. 'default')
        version: Schema version (e.g. '1.8.1' or 'latest')
        schema_file: Optional special schema (e.g. 'event.json')

    Returns:
        Set of field IDs (system.path) that should be written to the repository.
    """
    repo_field_ids: set[str] = set()

    # Always load core.json
    try:
        core_fields = get_schema_fields(context, version, "core.json")
        for field in core_fields:
            system = field.get("system", {})
            if system.get("repo_field", False):
                field_id = system.get("path") or field.get("id")
                if field_id:
                    repo_field_ids.add(field_id)
    except Exception as e:
        # Fewer (or no) writable fields follow; the log has to say why (L-04).
        logger.warning(f"⚠️ Repo fields of core.json ({context}/{version}): {e}")

    # Load special schema if provided (and not core.json itself)
    if schema_file and schema_file != "core.json":
        try:
            special_fields = get_schema_fields(context, version, schema_file)
            for field in special_fields:
                system = field.get("system", {})
                if system.get("repo_field", False):
                    field_id = system.get("path") or field.get("id")
                    if field_id:
                        repo_field_ids.add(field_id)
        except Exception as e:
            logger.warning(f"⚠️ Repo fields of {schema_file} ({context}/{version}): {e}")

    return repo_field_ids
