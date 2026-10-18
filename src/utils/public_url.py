"""Validation for user-controlled URLs that may trigger outbound network access."""

import asyncio
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

    A client that connects afterwards resolves again, so this alone does not
    stop DNS rebinding — use pin_public_url where this service connects itself,
    egress rules for the browser.
    """
    candidate, hostname, port = _checked_url(url)
    await resolve_public_host(hostname, port)
    return candidate


async def pin_public_url(
    url: str,
) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    """
    (urls, headers, extensions) that connect only to addresses that were checked.

    The host of the URL is replaced by each vetted address, IPv4 first: the
    chart allows no IPv6 egress, and a client used to fall back over all
    resolved addresses (try the next on a connect error). The Host header and
    the TLS server name keep the original name, so virtual hosting and the
    certificate check work as before. A second DNS answer (rebinding) can no
    longer point the connection inward (audit S-04).
    """
    candidate, hostname, port = _checked_url(url)
    addresses = await resolve_public_host(hostname, port)
    try:
        original = httpx.URL(candidate)
    except httpx.InvalidURL as exc:
        # Path or query httpx cannot send (control characters, over 64 KiB).
        raise PublicUrlError("URL ist syntaktisch ungültig.") from exc
    ordered = sorted(addresses, key=lambda a: ipaddress.ip_address(a).version)
    targets = [str(original.copy_with(host=address)) for address in ordered]
    headers = {"Host": original.netloc.decode("ascii")}
    extensions: dict[str, Any] = (
        {"sni_hostname": hostname} if original.scheme == "https" else {}
    )
    return targets, headers, extensions


def _checked_url(url: str) -> tuple[str, str, int]:
    """(url, ASCII hostname, port) of a syntactically acceptable http(s) URL."""
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
    _require_plain_host(hostname)

    # Handed on with the host as checked (audit 18.10. final, S-14): the text
    # extraction and Chromium parse the URL again, and must find the same host.
    # Path and query stay as they were, without the tabs and line breaks
    # urlsplit (like every browser) drops.
    normalized = urlunsplit(
        (
            authority.scheme,
            authority.netloc.decode("ascii"),
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
    return normalized, hostname, port or (443 if parsed.scheme == "https" else 80)


# Letters, digits, hyphen - and underscore, which DNS allows and real hosts use.
# Everything else ('\', '%', spaces, control characters) is read differently by
# Python and WHATWG parsers: 'http://127.0.0.1\x.example/' is one hostname for
# httpx and the host 127.0.0.1 for Chromium.
_HOST_LABEL = re.compile(r"[a-z0-9_-]+", re.IGNORECASE)


def _require_plain_host(hostname: str) -> None:
    """Accept an IP literal or a name of plain labels (after IDNA), else raise."""
    try:
        ipaddress.ip_address(hostname)
        return
    except ValueError:
        pass
    labels = hostname[:-1].split(".") if hostname.endswith(".") else hostname.split(".")
    if not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise PublicUrlError("Hostname enthält unzulässige Zeichen.")


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

    # sockaddr[0] is the address string for AF_INET and AF_INET6.
    resolved = {str(entry[4][0]).split("%", 1)[0] for entry in addresses if entry[4]}
    if not resolved:
        raise PublicUrlError("Hostname konnte nicht aufgelöst werden.")
    if any(not _is_public_address(address) for address in resolved):
        raise PublicUrlError(
            "URL löst auf eine nicht öffentliche oder interne IP-Adresse auf."
        )

    return sorted(resolved)
