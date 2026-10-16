"""Validation for user-controlled URLs that may trigger outbound network access."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


class PublicUrlError(ValueError):
    """Raised when a URL is not an HTTP(S) target on the public internet."""


def _is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # Deprecated site-local space (RFC 3879); Python still calls it global.
        if ip.is_site_local:
            return False
        # Transition mechanisms can encode private IPv4 destinations.
        if ip.ipv4_mapped:
            return _is_public_address(str(ip.ipv4_mapped))
        if ip.sixtofour or ip.teredo:
            return False
        if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            return False
    return True


async def require_public_http_url(url: str) -> str:
    """
    Validate syntax and resolve the host once; every address must be public.

    The client that connects afterwards resolves again, so this does not stop
    DNS rebinding — that needs egress rules at the network level.
    """
    if not isinstance(url, str) or not url.strip():
        raise PublicUrlError("URL fehlt oder ist leer.")

    candidate = url.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise PublicUrlError("URL ist syntaktisch ungültig.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise PublicUrlError("Nur öffentliche http://- und https://-URLs sind erlaubt.")
    if parsed.username is not None or parsed.password is not None:
        raise PublicUrlError("Zugangsdaten in URLs sind nicht erlaubt.")

    if not parsed.hostname:
        raise PublicUrlError("URL enthält keinen Hostnamen.")

    # Check the name the client will actually resolve. Python's own "idna"
    # codec implements IDNA 2003 and turns 'ß' into 'ss'; httpx (IDNA 2008)
    # connects to 'xn--zca'. Chromium's request URLs arrive here already in
    # ASCII form, so the same conversion serves both clients. Only scheme and
    # authority go to httpx: it refuses URLs over 64 KiB, and the length of
    # path and query must not decide a host check.
    try:
        authority = httpx.URL(f"{parsed.scheme}://{parsed.netloc}")
        hostname = authority.raw_host.decode("ascii")
    except (httpx.InvalidURL, UnicodeError) as exc:
        raise PublicUrlError("URL ist syntaktisch ungültig.") from exc

    await resolve_public_host(
        hostname, port or (443 if parsed.scheme == "https" else 80)
    )
    return candidate


async def resolve_public_host(hostname: str, port: int) -> list[str]:
    """
    Resolve once, reject mixed/private answers, return numeric connect targets.

    `hostname` must be in ASCII (punycode) form — see require_public_http_url.
    """
    hostname = hostname.rstrip(".").lower()
    if not hostname or not hostname.isascii():
        raise PublicUrlError("Hostname ist ungültig.")
    if hostname == "localhost" or hostname.endswith(
        (".localhost", ".local", ".internal", ".lan", ".home.arpa")
    ):
        raise PublicUrlError("Interne Hostnamen sind nicht erlaubt.")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_address(str(literal)):
            raise PublicUrlError("Nur öffentliche IP-Adressen sind erlaubt.")
        return [str(literal)]

    if len(hostname) > 253 or any(
        not label or len(label) > 63 for label in hostname.split(".")
    ):
        raise PublicUrlError("Hostname ist ungültig oder zu lang.")

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise PublicUrlError("Hostname konnte nicht aufgelöst werden.") from exc

    resolved = {entry[4][0].split("%", 1)[0] for entry in addresses if entry[4]}
    if not resolved:
        raise PublicUrlError("Hostname konnte nicht aufgelöst werden.")
    if any(not _is_public_address(address) for address in resolved):
        raise PublicUrlError(
            "URL löst auf eine nicht öffentliche oder interne IP-Adresse auf."
        )

    return sorted(resolved)
