"""
One TLS context for the outgoing HTTPS clients of the process (audit 14.11.2026, L-41 / P-13).

httpx builds a TLS context for every client it creates: loading the certificate
bundle took about 400 ms on the measured machine, synchronously on the event
loop - for each geocoder lookup and for each LLM client of a request that picks
its own provider or model. The context holds no connection and no state of a
request, so every client can verify against the same one.
"""

import ssl
from functools import lru_cache

import httpx


@lru_cache(maxsize=1)
def shared_tls_context() -> ssl.SSLContext:
    """httpx's default context (certificate and host name are verified), built once."""
    return httpx.create_ssl_context()
