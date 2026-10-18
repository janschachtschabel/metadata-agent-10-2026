"""Caller-supplied URLs made safe for log lines (audit 18.10. final, S-17)."""

import re
from urllib.parse import urlsplit


def loggable_url(url: object) -> str:
    """
    Scheme, host and path on one line.

    Query strings carry tokens, user info passwords; a line break inside the URL
    would forge a second log line with LOG_FORMAT=text. Never raises.
    """
    text = str(url) if url is not None else ""
    text = text.replace("\r", " ").replace("\n", " ")
    try:
        parts = urlsplit(text)
    except ValueError:
        return text[:200]
    if not parts.scheme or not parts.netloc:
        return text[:200]
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{parts.path}"[:500]


_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)


def loggable_text(text: object) -> str:
    """
    A message - a browser error, say - on one line, each URL in it as above.

    Playwright names the URL it failed on, query string included.
    """
    line = " ".join(str(text).splitlines())
    return _URL.sub(lambda match: loggable_url(match.group(0)), line)
