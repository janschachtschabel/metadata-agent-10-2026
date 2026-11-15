"""
Turning what a page says about its licence into what edu-sharing stores.

Three shapes arrive here: a creativecommons.org deed link, a URI from the
openeduhub licence vocabulary, and free text. Only the first two map onto
`ccm:commonlicense_key`; everything else has to survive as `ccm:custom_license`,
because a licence this code cannot read is not the same as no licence. Guessing
in either direction publishes a claim about someone else's rights.

No HTTP, no state — the calls live in `repository_service`.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# The licence keys edu-sharing resolves (used for validation). Only the
# underscore form counts: 'CC BY-SA' is stored verbatim and is no licence for
# the repository (WLO-REPO-FELDER.md, measured 2026-08-12).
VALID_LICENSE_KEYS = {
    "NONE",
    "CC_0",
    "CC_BY",
    "CC_BY_SA",
    "CC_BY_ND",
    "CC_BY_NC",
    "CC_BY_NC_SA",
    "CC_BY_NC_ND",
    "PDM",
    "CUSTOM",
    "SCHULFUNK",
    "UNTERRICHTS_UND_LEHRMEDIEN",
    "COPYRIGHT_FREE",
    "COPYRIGHT_LICENSE",
}

_KEY_SEPARATORS = re.compile(r"[\s_-]+")


def resolving_key(key: str) -> str | None:
    """
    The key the repository resolves for a spelling of it, or None.

    'CC BY-SA', 'cc-by-sa' and 'CC0' are how the licences are written
    everywhere else, and the schema's vocabulary offered them until 2.0.0;
    they used to pass the validation as they were (audit 14.11., R-09).
    """
    canonical = _KEY_SEPARATORS.sub("_", key.strip().upper())
    if canonical == "CC0":
        canonical = "CC_0"
    return canonical if canonical in VALID_LICENSE_KEYS else None


# creativecommons.org licence deeds: /licenses/<code>/<version>[/<jurisdiction>].
# Ported licences (2.0–3.0) append a country ('de', 'uk', 'scotland') or 'igo',
# which must not end up in the version. Matched anywhere in the value, because
# the link often sits inside a sentence. 'deed.de' and 'legalcode' after the
# version name a page of the licence, not a jurisdiction.
_CC_LICENSE_URL = re.compile(
    r"creativecommons\.org/licenses/([a-z-]+)/(\d+(?:\.\d+)?)"
    r"(?:/(?!deed\b|legalcode\b)([a-z]{2,})(?![a-z.]))?",
    re.IGNORECASE,
)
_CC_PUBLIC_DOMAIN_URL = re.compile(
    r"creativecommons\.org/publicdomain/(zero|mark)/(\d+(?:\.\d+)?)", re.IGNORECASE
)

# The two public-domain deeds do not follow the CC_<code> naming.
_CC_PUBLIC_DOMAIN_KEYS = {"zero": "CC_0", "mark": "PDM"}


def map_creative_commons_url(value: str) -> dict | None:
    """
    Map a creativecommons.org deed URL to licence key and version.

    Returns None when nothing matches, or when the code is not one of the seven
    licences edu-sharing knows — an unrecognised deed must stay readable text
    rather than become a wrong key.
    """
    match = _CC_LICENSE_URL.search(value)
    if match:
        code, version = match.group(1), match.group(2)
        key = "CC_" + code.upper().replace("-", "_")
    else:
        match = _CC_PUBLIC_DOMAIN_URL.search(value)
        if not match:
            return None
        key = _CC_PUBLIC_DOMAIN_KEYS[match.group(1).lower()]
        version = match.group(2)

    if key not in VALID_LICENSE_KEYS:
        return None

    mapped = {"ccm:commonlicense_key": [key]}
    # ccm:commonlicense_cc_version is a CC field; the public domain mark is not
    # a CC licence and carries no version there.
    if key.startswith("CC"):
        mapped["ccm:commonlicense_cc_version"] = [version]
    return mapped


# Where the OpenEduHub vocabularies live. A link elsewhere that happens to end
# in a key ('…/nutzungsbedingungen/PDM') is somebody's own licence text.
_VOCABULARY_PREFIXES = (
    "http://w3id.org/openeduhub/vocabs/",
    "https://w3id.org/openeduhub/vocabs/",
)


def is_ported(value: str) -> bool:
    """Whether a creativecommons.org licence link names a jurisdiction."""
    match = _CC_LICENSE_URL.search(value)
    return bool(match and match.group(3))


def map_license_vocabulary_uri(value: str) -> dict | None:
    """
    Map a licence vocabulary URI to key (+ CC version), or None if it is not one.

    Returning None is what keeps an unmappable value alive: `ccm:custom_license`
    is a free-text field whose prompt explicitly asks for links to own licence
    terms, so 'contains a slash' does not make a value a vocabulary URI.
    """
    if not value.startswith(_VOCABULARY_PREFIXES):
        return None
    license_key = value.split("/")[-1]

    if license_key.endswith("_40") and license_key[:-3] in VALID_LICENSE_KEYS:
        return {
            "ccm:commonlicense_key": [license_key[:-3]],
            "ccm:commonlicense_cc_version": ["4.0"],
        }
    if license_key == "OTHER":
        return {"ccm:commonlicense_key": ["CUSTOM"]}
    if license_key in VALID_LICENSE_KEYS:
        return {"ccm:commonlicense_key": [license_key]}
    return None


def custom_license_text(value: Any) -> str | None:
    """A ccm:custom_license value as the one text it holds, or None."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("uri") or value.get("label")
    return value if value and isinstance(value, str) else None


def license_from_custom(value: Any) -> tuple[dict | None, bool]:
    """
    What a ccm:custom_license is written as: (key and version, value replaced).

    (None, False) for free text and links that map onto nothing. `replaced`
    says the value itself is not written, because key and version say all of
    it. The write path and the comparison of /upload/verify both read this.
    """
    text = custom_license_text(value)
    if not text or "/" not in text:
        return None, False
    mapped = map_creative_commons_url(text) or map_license_vocabulary_uri(text)
    if not mapped:
        return None, False
    # Dropped only when it was nothing but the link. A sentence around it
    # ('© Uni X, Abbildungen CC BY') carries more than the key can express,
    # and so does the jurisdiction of a ported licence ('/3.0/de/').
    return mapped, len(text.split()) == 1 and not is_ported(text)


def transform_license(normalized: dict, original: dict):
    """Transform license URLs to key + version format.

    A ccm:custom_license that maps onto the licence vocabulary becomes
    ccm:commonlicense_key (+ version) and is removed, because it would then be
    stored twice. Everything else — free text and links we cannot map — is kept
    and marked CUSTOM so the editors see what the source actually said.
    Validates ccm:commonlicense_key against known edu-sharing keys.
    """
    license_val = custom_license_text(original.get("ccm:custom_license"))

    if license_val:
        mapped, replaced = license_from_custom(license_val)
        if mapped:
            normalized.update(mapped)
            if replaced:
                normalized.pop("ccm:custom_license", None)
        elif "ccm:commonlicense_key" not in normalized:
            normalized["ccm:commonlicense_key"] = ["CUSTOM"]

    # Validate ccm:commonlicense_key against known keys; a familiar spelling
    # of one is written as the key itself
    if "ccm:commonlicense_key" in normalized:
        key_list = normalized["ccm:commonlicense_key"]
        if isinstance(key_list, list) and key_list:
            key = str(key_list[0])
            resolved = resolving_key(key)
            if resolved is None:
                logger.warning(f"Invalid license key removed: {key.strip()[:80]}")
                del normalized["ccm:commonlicense_key"]
                normalized.pop("ccm:commonlicense_cc_version", None)
            else:
                normalized["ccm:commonlicense_key"] = [resolved, *key_list[1:]]

    # Default CC version only for CC-type licenses
    if (
        "ccm:commonlicense_key" in normalized
        and "ccm:commonlicense_cc_version" not in normalized
    ):
        key = (
            normalized["ccm:commonlicense_key"][0]
            if normalized["ccm:commonlicense_key"]
            else ""
        )
        # CC0 was only ever published as 1.0. For the BY family 4.0 is a
        # guess, the documented one (WLO-REPO-FELDER.md, "sonst 4.0 für
        # CC-Keys ohne Version"): the repository resolves a key without a
        # version to the 4.0 deed as well.
        if str(key) == "CC_0":
            normalized["ccm:commonlicense_cc_version"] = ["1.0"]
        elif str(key).startswith("CC"):
            normalized["ccm:commonlicense_cc_version"] = ["4.0"]
