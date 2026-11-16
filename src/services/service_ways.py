"""
The ways to the services the B-API proxies (text extraction, geocoder).

With METADATA_AGENT_B_API_SERVICES=auto a call tries them in this order, until
one gives a usable answer:

1. ``forwarded``: through the B-API, with the credential the B-API forwarded
   with the request. Older B-APIs forward the caller's key (``forwardApiKey``),
   current ones a delegation token (``forwardDelegation``) whose scopes need
   not cover the proxied service - on staging (16.11.) the token reached the
   LLM endpoints but got 401 on ``photon_geocoding``.
2. ``own``: through the B-API, with the agent's own ``B_API_KEY``.
3. ``direct``: the service itself, without a key - the way before 4.0.0.

``proxy`` stops after 2, ``direct`` has only 3. B_API_AUTH_MODE decides which
credentials there are (``forward``: no own key, ``static``: no forwarded one).

A way the B-API refused (401, 403) is skipped for REFUSED_FOR_SECONDS and then
tried again: one refusal costs the following requests neither a wasted call nor
a second of the geocoder's schedule, and a right granted later is found without
a restart. The agent's own key is remembered for every request; a forwarded
credential only for itself (by a hash), so that one caller's bad key never
moves the others - or their usage - onto the agent's key (review 16.11.).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal, Optional

from ..config import PHOTON_URL, Settings, environment_text_extraction
from .llm_auth import forwarded_credential

logger = logging.getLogger(__name__)

WayName = Literal["forwarded", "own", "direct"]
Service = Literal["text-extraction", "geocoder"]

# How long a refused way is skipped before it is tried again.
REFUSED_FOR_SECONDS = 600.0
# Refused forwarded credentials kept at most; the oldest go first.
MAX_REMEMBERED = 1024


@dataclass(frozen=True)
class Way:
    """One way to a service: its base address and the auth headers it carries."""

    name: WayName
    url: str
    # Not in repr: a way in a log line or a traceback must not show the key.
    headers: dict[str, str] = field(repr=False)

    @property
    def via_b_api(self) -> bool:
        return self.name != "direct"

    @property
    def credential(self) -> Optional[str]:
        """What a refusal of this way is remembered under: the forwarded value."""
        return self.headers.get("Authorization") if self.name == "forwarded" else None


def _fingerprint(credential: Optional[str]) -> str:
    if not credential:
        return ""
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()[:16]


class RefusalMemory:
    """
    Ways the B-API refused, per service: until when they are skipped and why.
    A forwarded credential is kept under its fingerprint, the others under "".
    """

    def __init__(self) -> None:
        self._until: OrderedDict[tuple[str, str, str], tuple[float, str]] = (
            OrderedDict()
        )

    def refused(
        self,
        service: Service,
        way: WayName,
        reason: str,
        credential: Optional[str] = None,
    ) -> None:
        if not self.skipped(service, way, credential):
            logger.warning(
                f"⚠️ {service}: Weg '{way}' abgelehnt ({reason}); für "
                f"{REFUSED_FOR_SECONDS:.0f} s übersprungen"
                + (" (nur diese Anmeldung)." if credential else ".")
            )
        key = (service, way, _fingerprint(credential))
        self._until[key] = (time.monotonic() + REFUSED_FOR_SECONDS, reason)
        self._until.move_to_end(key)
        while len(self._until) > MAX_REMEMBERED:
            self._until.popitem(last=False)

    def skipped(
        self, service: Service, way: WayName, credential: Optional[str] = None
    ) -> bool:
        return self.reason(service, way, credential) is not None

    def reason(
        self, service: Service, way: WayName, credential: Optional[str] = None
    ) -> Optional[str]:
        """Why `way` is skipped right now, None if it is not."""
        key = (service, way, _fingerprint(credential))
        entry = self._until.get(key)
        if entry is None:
            return None
        until, reason = entry
        if time.monotonic() >= until:
            del self._until[key]
            logger.info(f"ℹ️ {service}: Weg '{way}' wird wieder versucht.")
            return None
        return reason

    def snapshot(self) -> dict[str, float]:
        """
        Remaining seconds per refused way, for diagnostics; forwarded
        credentials as one entry with the longest remaining time.
        """
        now = time.monotonic()
        out: dict[str, float] = {}
        for (service, way, _), (until, _reason) in self._until.items():
            if until > now:
                name = f"{service}:{way}"
                out[name] = max(out.get(name, 0), round(until - now))
        return out

    def clear(self) -> None:
        self._until.clear()


class Ways(list):
    """The ways of one call; `refusal`: why a skipped way was refused, if any."""

    refusal: Optional[str] = None


REFUSALS = RefusalMemory()


def is_b_api_address(url: str, settings: Settings) -> bool:
    base = settings.b_api_base_url
    return bool(base) and (url == base or url.startswith(f"{base}/"))


def ways_to(
    service: Service,
    settings: Settings,
    b_api_url: Optional[str],
    direct_url: Optional[str],
) -> Ways:
    """
    The ways to `service`, in the order they are tried, without the ones the
    B-API refused lately - unless that leaves none: then all are tried again
    rather than none. `refusal` of the result names the first skipped one, so
    the answer is the same as when it was refused in this very call.
    """
    ways: list[Way] = []
    if b_api_url:
        mode = settings.b_api_auth_mode
        forwarded = forwarded_credential() if mode != "static" else None
        if forwarded:
            ways.append(Way("forwarded", b_api_url, {"Authorization": forwarded}))
        if mode != "forward" and settings.b_api_key:
            ways.append(Way("own", b_api_url, {"X-API-KEY": settings.b_api_key}))
        if settings.b_api_services == "auto" and direct_url:
            ways.append(Way("direct", direct_url, {}))
    elif direct_url:
        ways.append(Way("direct", direct_url, {}))
    usable = Ways()
    for way in ways:
        reason = (
            REFUSALS.reason(service, way.name, way.credential)
            if way.via_b_api
            else None
        )
        if reason is None:
            usable.append(way)
        elif usable.refusal is None:
            usable.refusal = reason
    if usable:
        return usable
    return Ways(ways)


def text_extraction_ways(settings: Settings) -> Ways:
    """
    An address of the B-API, with the service of the repository's environment
    as the direct way; any other configured address is the only way.
    """
    configured = settings.text_extraction_api_url
    if configured and is_b_api_address(configured, settings):
        direct = environment_text_extraction(settings.repository_url)
        return ways_to("text-extraction", settings, configured, direct or None)
    return ways_to("text-extraction", settings, None, configured or None)


def geocoder_ways(settings: Settings) -> Ways:
    url = settings.geocoder_api_url
    if is_b_api_address(url, settings):
        return ways_to("geocoder", settings, url, PHOTON_URL)
    return ways_to("geocoder", settings, None, url)
