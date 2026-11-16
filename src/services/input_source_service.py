"""Service for fetching input data from various sources (URL, NodeID, etc.)."""

import httpx
import json
import logging
import re
from functools import lru_cache
from typing import Any, Callable, NoReturn, Optional
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ..config import get_settings
from ..utils.loggable import loggable_url
from ..utils.public_url import PublicUrlError, require_public_http_url
from ..utils.schema_loader import get_available_schemas, get_schema_fields
from ..utils.tls_context import shared_tls_context
from ..metrics import OWN_SERVICE_HOOKS
from .llm_auth import b_api_auth
from .llm_errors import LLMCredentialsMissing
from .repository_diff import WRITTEN_AS
from .repository_service import build_auth_header
from .repository_support import rest_base
from .service_ways import REFUSALS, Way, text_extraction_ways

logger = logging.getLogger(__name__)


class TextExtractionError(Exception):
    """
    The text extraction service delivered no text for a URL - with the cause.

    httpx timeouts carry no message, so a bare str(e) used to end as
    "Failed to fetch input: " with nothing after the colon.
    """

    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class NodeWithoutText(Exception):
    """
    The node holds nothing to read: no stored text, no title, description or
    keywords, no page that can be fetched. The caller's choice of node, not a
    fault of the repository - and an exception of its own, so that no other
    ValueError from below passes for it (audit 14.11., list S-N).
    """


# Not a problem of the page: a key the B-API refuses, no key, no service. The
# fallback to a node's title and description must not cover these up (L-16).
SETUP_ERROR_CODES = frozenset(
    {
        "TEXT_EXTRACTION_AUTH_REFUSED",
        "TEXT_EXTRACTION_AUTH_MISSING",
        "TEXT_EXTRACTION_NOT_CONFIGURED",
    }
)


# Failures that say something about the page, not about the way to the
# extraction: another way would wait as long or find as little.
_ABOUT_THE_PAGE = frozenset({"TEXT_EXTRACTION_TIMEOUT", "TEXT_EXTRACTION_EMPTY"})
# Failures of a credential: the next way may carry another one.
_ABOUT_THE_CREDENTIAL = frozenset(
    {"TEXT_EXTRACTION_AUTH_REFUSED", "TEXT_EXTRACTION_RATE_LIMITED"}
)


# The stored text of a node is cut like every other source text: the same
# number as MAX_TEXT_LENGTH of the API layer, which cuts again with the source
# URL in front.
MAX_NODE_TEXT_LENGTH = 150_000
# How much of a textContent answer is read. JSON takes up to six bytes for a
# character (\uXXXX); what lies beyond cannot be part of the text that is kept.
MAX_NODE_TEXT_BYTES = 6 * MAX_NODE_TEXT_LENGTH

_TEXT_KEY = re.compile(r'"(text|html|raw)"\s*:\s*"')
_HALF_SURROGATE_AT_END = re.compile(r"[\ud800-\udbff]+$")

# Properties the write path stores under another name than the schema's.
_SCHEMA_NAME_OF = {written: name for name, written in WRITTEN_AS.items()}


@lru_cache(maxsize=16)
def _fields_declared_in(context: str, version: str) -> frozenset[str]:
    declared: set[str] = set()
    for schema in get_available_schemas(context, version):
        for schema_field in get_schema_fields(context, version, schema["file"]):
            declared.add(schema_field.get("id", ""))
            declared.add((schema_field.get("system") or {}).get("path") or "")
    return frozenset(declared - {""})


def _declared_fields(context: str, version: str) -> frozenset[str]:
    """
    Every field a schema of the context declares, as the repository names it.

    A context or version that cannot be read falls back to the default
    schemata: the request is refused for it elsewhere, the read stays usable.
    """
    try:
        return _fields_declared_in(context, version)
    except Exception as e:
        logger.warning(f"⚠️ Schemata of {context!r} {version!r} not readable: {e}")
        return _fields_declared_in("default", "latest")


async def _read_up_to(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    """(the first `limit` bytes of the body, whether that was all of it)."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            return b"".join(chunks)[:limit], False
    return b"".join(chunks), True


def text_of_cut_json(prefix: str) -> Optional[str]:
    """
    The text of a textContent answer that was read only in part.

    The answer is one JSON object with `text`, `html` and `raw`; cut off it
    does not parse. The value of the first of them that starts inside
    `prefix` is decoded up to its end or the cut (a quote inside a JSON string
    is escaped, so the key pattern only matches real keys).
    """
    starts: dict[str, int] = {}
    for match in _TEXT_KEY.finditer(prefix):
        starts.setdefault(match.group(1), match.end())
    for key in ("text", "html", "raw"):
        if key in starts:
            return _json_string_from(prefix, starts[key])
    return None


def _json_string_from(source: str, start: int) -> Optional[str]:
    """The JSON string starting at `start`, up to its quote or the end of `source`."""
    end = len(source)
    position = start
    while position < len(source):
        character = source[position]
        if character == '"':
            end = position
            break
        if character == "\\":
            width = 6 if source[position + 1 : position + 2] == "u" else 2
            if position + width > len(source):
                # Cut inside an escape: it does not belong to the text.
                end = position
                break
            position += width
        else:
            position += 1
    try:
        text = json.loads(f'"{source[start:end]}"')
    except ValueError:
        return None
    # Half of a surrogate pair at the cut cannot be encoded later.
    return _HALF_SURROGATE_AT_END.sub("", text)


def _stored_text_warnings(text: Optional[str]) -> list[str]:
    """What the caller must be told about a stored text that fills the limit."""
    if not text or len(text) < MAX_NODE_TEXT_LENGTH:
        return []
    return [
        f"Gespeicherter Volltext des Nodes auf {MAX_NODE_TEXT_LENGTH} Zeichen gekürzt."
    ]


def _text_from_metadata(
    e: Exception, existing_metadata: dict[str, Any], node_id: str
) -> tuple[str, str]:
    """
    (text, warning) for a node whose page could not be fetched: what the
    repository entry says about it. Raises NodeWithoutText when that is nothing.
    """
    # Final fallback: build text from metadata - and say so. A result
    # from title and description alone must not pass for one from
    # the page.
    cause = (
        e.message
        if isinstance(e, TextExtractionError)
        else (str(e) or type(e).__name__)
    )
    logger.warning(f"URL extraction failed: {cause}")
    # Callers see the code; the message names internal hosts and
    # stays in the log.
    reported = e.code if isinstance(e, TextExtractionError) else cause
    title = existing_metadata.get("cclom:title", "")
    description = existing_metadata.get("cclom:general_description", "")
    keywords = existing_metadata.get("cclom:general_keyword", [])

    if isinstance(keywords, list):
        keywords_text = ", ".join(keywords)
    else:
        keywords_text = keywords or ""

    text_parts = []
    if title:
        text_parts.append(title)
    if description:
        text_parts.append(description)
    if keywords_text:
        text_parts.append(f"Keywords: {keywords_text}")

    text = "\n\n".join(text_parts)

    if not text:
        raise NodeWithoutText(
            f"No text content available for node {node_id} and URL extraction failed: {reported}"
        )

    logger.info(f"Using fallback text from metadata: {len(text)} chars")
    return text, (
        f"Webseitentext nicht abrufbar ({reported}) – generiert wurde nur aus "
        "Titel, Beschreibung und Schlagworten des Repository-Eintrags."
    )


@dataclass
class InputData:
    """Result from input source fetch."""

    text: str
    existing_metadata: Optional[dict[str, Any]] = None
    source_url: Optional[str] = None
    node_id: Optional[str] = None
    repository: Optional[str] = None
    # What the caller must pass on about how the text was obtained.
    warnings: list[str] = field(default_factory=list)


class InputSourceService:
    """Service for fetching input data from various sources."""

    def __init__(self):
        self.settings = get_settings()
        self.timeout = httpx.Timeout(30.0, connect=10.0)
        self.http_client = httpx.AsyncClient(
            timeout=self.timeout,
            event_hooks=OWN_SERVICE_HOOKS,
            verify=shared_tls_context(),
        )

    async def close(self):
        """Close the shared HTTP client."""
        if self.http_client:
            await self.http_client.aclose()

    def _get_repository_base_url(self, repository: str = "") -> str:
        """The repository's REST base, as the write path builds it (param ignored)."""
        return rest_base(self.settings.repository_url)

    def _repository_headers(self) -> dict[str, str]:
        """
        Headers for repository reads, authenticated with the service account.

        Anonymous reads return 403 for every node that is not publicly readable —
        which includes freshly uploaded inbox nodes. Falls back to anonymous
        access when no credentials are configured.
        """
        headers = {"accept": "application/json"}
        auth_header = build_auth_header(
            self.settings.wlo_guest_username, self.settings.wlo_guest_password
        )
        if auth_header:
            headers["Authorization"] = auth_header
        return headers

    async def fetch_from_url(
        self,
        url: str,
        method: str = "browser",
        lang: str = "auto",
        output_format: str = "markdown",
    ) -> str:
        """
        Fetch text content from URL via text extraction API.

        Args:
            url: URL to fetch content from
            method: Extraction method ('simple' or 'browser')
            lang: Language hint ('auto', 'de', 'en')

        Returns:
            Extracted text content

        Raises:
            PublicUrlError: the URL does not point at a public http(s) target
        """
        # Refuse internal targets before they are handed to the crawler. Only
        # this URL is checked: redirects and subresources the remote crawler
        # follows itself need egress rules on the crawler's side.
        url = await require_public_http_url(url)
        found = text_extraction_ways(self.settings)
        if not found:
            self._no_way_to_the_extraction()
        ways = list(found)

        payload = {
            "url": url,
            "method": method,
            "browser_location": None,
            "lang": lang,
            "output_format": output_format,
            "preference": "none",
        }

        logger.info(f"Fetching text from URL: {loggable_url(url)} (method: {method})")

        # Along the ways of service_ways.py: the forwarded credential, the
        # agent's own key, the service itself. The first failure is the one
        # the caller hears about when no way is left - also when the way that
        # failed first was only skipped for a remembered refusal: the same
        # situation gives the same code (review 16.11.).
        first: Optional[TextExtractionError] = (
            TextExtractionError("TEXT_EXTRACTION_AUTH_REFUSED", found.refusal, 502)
            if found.refusal
            else None
        )
        while ways:
            way = ways[0]
            try:
                return await self._extract_along(way, payload, url)
            except TextExtractionError as e:
                first = first or e
                if e.code in _ABOUT_THE_PAGE:
                    raise
                if e.code == "TEXT_EXTRACTION_AUTH_REFUSED":
                    REFUSALS.refused(
                        "text-extraction",
                        way.name,
                        e.message,
                        credential=way.credential,
                    )
                if e.code == "TEXT_EXTRACTION_RATE_LIMITED" and way.name == "forwarded":
                    # The caller's quota is used up: not on the agent's key.
                    ways = [w for w in ways if w.name not in ("forwarded", "own")]
                elif e.code in _ABOUT_THE_CREDENTIAL or not way.via_b_api:
                    ways = [w for w in ways if w is not way]
                else:
                    # The B-API itself failed: another key would not help.
                    ways = [w for w in ways if not w.via_b_api]
                if not ways:
                    raise first from None
                logger.warning(
                    f"⚠️ Textextraktion über Weg '{way.name}': {e.code}; "
                    f"nächster Weg: '{ways[0].name}'."
                )
        raise first or TextExtractionError(
            "TEXT_EXTRACTION_NOT_CONFIGURED", "Kein Weg zur Textextraktion.", 503
        )

    def _no_way_to_the_extraction(self) -> NoReturn:
        """No way at all: no address, or a B-API address and no key for it."""
        configured = self.settings.text_extraction_api_url
        if not configured:
            raise TextExtractionError(
                "TEXT_EXTRACTION_NOT_CONFIGURED",
                "Für diese Installation ist kein Textextraktionsdienst eingerichtet "
                "(METADATA_AGENT_TEXT_EXTRACTION_API_URL).",
                503,
            )
        try:
            b_api_auth(self.settings)
        except LLMCredentialsMissing as e:
            raise TextExtractionError(
                "TEXT_EXTRACTION_AUTH_MISSING",
                f"Kein Schlüssel für die Textextraktion über die B-API: {e}",
                502,
            ) from e
        raise TextExtractionError(
            "TEXT_EXTRACTION_NOT_CONFIGURED", "Kein Weg zur Textextraktion.", 503
        )

    async def _extract_along(self, way: Way, payload: dict[str, Any], url: str) -> str:
        """The text of the page, fetched along `way`."""
        api_url = f"{way.url}/from-url"
        # The message reaches the client (detail, error.message): the service's
        # host is internal and goes to the log only (audit S-06, review #5).
        # The host only: user and password of a configured URL stay out.
        service = urlsplit(api_url).hostname or "text-extraction"

        def failure(code: str, message: str, status: int) -> TextExtractionError:
            logger.warning(f"{code}: {message} [{service}, Weg '{way.name}']")
            return TextExtractionError(code, message, status)

        response = await self._post_extraction(api_url, payload, way.headers, failure)

        try:
            data = response.json()
            text = data.get("text", "")
        except (ValueError, AttributeError):
            # A proxy's error page with status 200, or JSON that is no object.
            raise failure(
                "TEXT_EXTRACTION_FAILED",
                "Der Textextraktionsdienst lieferte keine lesbare Antwort.",
                502,
            ) from None

        if not text:
            raise TextExtractionError(
                "TEXT_EXTRACTION_EMPTY",
                f"Für {loggable_url(url)} hat der Textextraktionsdienst keinen Text "
                "geliefert.",
                422,
            )

        logger.info(f"Extracted {len(text)} characters from URL")
        return text

    async def _post_extraction(
        self,
        api_url: str,
        payload: dict[str, Any],
        auth: dict[str, str],
        failure: Callable[[str, str, int], TextExtractionError],
    ) -> httpx.Response:
        """The request to the text extraction; whatever goes wrong is `failure`'s."""
        try:
            response = await self.http_client.post(
                api_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "accept": "application/json",
                    **auth,
                },
            )
            if auth and response.status_code in (401, 403):
                raise failure(
                    "TEXT_EXTRACTION_AUTH_REFUSED",
                    "Die B-API hat den Schlüssel für die Textextraktion abgelehnt "
                    f"(HTTP {response.status_code}): "
                    + (
                        "Sie kennt ihn nicht, oder er ist ungültig "
                        "(Proxy-Dienst text-extraction)."
                        if response.status_code == 401
                        else "Ihm fehlt das Recht für den Proxy-Dienst text-extraction."
                    ),
                    502,
                )
            if response.status_code == 504:
                # A gateway on the way gave up waiting for the page: as slow as
                # a timeout of our own, and another way would wait as long.
                raise failure(
                    "TEXT_EXTRACTION_TIMEOUT",
                    "Der Textextraktionsdienst hat nicht rechtzeitig geantwortet "
                    "(HTTP 504 eines Gateways).",
                    504,
                )
            if response.status_code == 429:
                # Like a throttled LLM call: worth trying again later.
                raise failure(
                    "TEXT_EXTRACTION_RATE_LIMITED",
                    "Der Textextraktionsdienst ist gerade gedrosselt (HTTP 429).",
                    503,
                )
            response.raise_for_status()
        except httpx.ConnectTimeout as e:
            raise failure(
                "TEXT_EXTRACTION_CONNECT_TIMEOUT",
                "Textextraktionsdienst nicht erreichbar: kein Verbindungsaufbau "
                f"innerhalb von {self.timeout.connect:g} s.",
                504,
            ) from e
        except httpx.TimeoutException as e:
            raise failure(
                "TEXT_EXTRACTION_TIMEOUT",
                "Textextraktionsdienst hat nicht innerhalb von "
                f"{self.timeout.read:g} s geantwortet ({type(e).__name__}).",
                504,
            ) from e
        except httpx.ConnectError as e:
            raise failure(
                "TEXT_EXTRACTION_UNREACHABLE",
                "Textextraktionsdienst nicht erreichbar "
                "(Verbindung abgelehnt oder Name nicht auflösbar).",
                502,
            ) from e
        except httpx.HTTPStatusError as e:
            raise failure(
                "TEXT_EXTRACTION_HTTP_ERROR",
                f"Textextraktionsdienst antwortete mit HTTP {e.response.status_code}.",
                502,
            ) from e
        except httpx.HTTPError as e:
            raise failure(
                "TEXT_EXTRACTION_FAILED",
                f"Textextraktionsdienst: Abruf fehlgeschlagen ({type(e).__name__}).",
                502,
            ) from e
        return response

    async def fetch_node_metadata(
        self, node_id: str, repository: str = "staging"
    ) -> dict[str, Any]:
        """
        Fetch metadata for a node from the repository.

        Args:
            node_id: The NodeID to fetch
            repository: 'prod' or 'staging'

        Returns:
            Node metadata including properties
        """
        base_url = self._get_repository_base_url(repository)
        api_url = (
            f"{base_url}/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-"
        )

        logger.info(f"Fetching node metadata: {node_id} from {repository}")

        response = await self.http_client.get(
            api_url, headers=self._repository_headers()
        )
        response.raise_for_status()

        data = response.json()
        return data.get("node", {})

    async def fetch_node_text_content(
        self, node_id: str, repository: str = "staging"
    ) -> Optional[str]:
        """
        Fetch text content for a node from the repository.

        Args:
            node_id: The NodeID to fetch
            repository: 'prod' or 'staging'

        Returns:
            Text content if available, None otherwise
        """
        base_url = self._get_repository_base_url(repository)
        api_url = f"{base_url}/node/v1/nodes/-home-/{node_id}/textContent"

        logger.info(f"Fetching node text content: {node_id} from {repository}")

        # Streamed: the answer of a node with a book as its text is not held
        # in memory before it is cut (audit 14.11., R-N).
        async with self.http_client.stream(
            "GET", api_url, headers=self._repository_headers()
        ) as response:
            if response.status_code in (403, 404):
                # The metadata read has answered for the node itself: this is
                # "no stored text", and the fallbacks run (audit 14.11., R-11).
                logger.info(f"No text content for node (HTTP {response.status_code})")
                return None
            response.raise_for_status()
            body, complete = await _read_up_to(response, MAX_NODE_TEXT_BYTES)

        if complete:
            data = json.loads(body)
            # Try text, then html, then raw
            text = data.get("text") or data.get("html") or data.get("raw")
        else:
            text = text_of_cut_json(body.decode("utf-8", errors="ignore"))

        if text:
            logger.info(f"Fetched {len(text)} characters of text content from node")
        else:
            logger.info("No text content available for node")

        return text[:MAX_NODE_TEXT_LENGTH] if isinstance(text, str) else text

    def _convert_node_properties_to_metadata(
        self,
        properties: dict[str, Any],
        context: str = "default",
        version: str = "latest",
    ) -> dict[str, Any]:
        """
        Convert repository node properties to flat metadata format.

        The repository returns properties as arrays (e.g., {"cclom:title": ["My Title"]}).
        We convert single-item arrays to scalar values.

        Only what a schema of the context declares is metadata. Everything
        else on a node is the repository's bookkeeping (sys:, cm:, virtual:,
        _DISPLAYNAME, workflow, permission history, VCARDs) and would travel
        through /generate into the next upload's ccm:oeh_extendedData (audit
        14.11., R-06) - as would the extended fields of the last upload.
        """
        metadata = {}
        declared = _declared_fields(context, version)

        for key, value in properties.items():
            if key.startswith("ccm:oeh_extended"):
                continue
            if key not in declared:
                # Stored under the repository's name, declared under the schema's.
                key = _SCHEMA_NAME_OF.get(key, key)
                if key not in declared:
                    continue

            if isinstance(value, list):
                if len(value) == 1:
                    metadata[key] = value[0]
                elif len(value) > 1:
                    metadata[key] = value
                # Skip empty lists
            else:
                metadata[key] = value

        return metadata

    async def fetch_from_node_id(
        self,
        node_id: str,
        repository: str = "staging",
        context: str = "default",
        version: str = "latest",
    ) -> InputData:
        """
        Fetch input data from repository by NodeID.

        Args:
            node_id: The NodeID to fetch
            repository: 'prod' or 'staging'
            context, version: the schemata that say which of the node's
                properties are metadata

        Returns:
            InputData with text and existing metadata
        """
        # Fetch metadata
        node_data = await self.fetch_node_metadata(node_id, repository)
        properties = node_data.get("properties", {})

        # Convert properties to flat metadata
        existing_metadata = self._convert_node_properties_to_metadata(
            properties, context, version
        )

        # Get the source URL from properties
        source_url = None
        wwwurl = properties.get("ccm:wwwurl", [])
        if wwwurl:
            source_url = wwwurl[0] if isinstance(wwwurl, list) else wwwurl

        # Fetch text content
        text = await self.fetch_node_text_content(node_id, repository)
        warnings = _stored_text_warnings(text)

        if not text:
            # Try to build text from title and description
            title = existing_metadata.get("cclom:title", "")
            description = existing_metadata.get("cclom:general_description", "")
            keywords = existing_metadata.get("cclom:general_keyword", [])

            if isinstance(keywords, list):
                keywords_text = ", ".join(keywords)
            else:
                keywords_text = keywords or ""

            text_parts = []
            if title:
                text_parts.append(title)
            if description:
                text_parts.append(description)
            if keywords_text:
                text_parts.append(f"Keywords: {keywords_text}")

            text = "\n\n".join(text_parts)

            if not text:
                raise NodeWithoutText(f"No text content available for node {node_id}")

            logger.info(f"Built text from metadata: {len(text)} characters")

        return InputData(
            text=text,
            existing_metadata=existing_metadata,
            source_url=source_url,
            node_id=node_id,
            repository=repository,
            warnings=warnings,
        )

    async def fetch_from_node_url(
        self,
        node_id: str,
        repository: str = "staging",
        source_url: Optional[str] = None,
        extraction_method: str = "browser",
        lang: str = "auto",
        output_format: str = "markdown",
        context: str = "default",
        version: str = "latest",
    ) -> InputData:
        """
        Fetch input data using NodeID for metadata and URL for text (fallback only).

        Logic:
        1. Fetch metadata from repository
        2. Get URL from ccm:wwwurl if not provided
        3. Check if stored fulltext exists in repository
        4. If fulltext exists: use metadata + fulltext (no crawler)
        5. If no fulltext: use crawler to fetch text from URL

        Args:
            node_id: The NodeID to fetch metadata from
            repository: 'prod' or 'staging'
            source_url: Optional URL - if not provided, uses ccm:wwwurl from metadata
            extraction_method: 'simple' or 'browser' for URL extraction

        Returns:
            InputData with text and existing metadata

        Raises:
            PublicUrlError: the caller's source_url is not public (a stored
                ccm:wwwurl that is not public falls back to the node instead)
        """
        # Step 1: Fetch metadata
        node_data = await self.fetch_node_metadata(node_id, repository)
        properties = node_data.get("properties", {})
        existing_metadata = self._convert_node_properties_to_metadata(
            properties, context, version
        )

        # Step 2: Get URL from metadata if not provided
        caller_url = bool(source_url)
        if not source_url:
            wwwurl = properties.get("ccm:wwwurl", [])
            if wwwurl:
                source_url = wwwurl[0] if isinstance(wwwurl, list) else wwwurl
                logger.info(
                    f"Using ccm:wwwurl from metadata: {loggable_url(source_url)}"
                )

        # Step 3: Check for stored fulltext in repository
        stored_text = await self.fetch_node_text_content(node_id, repository)
        warnings = _stored_text_warnings(stored_text)

        if stored_text and stored_text.strip():
            # Step 4: Fulltext exists - use it (no crawler needed)
            logger.info(
                f"Using stored fulltext from repository ({len(stored_text)} chars)"
            )
            text = stored_text
        else:
            # Step 5: No fulltext - use crawler as fallback
            if not source_url:
                raise NodeWithoutText(
                    f"No stored fulltext and no URL available for node {node_id}"
                )
            logger.info(
                f"No stored fulltext, fetching from URL: {loggable_url(source_url)}"
            )
            try:
                text = await self.fetch_from_url(
                    source_url,
                    extraction_method,
                    lang=lang,
                    output_format=output_format,
                )
            except Exception as e:
                # A non-public URL the caller sent is the caller's error (400), as
                # with input_source=url. One stored on the node (ccm:wwwurl) is
                # not: it falls back like any other failed fetch (review #12).
                if caller_url and isinstance(e, PublicUrlError):
                    raise
                if isinstance(e, TextExtractionError) and e.code in SETUP_ERROR_CODES:
                    raise
                text, warning = _text_from_metadata(e, existing_metadata, node_id)
                warnings.append(warning)

        return InputData(
            text=text,
            existing_metadata=existing_metadata,
            source_url=source_url,
            node_id=node_id,
            repository=repository,
            warnings=warnings,
        )


_input_source_service: Optional[InputSourceService] = None


def get_input_source_service() -> InputSourceService:
    """Get singleton instance of input source service."""
    global _input_source_service
    if _input_source_service is None:
        _input_source_service = InputSourceService()
    return _input_source_service
