"""Geocoding service using Photon API (Komoot) to convert addresses to coordinates."""

import asyncio
import time
from typing import Any, Optional
import httpx
import logging

from ..metrics import THIRD_PARTY_HOOKS

logger = logging.getLogger(__name__)

# Lookups per request. Photon allows one per second and the schedule is shared
# by the whole process, so every place costs every other request a second.
MAX_GEOCODED_LOCATIONS = 10

# Of the /generate deadline, this much is left over for output normalisation
# and the answer: geocoding stops this long before it.
GEOCODING_RESERVE_SECONDS = 1.0


def _has_coordinates(location: Any) -> bool:
    """A location that needs no lookup, and so uses none of the limit."""
    return isinstance(location, dict) and bool(
        (location.get("geo") or {}).get("latitude")
    )


class GeocodingService:
    """
    Geocoding service using Photon API from Komoot.
    Converts addresses to geo-coordinates (latitude/longitude).
    """

    # Photon API endpoint (free, no API key required)
    PHOTON_API_URL = "https://photon.komoot.io/api"

    # Rate limiting: 1 request per second
    RATE_LIMIT_MS = 1000

    def __init__(self):
        # Next free request slot (monotonic ms), shared by concurrent lookups.
        self._next_slot = 0.0

    async def geocode_address(
        self, address: str, language: str = "de"
    ) -> Optional[dict]:
        """
        Geocode an address string using Photon API.

        Args:
            address: Address string (e.g., "Erfurter Straße 1, 99423 Weimar")
            language: Language for results (de, en, fr, it)

        Returns:
            Dict with latitude, longitude, and enriched address data, or None
        """
        if not address or not address.strip():
            return None

        try:
            # Rate limiting
            await self._wait_for_rate_limit()

            # Build API URL
            params: dict[str, str | int] = {"q": address, "lang": language, "limit": 1}

            headers = {"User-Agent": "MetadataAgentAPI/1.0 (geocoding service)"}
            # Counted, but no tracing headers: Photon is a third party.
            async with httpx.AsyncClient(
                timeout=10.0, event_hooks=THIRD_PARTY_HOOKS
            ) as client:
                response = await client.get(
                    self.PHOTON_API_URL, params=params, headers=headers
                )

                if response.status_code != 200:
                    logger.error(f"❌ Photon API error: {response.status_code}")
                    return None

                data = response.json()

            # Check if we got results
            if not data.get("features") or len(data["features"]) == 0:
                logger.warning(f"⚠️ No geocoding results for: {address}")
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

            logger.info(
                f"✅ Geocoded '{address}' → {result['latitude']}, {result['longitude']}"
            )
            return result

        except Exception as e:
            logger.error(f"❌ Geocoding error: {e}")
            return None

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
        enriched_locations, skipped, out_of_time = await self._enrich_locations(
            location_field, language, budget
        )
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
    ) -> tuple[list, int, int]:
        """
        (locations, skipped by the lookup limit, left out by the budget).

        Past the lookup limit or the budget (loop time) a location passes
        through unchanged; a lookup still running at the budget is cut.
        """
        enriched_locations: list = []
        lookups = skipped = out_of_time = 0
        for i, location in enumerate(locations):
            if not _has_coordinates(location):
                if lookups >= MAX_GEOCODED_LOCATIONS:
                    skipped += 1
                    enriched_locations.append(location)
                    continue
                if budget is not None and not self._slot_before(budget):
                    out_of_time += 1
                    enriched_locations.append(location)
                    continue
                lookups += 1
            logger.info(f"📍 Processing location {i + 1}/{len(locations)}: {location}")
            try:
                async with asyncio.timeout_at(budget):
                    enriched = await self._enrich_single_location(location, language)
            except TimeoutError:
                out_of_time += 1
                enriched = location
            enriched_locations.append(enriched)
        return enriched_locations, skipped, out_of_time

    def _slot_before(self, budget: float) -> bool:
        """
        Whether the next free Photon slot comes before `budget` (loop time).

        Checked without reserving: a slot after the budget would only make
        every other request wait for a lookup that is never made.
        """
        wait = max(0.0, self._next_slot - time.monotonic() * 1000) / 1000
        return asyncio.get_running_loop().time() + wait < budget

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
        # If already a structured object
        if isinstance(location, dict):
            # Check if already has geo coordinates
            if location.get("geo") and location["geo"].get("latitude"):
                logger.info(f"  ✅ Already has coordinates: {location['geo']}")
                return location

            # Build address string for geocoding
            address_obj = location.get("address", {})
            address_str = self._build_address_string(address_obj) if address_obj else ""

            # Fallback: If address is empty/incomplete, try using the name field
            if not address_str and location.get("name"):
                name = location.get("name", "")
                logger.info(f"  ℹ️ Address empty, using name for geocoding: {name}")
                address_str = name

            if address_str:
                geo_result = await self.geocode_address(address_str, language)
                if geo_result:
                    location["geo"] = {
                        "latitude": geo_result["latitude"],
                        "longitude": geo_result["longitude"],
                    }
                    # Enrich address if empty or incomplete
                    if not address_obj or not self._build_address_string(address_obj):
                        location["address"] = self._build_address_object(geo_result)
                        logger.info("  ✅ Enriched address from geocoding result")

            return location

        # If string, convert to structured object
        if isinstance(location, str):
            address_str = location.strip()

            # Try to parse name from address (format: "Name, Address")
            name = None
            if "," in address_str:
                parts = address_str.split(",", 1)
                # If first part doesn't look like a street address, use as name
                first_part = parts[0].strip()
                if (
                    not any(char.isdigit() for char in first_part)
                    and len(first_part) < 50
                ):
                    name = first_part
                    address_str = parts[1].strip() if len(parts) > 1 else address_str

            # Geocode the address
            geo_result = await self.geocode_address(location, language)

            if geo_result:
                enriched = geo_result.get("enriched_address", {})

                result = {
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

                logger.info(
                    f"  ✅ Geocoded to: {result['geo']['latitude']}, {result['geo']['longitude']}"
                )
                return result
            else:
                # Return basic structure even without geocoding
                return {"name": location, "address": {}, "geo": {}}

        # Unknown format, return as-is wrapped
        return {"name": str(location), "address": {}, "geo": {}}

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
        slot = max(now, self._next_slot)
        self._next_slot = slot + self.RATE_LIMIT_MS
        await asyncio.sleep((slot - now) / 1000)


# Singleton instance
_geocoding_service: Optional[GeocodingService] = None


def get_geocoding_service() -> GeocodingService:
    """Get or create geocoding service singleton."""
    global _geocoding_service
    if _geocoding_service is None:
        _geocoding_service = GeocodingService()
    return _geocoding_service
