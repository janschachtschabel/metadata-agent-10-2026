"""Writing metadata to a node: properties, extended fields and the aspects they need."""

import json
import logging
import time
from typing import Any, Optional

import httpx

from ..utils.schema_loader import get_content_type_uri
from .field_normalizer import find_object_shape_violations
from .repository_licenses import transform_license
from .repository_values import (
    extract_geo_coordinates,
    normalize_for_repo,
    transform_author_to_vcard,
    transform_lrt,
)


from .repository_support import metadata_fields, repository_error

logger = logging.getLogger(__name__)

# After a refused bulk write, single-field writes start only within this many
# seconds (audit D-02). Each may take the client timeout; without a budget a
# schema of 40 fields could hold the request for half an hour.
FIELD_FALLBACK_SECONDS = 30.0


# Mapping: ccm:oeh_extendedType URI → oeh:new_lrt URI
# Sets the learning resource type (LRT) based on the detected content type.
EXTENDED_TYPE_TO_NEW_LRT = {
    "http://w3id.org/openeduhub/vocabs/contentTypes/event": "http://w3id.org/openeduhub/vocabs/new_lrt/955590ae-5f06-4513-98e9-91dfa8d5a05e",
    "http://w3id.org/openeduhub/vocabs/contentTypes/source": "http://w3id.org/openeduhub/vocabs/new_lrt/3869b453-d3c1-4b34-8f25-9127e9d68766",
    "http://w3id.org/openeduhub/vocabs/contentTypes/education_offer": "http://w3id.org/openeduhub/vocabs/new_lrt/03ab835b-c39c-48d1-b5af-7611de2f6464",
    "http://w3id.org/openeduhub/vocabs/contentTypes/tool_service": "http://w3id.org/openeduhub/vocabs/new_lrt/cefccf75-cba3-427d-9a0f-35b4fedcbba1",
    "http://w3id.org/openeduhub/vocabs/contentTypes/didactic_concepts": "http://w3id.org/openeduhub/vocabs/new_lrt/0a79a1d0-583b-47ce-86a7-517ab352d796",
    "http://w3id.org/openeduhub/vocabs/contentTypes/learning_material": "http://w3id.org/openeduhub/vocabs/new_lrt/1846d876-d8fd-476a-b540-b8ffd713fedb",
}


def _extended_fields(
    metadata: dict[str, Any],
    context: str,
    version: str,
    extended_text: Optional[str],
    keep_lrt: bool = False,
) -> dict[str, list[str]]:
    """
    ccm:oeh_extendedType, -Data, -Text and, as a fallback, ccm:oeh_lrt.

    `keep_lrt`: the node already carries an LRT (a partial update of an
    existing node); the fallback is then left out.

    Raises ValueError for a value whose shape contradicts the schema - a
    backstop: upload_metadata and save_node refuse such a value before they
    touch the repository, so reaching this is a bug. Writing it would fix the
    Elasticsearch path to `text` and lock every later record out of the index;
    the raise lands in the caller's except, which discards the half-written node.
    """
    extended_fields: dict[str, list[str]] = {}
    fields = metadata_fields(metadata)

    # 1. ccm:oeh_extendedType — resolve URI from metadataset (schema_file)
    type_uri = None
    schema_file = metadata.get("metadataset")
    if schema_file:
        type_uri = get_content_type_uri(schema_file, context, version)
        if type_uri:
            extended_fields["ccm:oeh_extendedType"] = [type_uri]
            logger.info(f"📎 extendedType: {type_uri} (from {schema_file})")
        else:
            logger.warning(
                f"⚠️ extendedType: No URI found for schema_file={schema_file}"
            )

    # 1b. ccm:oeh_lrt — derived from the content type, but only as a fallback.
    # This write happens after the metadata write, so setting it unconditionally
    # would replace whatever the extraction found. The derivation knows six
    # coarse types ('Material'); the extraction picks from 220 ('Arbeitsblatt').
    # Read from the fields, flat or nested: the widget sends them nested, and
    # the coarse type replaced the precise one there (audit L-03).
    extracted_lrt = fields.get("oeh:new_lrt") or fields.get("ccm:oeh_lrt")
    if extracted_lrt:
        logger.info(f"📎 lrt: kept from extraction, not overwritten ({extracted_lrt})")
    elif keep_lrt:
        logger.info("📎 lrt: kept from the node, not overwritten")
    elif type_uri and type_uri in EXTENDED_TYPE_TO_NEW_LRT:
        lrt_uri = EXTENDED_TYPE_TO_NEW_LRT[type_uri]
        extended_fields["ccm:oeh_lrt"] = [lrt_uri]
        logger.info(f"📎 lrt: {lrt_uri} (from extendedType {type_uri.split('/')[-1]})")

    # 2. ccm:oeh_extendedData — the metadata fields as JSON string
    if schema_file:
        violations = find_object_shape_violations(
            metadata, context, version, schema_file
        )
        if violations:
            raise ValueError(
                "ccm:oeh_extendedData nicht geschrieben — "
                + "; ".join(v["message"] for v in violations)
            )
    if fields:
        data = json.dumps(fields, ensure_ascii=False)
        extended_fields["ccm:oeh_extendedData"] = [data]
        logger.info(f"📎 extendedData: {len(data)} chars")

    # 3. ccm:oeh_extendedText — raw source text
    if extended_text:
        extended_fields["ccm:oeh_extendedText"] = [extended_text]
        logger.info(f"📎 extendedText: {len(extended_text)} chars")
    return extended_fields


class MetadataWriteMixin:
    """
    Metadata write path (part of RepositoryService).

    Needs _auth_header, set by RepositoryService.__init__.
    """

    _auth_header: str

    def _extract_metadata_fields(self, metadata: dict) -> dict:
        """Extract only metadata fields, removing processing/system info.

        Handles two formats:
        - Flat (from /generate API): fields at root level alongside system keys
        - Nested (from web component export): fields inside a 'metadata' sub-dict
        """
        return metadata_fields(metadata)

    async def _set_metadata(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        metadata: dict,
        repo_field_ids: set[str] | None = None,
    ) -> dict:
        """
        Set full metadata on node using dynamically loaded repo fields from schemas.

        Strategy:
        1. Filter metadata to only include fields with repo_field=true in schemas
        2. Try bulk update with all fields
        3. If bulk fails, retry field-by-field to identify problematic fields
        4. Report per-field errors
        """
        metadata_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?versionComment=METADATA_UPDATE&obeyMds=false"

        # Normalize metadata values for repository API
        normalized = normalize_for_repo(metadata, repo_field_ids)

        # Handle license transformation
        transform_license(normalized, metadata)

        # No default license on purpose: the extraction returns null when it
        # finds none, and filling that in would turn "we do not know" into a
        # claim that the material is free of copyright — published to a public
        # repository. An empty field is what hands the decision to the editorial
        # workflow this upload already enters.
        if "ccm:commonlicense_key" not in normalized:
            logger.info("📜 No license detected — left empty for editorial review")

        # Extract geo coordinates from schema:location → cm:latitude / cm:longitude
        extract_geo_coordinates(normalized, metadata)

        # Transform cm:author → ccm:lifecyclecontributer_author (VCARD format)
        transform_author_to_vcard(normalized)

        # Transform oeh:new_lrt → ccm:oeh_lrt (the property the repository keeps)
        transform_lrt(normalized, metadata)

        if not normalized:
            return {
                "success": True,
                "fields_written": 0,
                "fields_skipped": 0,
                "field_errors": [],
            }

        fields_to_write = set(normalized.keys())
        logger.info(f"📝 Writing {len(fields_to_write)} fields to node {node_id}")

        # --- Strategy 1: Bulk update (all fields at once) ---
        response = await client.post(
            metadata_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=normalized,
        )

        if response.status_code in (200, 201):
            logger.info(f"✅ Bulk metadata update succeeded: {len(normalized)} fields")
            return {
                "success": True,
                "fields_written": len(normalized),
                "fields_skipped": 0,
                "field_errors": [],
            }

        # --- Strategy 2: Bulk failed → field-by-field fallback ---
        bulk_error = response.text[:500]
        logger.warning(
            f"⚠️ Bulk metadata update failed ({response.status_code}): {bulk_error}"
        )
        logger.info("🔄 Retrying field-by-field to identify problematic fields...")

        fields_written = 0
        fields_skipped = 0
        field_errors = []
        budget_ends = time.monotonic() + FIELD_FALLBACK_SECONDS

        for field_id, field_value in normalized.items():
            if time.monotonic() >= budget_ends:
                fields_skipped += 1
                field_errors.append(
                    {
                        "field_id": field_id,
                        "error": "Nicht versucht: Zeitbudget für das Schreiben "
                        f"Feld für Feld ({FIELD_FALLBACK_SECONDS:g} s) erschöpft",
                        "status_code": None,
                    }
                )
                continue
            try:
                single_response = await client.post(
                    metadata_url,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={field_id: field_value},
                )

                if single_response.status_code in (200, 201):
                    fields_written += 1
                else:
                    fields_skipped += 1
                    field_errors.append(
                        {
                            "field_id": field_id,
                            "error": repository_error(single_response),
                            "status_code": single_response.status_code,
                        }
                    )
                    logger.error(f"   ❌ {field_id}: {single_response.status_code}")
            except Exception as e:
                fields_skipped += 1
                # The text can name internal hosts: log it, answer with the type.
                field_errors.append(
                    {
                        "field_id": field_id,
                        "error": f"Schreiben fehlgeschlagen ({type(e).__name__})",
                        "status_code": None,
                    }
                )
                logger.error(f"   ❌ {field_id}: {type(e).__name__}: {e}")

        logger.info(
            f"📊 Field-by-field result: {fields_written} written, {fields_skipped} failed"
        )

        return {
            "success": fields_written > 0,
            "fields_written": fields_written,
            "fields_skipped": fields_skipped,
            "field_errors": field_errors,
        }

    async def _write_extended_fields(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        metadata: dict[str, Any],
        context: str,
        version: str,
        extended_text: Optional[str] = None,
        keep_lrt: bool = False,
    ) -> dict[str, Any]:
        """
        Write the 3 extended metadata fields to a node (bypasses repo_field filter).

        Fields:
        - ccm:oeh_extendedType: URI of the content type from core.json vocabulary
        - ccm:oeh_extendedData: Full metadata JSON after generation/extraction
        - ccm:oeh_extendedText: Raw source text before extraction
        """
        metadata_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?versionComment=EXTENDED_DATA&obeyMds=false"
        extended_fields = _extended_fields(
            metadata, context, version, extended_text, keep_lrt
        )
        if not extended_fields:
            logger.warning("⚠️ No extended fields to write")
            return {"success": True, "fields_written": 0}

        try:
            # All in one request; on refusal field by field
            response = await self._post_fields(client, metadata_url, extended_fields)
            if response.status_code in (200, 201):
                logger.info(
                    f"✅ Extended fields written: {list(extended_fields.keys())}"
                )
                return {"success": True, "fields_written": len(extended_fields)}
            logger.warning(
                f"⚠️ Extended fields bulk write failed ({response.status_code}): "
                f"{response.text[:300]}"
            )
            written = 0
            failed = []
            for field_id, field_value in extended_fields.items():
                try:
                    single = await self._post_fields(
                        client, metadata_url, {field_id: field_value}
                    )
                    if single.status_code in (200, 201):
                        written += 1
                        logger.info(f"   ✅ {field_id}")
                    else:
                        failed.append(field_id)
                        logger.error(f"   ❌ {field_id}: {single.status_code}")
                except Exception as e:
                    failed.append(field_id)
                    logger.error(f"   ❌ {field_id}: {e}")
            # Written in part is not written: the answer names what is missing.
            return {
                "success": not failed,
                "fields_written": written,
                "failed_fields": failed,
            }

        except Exception as e:
            logger.error(f"❌ Extended fields write failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "fields_written": 0,
                "error": f"Schreiben fehlgeschlagen ({type(e).__name__})",
            }

    async def _post_fields(
        self, client: httpx.AsyncClient, url: str, fields: dict[str, Any]
    ) -> Any:
        return await client.post(
            url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=fields,
        )

    async def _ensure_aspects(
        self, client: httpx.AsyncClient, base_url: str, node_id: str, metadata: dict
    ) -> Optional[str]:
        """
        Add required aspects to node based on metadata content.

        Aspects are Alfresco extension packages that enable specific property groups.
        Without the correct aspect, the repo silently drops writes to those properties.

        Returns a message for the caller when an aspect could not be set, since
        the fields depending on it are then lost although the write reports them.
        """
        extra_aspects = []

        # cm:geographic → needed for cm:latitude, cm:longitude
        has_geo = False
        locations = metadata.get("schema:location")
        if locations:
            if isinstance(locations, list):
                for loc in locations:
                    if isinstance(loc, dict) and isinstance(loc.get("geo"), dict):
                        has_geo = True
                        break
            elif isinstance(locations, dict) and isinstance(locations.get("geo"), dict):
                has_geo = True
        # Also check schema:geo (organization.json top-level)
        if not has_geo and isinstance(metadata.get("schema:geo"), dict):
            geo = metadata["schema:geo"]
            if geo.get("latitude") is not None and geo.get("longitude") is not None:
                has_geo = True
        if has_geo:
            extra_aspects.append("cm:geographic")

        # cm:author → needed for ccm:lifecyclecontributer_author
        if metadata.get("cm:author"):
            extra_aspects.append("cm:author")

        if not extra_aspects:
            return None

        lost = (
            f"Aspekt(e) {', '.join(extra_aspects)} nicht gesetzt — "
            "Koordinaten bzw. Autor werden vom Repository verworfen"
        )

        # Read current aspects, merge, PUT back
        try:
            aspects_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/aspects"
            r = await client.get(
                f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-",
                headers={
                    "Authorization": self._auth_header,
                    "Accept": "application/json",
                },
            )
            if r.status_code != 200:
                # The PUT replaces the aspect list (hence the merge): sent
                # without the current ones it would drop them from the node.
                logger.warning(f"⚠️ Aspects of {node_id} unreadable: {r.status_code}")
                return f"{lost} (HTTP {r.status_code})"
            current_aspects = r.json().get("node", {}).get("aspects", [])

            new_aspects = [a for a in extra_aspects if a not in current_aspects]
            if new_aspects:
                full_list = current_aspects + new_aspects
                r = await client.put(
                    aspects_url,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=full_list,
                )
                if r.status_code == 200:
                    logger.info(f"🔧 Aspects added: {new_aspects}")
                else:
                    logger.warning(
                        f"⚠️ Failed to add aspects {new_aspects}: {r.status_code}"
                    )
                    return f"{lost} (HTTP {r.status_code})"
        except Exception as e:
            logger.warning(f"⚠️ Aspect update error: {e}")
            return lost
        return None
