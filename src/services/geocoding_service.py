"""
Geocoding service using Photon API (Komoot) to convert addresses to coordinates.

A deployment with a B-API calls Photon through the B-API's proxy path, with the
same key as the LLM calls (Settings.geocoder_api_url); otherwise Photon itself.
"""

import asyncio
import heapq
import time
from contextlib import AsyncExitStack
from contextvars import ContextVar
from typing import Any, Optional
import httpx
import logging

from ..config import get_settings
from ..metrics import OWN_SERVICE_HOOKS, THIRD_PARTY_HOOKS
from ..utils.tls_context import shared_tls_context
from .llm_auth import b_api_headers_for
from .llm_errors import LLMCredentialsMissing

logger = logging.getLogger(__name__)

# Lookups per request. Photon allows one per second and the schedule is shared
# by the whole process, so every place costs every other request a second.
MAX_GEOCODED_LOCATIONS = 10

# Of the /generate deadline, this much is left over for output normalisation
# and the answer: geocoding stops this long before it.
GEOCODING_RESERVE_SECONDS = 1.0


class GeocoderRefused(Exception):
    """
    The B-API does not let this key use the geocoder - or there is no key.

    True for every place of the request alike: asking again per place only
    costs time. The message is one for the caller (processing.warnings).
    """


# The languages Photon answers in; any other is a 400 there, and the place
# stayed without coordinates. Left out, Photon uses the language of the place.
PHOTON_LANGUAGES = ("de", "en", "fr")


def photon_language(language: Any) -> Optional[str]:
    """ "de" for "de", "DE" or "de-AT"; None for a language Photon does not know."""
    code = str(language or "")[:8].lower().split("-")[0].split("_")[0]
    return code if code in PHOTON_LANGUAGES else None


def _has_coordinates(location: Any) -> bool:
    """
    A location that needs no lookup, and so uses none of the limit.

    Latitude 0 is a coordinate (the equator); a `geo` that is no object - a
    model or a caller wrote text there - holds none (audit 14.11., L-40).
    """
    geo = location.get("geo") if isinstance(location, dict) else None
    return isinstance(geo, dict) and geo.get("latitude") not in (None, "")


class _Lookups:
    """
    The HTTP client for the lookups of one request, opened at the first one.

    Each lookup built its own client: 420 ms of TLS setup, synchronously, up
    to ten times per request (audit 14.11., L-41).
    """

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._client: Optional[httpx.AsyncClient] = None

    async def client(self, via_b_api: bool) -> httpx.AsyncClient:
        if self._client is None:
            # Counted. Tracing headers only on the way through the B-API:
            # Photon itself is a third party.
            self._client = await self._stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=10.0,
                    verify=shared_tls_context(),
                    event_hooks=OWN_SERVICE_HOOKS if via_b_api else THIRD_PARTY_HOOKS,
                )
            )
        return self._client

    async def aclose(self) -> None:
        await self._stack.aclose()


# Set while the locations of one request are enriched; geocode_address finds
# the request's client here. A context variable, so the signature callers and
# tests know stays as it is, and concurrent requests each see their own.
_SHARED_LOOKUPS: ContextVar[Optional[_Lookups]] = ContextVar(
    "geocoder_lookups", default=None
)


class GeocodingService:
    """
    Geocoding service using Photon API from Komoot.
    Converts addresses to geo-coordinates (latitude/longitude).
    """

    # Rate limiting: 1 request per second
    RATE_LIMIT_MS = 1000

    def __init__(self):
        # Next free request slot (monotonic ms), shared by concurrent lookups.
        self._next_slot = 0.0
        # Slots of lookups cancelled while they waited, earliest first.
        self._returned_slots: list[float] = []

    async def geocode_address(
        self, address: str, language: str = "de"
    ) -> Optional[dict]:
        """
        Geocode an address string using Photon API.

        Args:
            address: Address string (e.g., "Erfurter Straße 1, 99423 Weimar")
            language: Language for results (PHOTON_LANGUAGES; any other is
                not sent)

        Returns:
            Dict with latitude, longitude, and enriched address data, or None
        """
        if not address or not address.strip():
            return None

        settings = get_settings()
        api_url = settings.geocoder_api_url
        try:
            # Before the rate-limit slot: a lookup that cannot be made must not
            # make every other request wait for it.
            auth = b_api_headers_for(api_url, settings)
        except LLMCredentialsMissing as e:
            raise GeocoderRefused(
                "Für den Geocoder über die B-API (Proxy-Dienst photon_geocoding) "
                "gibt es keinen Schlüssel."
            ) from e

        # The client of the request (enrich_metadata_with_geocoding), else one
        # for this lookup alone.
        shared = _SHARED_LOOKUPS.get()
        session = shared or _Lookups()
        try:
            # Rate limiting
            await self._wait_for_rate_limit()

            # Build API URL
            params: dict[str, str | int] = {"q": address, "limit": 1}
            lang = photon_language(language)
            if lang:
                params["lang"] = lang
            headers = {"User-Agent": "MetadataAgentAPI/1.0 (geocoding service)"}
            client = await session.client(bool(auth))
            response = await client.get(
                api_url, params=params, headers={**headers, **auth}
            )

            if response.status_code != 200:
                if auth and response.status_code in (401, 403, 429):
                    raise GeocoderRefused(
                        "Die B-API hat den Aufruf des Geocoders abgelehnt "
                        f"(HTTP {response.status_code}, Proxy-Dienst "
                        "photon_geocoding)."
                    )
                via = "B-API (photon_geocoding)" if auth else "Photon"
                logger.error(f"❌ Geocoder {via}: HTTP {response.status_code}")
                return None

            data = response.json()

            # Check if we got results
            if not data.get("features") or len(data["features"]) == 0:
                # The place itself only at DEBUG: an address can be personal.
                logger.warning("⚠️ No geocoding result for a place")
                logger.debug(f"No geocoding result for: {address}")
                return None

            feature = data["features"][0]
            coords = feature.get("geometry", {}).get("coordinates", [])
            props = feature.get("properties", {})

            if len(coords) < 2:
                logger.error("❌ Invalid coordinates in Photon response")
                return None

            # Photon returns [longitude, latitude]
            result = {
                "latitude": round(coords[1], 7),
                "longitude": round(coords[0], 7),
                "enriched_address": {
                    "street": props.get("street"),
                    "housenumber": props.get("housenumber"),
                    "postal_code": props.get("postcode"),
                    "city": props.get("city"),
                    "state": props.get("state"),
                    "country": props.get("country"),
                    "country_code": props.get("countrycode"),
                    "district": props.get("district"),
                },
                "osm_data": {
                    "osm_type": props.get("osm_type"),
                    "osm_id": props.get("osm_id"),
                    "osm_key": props.get("osm_key"),
                    "osm_value": props.get("osm_value"),
                    "type": props.get("type"),
                },
            }

            logger.debug(
                f"✅ Geocoded '{address}' → {result['latitude']}, {result['longitude']}"
            )
            return result

        except GeocoderRefused:
            raise
        except Exception as e:
            logger.error(f"❌ Geocoding error: {e}")
            return None
        finally:
            if session is not shared:
                await session.aclose()

    async def enrich_metadata_with_geocoding(
        self,
        metadata: dict[str, Any],
        language: str = "de",
        deadline: Optional[float] = None,
        warnings: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Enrich metadata with geocoding results.

        Converts schema:location entries to structured objects with geo coordinates.
        Each location gets its own geo object with latitude/longitude.

        Input formats supported:
        - String array: ["Erfurter Str. 1, 99423 Weimar"]
        - Object array: [{"name": "...", "address": {...}}]

        Output format (per location):
        {
            "name": "...",
            "address": {"streetAddress": "...", "postalCode": "...", ...},
            "geo": {"latitude": 50.97, "longitude": 11.33}
        }

        Args:
            metadata: Generated metadata dict
            language: Language for geocoding
            deadline: Loop time (loop.time()) by which the /generate run must
                end. Geocoding stops GEOCODING_RESERVE_SECONDS before it: no
                lookup whose slot comes later, none still running then. The
                locations concerned stay as they are. None: no limit.
            warnings: Receives a warning when the deadline left locations
                without coordinates.

        Returns:
            Metadata with enriched location objects including geo coordinates
        """
        location_field = metadata.get("schema:location")

        if not location_field:
            logger.info("ℹ️ No schema:location field found, skipping geocoding")
            return metadata

        logger.info(
            f"🌍 Found schema:location with {len(location_field) if isinstance(location_field, list) else 1} location(s)"
        )

        # Convert to list if single value
        if isinstance(location_field, str):
            location_field = [location_field]

        if not isinstance(location_field, list):
            return metadata

        budget = None if deadline is None else deadline - GEOCODING_RESERVE_SECONDS
        (
            enriched_locations,
            skipped,
            out_of_time,
            refused,
            failed,
        ) = await self._enrich_locations(location_field, language, budget)
        if failed:
            message = (
                f"Geokodierung: {len(failed)} Ort(e) konnten nicht verarbeitet "
                f"werden und bleiben unverändert ({', '.join(sorted(set(failed)))})."
            )
            logger.warning(f"⚠️ {message}")
            if warnings is not None:
                warnings.append(message)
        if refused:
            message = f"Orte bleiben ohne Koordinaten: {refused}"
            logger.warning(f"⚠️ {message}")
            if warnings is not None:
                warnings.append(message)
        if skipped:
            logger.warning(
                f"⚠️ Geocoding limited to {MAX_GEOCODED_LOCATIONS} lookups; "
                f"{skipped} locations passed through without coordinates"
            )
        if out_of_time:
            message = (
                f"Geocoding stopped at the time limit: {out_of_time} location(s) "
                "kept without coordinates"
            )
            logger.warning(f"⚠️ {message}")
            if warnings is not None:
                warnings.append(message)

        # Update metadata with enriched locations
        metadata["schema:location"] = enriched_locations

        return metadata

    async def _enrich_locations(
        self, locations: list, language: str, budget: Optional[float]
    ) -> tuple[list, int, int, Optional[str], list[str]]:
        """
        (locations, skipped by the lookup limit, left out by the budget, why
        the geocoder refused - None if it did not -, the kinds of fault that
        left a location as it was).

        Past the lookup limit or the budget (loop time) a location passes
        through unchanged; a lookup still running at the budget is cut. After
        a refusal no further lookup is made. A fault in one location costs
        that location only (audit 14.11., L-40).
        """
        enriched_locations: list = []
        lookups = skipped = out_of_time = 0
        refused: Optional[str] = None
        failed: list[str] = []
        shared = _Lookups()
        token = _SHARED_LOOKUPS.set(shared)
        try:
            for i, location in enumerate(locations):
                if not _has_coordinates(location):
                    if refused:
                        enriched_locations.append(location)
                        continue
                    if lookups >= MAX_GEOCODED_LOCATIONS:
                        skipped += 1
                        enriched_locations.append(location)
                        continue
                    if budget is not None and not self._slot_before(budget):
                        out_of_time += 1
                        enriched_locations.append(location)
                        continue
                    lookups += 1
                logger.info(f"📍 Processing location {i + 1}/{len(locations)}")
                try:
                    async with asyncio.timeout_at(budget):
                        enriched = await self._enrich_single_location(
                            location, language
                        )
                except TimeoutError:
                    out_of_time += 1
                    enriched = location
                except GeocoderRefused as e:
                    refused = str(e)
                    enriched = location
                except Exception as e:
                    # The kind only: the text of a fault may quote the place.
                    failed.append(type(e).__name__)
                    enriched = location
                enriched_locations.append(enriched)
        finally:
            _SHARED_LOOKUPS.reset(token)
            await shared.aclose()
        return enriched_locations, skipped, out_of_time, refused, failed

    def _slot_before(self, budget: float) -> bool:
        """
        Whether the next free Photon slot comes before `budget` (loop time).

        Checked without reserving: a slot after the budget would only make
        every other request wait for a lookup that is never made.
        """
        now = time.monotonic() * 1000
        wait = (self._free_slot(now) - now) / 1000
        return asyncio.get_running_loop().time() + wait < budget

    def _free_slot(self, now: float) -> float:
        """
        The next free slot (monotonic ms), not yet taken: one a cancelled
        lookup returned if it is still to come, else the end of the queue.
        """
        while self._returned_slots and self._returned_slots[0] < now:
            heapq.heappop(self._returned_slots)
        if self._returned_slots:
            return self._returned_slots[0]
        return max(now, self._next_slot)

    async def _enrich_single_location(
        self, location: Any, language: str
    ) -> dict[str, Any]:
        """
        Enrich a single location with geocoding.

        Args:
            location: String or dict location
            language: Geocoding language

        Returns:
            Structured location object with geo coordinates
        """
        if isinstance(location, dict):
            return await self._enrich_location_object(location, language)
        if isinstance(location, str):
            return await self._location_from_text(location, language)

        # Unknown format, return as-is wrapped
        return {"name": str(location), "address": {}, "geo": {}}

    async def _enrich_location_object(
        self, location: dict[str, Any], language: str
    ) -> dict[str, Any]:
        """A structured location: coordinates (and a missing address) are added."""
        if _has_coordinates(location):
            logger.info("  ✅ Already has coordinates")
            return location

        # An address written as text is the address to look up, and stays
        # as the caller wrote it; any other non-object is no address.
        address = location.get("address")
        address_obj = address if isinstance(address, dict) else {}
        if isinstance(address, str):
            address_str = address.strip()
        else:
            address_str = self._build_address_string(address_obj)

        # Fallback: If address is empty/incomplete, try using the name field
        name = location.get("name")
        if not address_str and name and isinstance(name, str):
            logger.info("  ℹ️ Address empty, using the name for geocoding")
            address_str = name

        if address_str:
            geo_result = await self.geocode_address(address_str, language)
            if geo_result:
                location["geo"] = {
                    "latitude": geo_result["latitude"],
                    "longitude": geo_result["longitude"],
                }
                # Enrich address if empty or incomplete
                if not isinstance(address, str) and not (
                    self._build_address_string(address_obj)
                ):
                    location["address"] = self._build_address_object(geo_result)
                    logger.info("  ✅ Enriched address from geocoding result")

        return location

    async def _location_from_text(self, location: str, language: str) -> dict[str, Any]:
        """A location written as text, as a structured object with coordinates."""
        address_str = location.strip()

        # Try to parse name from address (format: "Name, Address")
        name = None
        if "," in address_str:
            parts = address_str.split(",", 1)
            # If first part doesn't look like a street address, use as name
            first_part = parts[0].strip()
            if not any(char.isdigit() for char in first_part) and len(first_part) < 50:
                name = first_part
                address_str = parts[1].strip() if len(parts) > 1 else address_str

        # Geocode the address
        geo_result = await self.geocode_address(location, language)

        if geo_result:
            enriched = geo_result.get("enriched_address", {})

            result: dict[str, Any] = {
                "address": {
                    "streetAddress": enriched.get("street") or "",
                    "postalCode": enriched.get("postal_code") or "",
                    "addressLocality": enriched.get("city") or "",
                    "addressRegion": enriched.get("state") or "",
                    # Photon leaves countrycode out for some places
                    "addressCountry": (enriched.get("country_code") or "").upper()
                    or enriched.get("country")
                    or "",
                },
                "geo": {
                    "latitude": geo_result["latitude"],
                    "longitude": geo_result["longitude"],
                },
            }

            # Add name if extracted or use locality
            if name:
                result["name"] = name

            # Add housenumber to street if available
            if enriched.get("housenumber") and enriched.get("street"):
                result["address"]["streetAddress"] = (
                    f"{enriched['street']} {enriched['housenumber']}"
                )

            logger.info("  ✅ Geocoded")
            return result
        else:
            # Return basic structure even without geocoding
            return {"name": location, "address": {}, "geo": {}}

    def _build_address_object(self, geo_result: dict) -> dict:
        """Build structured address object from geocoding result."""
        enriched = geo_result.get("enriched_address", {})

        street = enriched.get("street") or ""
        if enriched.get("housenumber"):
            street = f"{street} {enriched['housenumber']}".strip()

        return {
            "streetAddress": street,
            "postalCode": enriched.get("postal_code") or "",
            "addressLocality": enriched.get("city") or "",
            "addressRegion": enriched.get("state") or "",
            "addressCountry": (enriched.get("country_code") or "").upper(),
        }

    def _build_address_string(self, address_obj: dict) -> str:
        """Build address string from structured address object."""
        parts = []

        # Try different address field names
        street = address_obj.get("streetAddress") or address_obj.get("street") or ""
        if street:
            parts.append(street)

        postal_code = (
            address_obj.get("postalCode") or address_obj.get("postal_code") or ""
        )
        city = address_obj.get("addressLocality") or address_obj.get("city") or ""

        if postal_code and city:
            parts.append(f"{postal_code} {city}")
        elif city:
            parts.append(city)

        country = address_obj.get("addressCountry") or address_obj.get("country") or ""
        if country:
            parts.append(country)

        return ", ".join(parts)

    async def _wait_for_rate_limit(self) -> None:
        """
        Wait for this call's slot (1 request per second across concurrent calls).

        The slot is taken without an await in between - atomic on the event
        loop - and the sleep happens outside any lock: a cancelled or slow
        waiter no longer holds up the others (audit P-03). Monotonic: a
        wall-clock jump must not open or close the window.
        """
        now = time.monotonic() * 1000
        slot = self._free_slot(now)
        if self._returned_slots:
            heapq.heappop(self._returned_slots)
        else:
            self._next_slot = slot + self.RATE_LIMIT_MS
        try:
            await asyncio.sleep((slot - now) / 1000)
        except asyncio.CancelledError:
            # No lookup was made in this slot: the next one takes it instead
            # of waiting a second behind it (audit 14.11., L-N).
            heapq.heappush(self._returned_slots, slot)
            raise


# Singleton instance
_geocoding_service: Optional[GeocodingService] = None


def get_geocoding_service() -> GeocodingService:
    """Get or create geocoding service singleton."""
    global _geocoding_service
    if _geocoding_service is None:
        _geocoding_service = GeocodingService()
    return _geocoding_service
