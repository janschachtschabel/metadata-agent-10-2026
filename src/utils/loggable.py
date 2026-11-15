"""
Caller-supplied values made safe for log lines and error messages: URLs
(audit 18.10. final, S-17) and whatever else a message repeats.
"""

import re
from urllib.parse import urlsplit


# What a message repeats of a value the request named (audit 14.11., S-34).
MAX_QUOTED_LENGTH = 200


def shortened(value: object, limit: int = MAX_QUOTED_LENGTH) -> str:
    """A value as an error message or log line repeats it: cut to `limit`."""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


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


# The scheme has a length: unbounded, every position of a long run of letters
# was tried as the start of one, to the end of the run - quadratic (audit
# 14.11., S-31). No scheme in use comes near 32 characters.
_URL = re.compile(r"[a-z][a-z0-9+.-]{0,31}://[^\s\"'<>]+", re.IGNORECASE)

# A browser error is a few lines. A URL of the request can be megabytes.
MAX_LOGGABLE_TEXT = 2000


def loggable_text(text: object) -> str:
    """
    A message - a browser error, say - on one line, each URL in it as above,
    cut to MAX_LOGGABLE_TEXT characters before the URLs are looked for.

    Playwright names the URL it failed on, query string included.
    """
    line = str(text)
    cut = len(line) > MAX_LOGGABLE_TEXT
    line = " ".join(line[:MAX_LOGGABLE_TEXT].splitlines())

    def shown(match: re.Match) -> str:
        url = match.group(0)
        scheme, _, rest = url.partition("://")
        # Cut inside the authority: what is left may be half a password.
        if cut and match.end() == len(line) and "/" not in rest:
            return f"{scheme}://"
        return loggable_url(url)

    line = _URL.sub(shown, line)
    return line + "…" if cut else line
