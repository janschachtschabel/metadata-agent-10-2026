"""Application configuration."""

from pydantic_settings import BaseSettings
from pydantic import Field, ValidationInfo, field_validator, model_validator
from typing import Any, Literal, Optional
from urllib.parse import urlsplit, urlunsplit


# Default throughput per provider. Each provider has its own budget.
#
# b-api-academiccloud (GWDG) has two measured limits, both per B-API key, not
# per process:
# - two requests in flight (2026-08-12, staging, deepseek-v4-flash). A third
#   parallel request is refused with 429 at once, without `retry-after`, so a
#   client has to stay under the limit; at 3 req/s the effective throughput
#   fell below what 2 req/s delivered.
# - about 10 calls per minute, ~200 per hour (2026-10-15, router with Qwen
#   models). For a whole /generate (~50 calls) this is the tighter one.
# The defaults below only encode the first: they are per process and cannot
# know the replica count. Lower both ENV overrides for the per-minute limit and
# divide by the number of instances (Helm: sharedLlmLimit, values.yaml).
#
# b-api-openai: a real /generate run on 2026-10-06 with gpt-6-luna and 20
# requests in flight finished without a 429. On 2026-11-15 the B-API answered
# 60 and 100 calls at the same moment with 200 as well (60 also through the
# router), and 60 workers took 4.6 s where 20 take 5.2 s. The default stays at
# 20: half a second is not worth tripling the load on a shared key before it
# is tried in production. The August measurement had found one budget per API
# key for both b-api paths — if the gateway answers 429, lower
# METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS. That override applies to every
# provider: a value above 2 also lifts AcademicCloud past its limit.
#
# Native OpenAI grants per-account limits and its 429 says how long to wait, so
# no client-side rate cap is imposed by default.
#
# b-api-router: the agent cannot see which models a route holds. The default
# matches its default model (openai/gpt-6-luna); a route of AcademicCloud models
# needs the lower AcademicCloud values via the two ENV overrides.
# Providers the service can talk to. Anything else used to fall through to
# native OpenAI — with the server's key and the caller's text.
LLMProvider = Literal["b-api-openai", "b-api-academiccloud", "b-api-router", "openai"]

PROVIDER_LIMITS: dict[str, dict[str, Any]] = {
    "b-api-openai": {"max_concurrent_requests": 20, "requests_per_second": 0.0},
    "b-api-academiccloud": {"max_concurrent_requests": 2, "requests_per_second": 2.0},
    "b-api-router": {"max_concurrent_requests": 20, "requests_per_second": 0.0},
    "openai": {"max_concurrent_requests": 20, "requests_per_second": 0.0},
}

# Parallel extraction workers: the range a request may ask for, and the cap
# for the ENV default.
MAX_WORKERS = 20

# Proxy timeout of the ingress and termination grace period of the Helm chart:
# generate_timeout (300 s) + 10 s for the answer and preStop, plus a buffer.
INGRESS_TIMEOUT_SECONDS = 320

# The geocoder itself (Komoot). A deployment with a B-API calls it through
# the B-API's proxy path instead (Settings.geocoder_api_url).
PHOTON_URL = "https://photon.komoot.io/api"

# Without a B-API the text extraction follows the repository's environment.
TEXT_EXTRACTION_URLS = {
    "Staging": "https://text-extraction.staging.openeduhub.net",
    "Produktion": "https://text-extraction.prod.openeduhub.net",
}


# Settings an operator empties on purpose: "do not send the parameter"
# (llm_transport.py) and "no origin may call" (main.py).
EMPTY_IS_A_VALUE = frozenset({"llm_verbosity", "llm_reasoning_effort", "cors_origins"})


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _normalized_url(url: str) -> str:
    """
    A URL setting as httpx sends it: scheme and host in lower case, the
    scheme's default port dropped, no blanks around it, no trailing slash.

    The settings are compared with the addresses of outgoing calls
    (metrics.upstream_service, llm_auth.b_api_headers_for): with
    "https://B-API.example:443" every call was counted as "other" and the
    upstream alerts never fired (audit 14.11., O-36). Anything that is no
    absolute URL is returned as it is.
    """
    url = url.strip().rstrip("/")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url
    host = parts.hostname
    if not parts.scheme or not host:
        return url
    scheme = parts.scheme.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"
    userinfo, at, _ = parts.netloc.rpartition("@")
    return urlunsplit(
        (scheme, f"{userinfo}{at}{host}", parts.path, parts.query, parts.fragment)
    )


def _without_credentials(url: str) -> str:
    """A URL setting for a log line: user and password in it are not printed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


def _environment_of(url: str) -> Optional[str]:
    """'Staging' or 'Produktion' for the known OpenEduHub hosts, else None."""
    host = urlsplit(url).hostname or ""
    # Only OpenEduHub's own hosts: "staging" in someone else's host name says
    # nothing about which of its services belong together (audit 13.11., L-23).
    if host != "openeduhub.net" and not host.endswith(".openeduhub.net"):
        return None
    if "staging" in host:
        return "Staging"
    if ".prod." in f".{host}" or host == "redaktion.openeduhub.net":
        return "Produktion"
    return None


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # API Settings
    app_name: str = "Metadata Agent API"
    app_version: str = "4.0.1"

    # Logs go to stdout. text: the message alone, as the agent always wrote
    # them; json: one object per line with timestamp, level, service, logger,
    # message and the ids of the request (trace_id, span_id, request_id).
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    # The `service` field of every JSON log line: the name operations finds
    # the service under (chart, release, dashboards). The proxy entry of the
    # B-API is called metadata-agent-canvas - edu-sharing is wired to that.
    service_name: str = "metadata-agent"

    # Second, internal port for /health, /ready and /metrics (infra.py), not to
    # be exposed by the ingress. Unset: no second server and no /metrics.
    internal_port: Optional[int] = Field(default=None, ge=1, le=65535)
    # Address the internal port listens on: "::" for an IPv6 (dual-stack)
    # cluster, where probes reach the pod over IPv6.
    internal_host: str = "0.0.0.0"

    # Largest request body accepted (bytes); larger ones get 413 before they are
    # read. Room for a /upload with a preview image as data URL.
    max_request_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)

    # LLM Provider Selection
    # Options: 'b-api-openai', 'b-api-academiccloud', 'b-api-router', 'openai'
    llm_provider: LLMProvider = "b-api-openai"

    # OpenAI Configuration (native OpenAI API)
    #
    # openai_api_base points at any OpenAI-compatible endpoint, not only
    # api.openai.com — an Azure deployment, a self-hosted vLLM or a gateway all
    # work, as long as they serve /chat/completions and take a Bearer token.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_api_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # Unset means: follow llm_temperature, the same setting the b-api providers
    # read. Having exactly one of three providers ignore the shared temperature
    # is a trap; this keeps the separate knob for anyone who already sets it.
    openai_temperature: Optional[float] = Field(default=None, ge=0, le=2)

    # B-API Configuration (shared key for both b-api providers)
    b_api_key: str = Field(default="", alias="B_API_KEY")

    # Which key B-API calls carry (see src/services/llm_auth.py):
    #   auto     the caller's key forwarded by the B-API proxy
    #            (Authorization: Bearer, forwardApiKey) if present, else B_API_KEY
    #   forward  only the forwarded key; a request without it fails (LLM_AUTH_MISSING)
    #   static   only B_API_KEY (X-API-KEY), an incoming header is ignored
    b_api_auth_mode: Literal["static", "forward", "auto"] = "auto"

    # GET /debug/auth (does a forwarded key arrive - masked: presence, scheme,
    # length) and GET /debug/dependencies (are B-API, text extraction and
    # repository reachable, is the key taken). Off by default; switch on only
    # while setting up or debugging a deployment.
    debug_endpoints: bool = False

    # Models a request may name in llm_model, comma-separated; empty: any (as in
    # 2.1.1). Wherever no caller key is forwarded, that model runs on the
    # server's key (audit 18.10. final, S-19). The provider's configured default
    # model is always allowed.
    allowed_llm_models: str = ""

    # B-API Base URL (Staging/Prod — endpoint paths are derived automatically)
    # Staging: https://b-api.staging.openeduhub.net
    # Prod:    https://b-api.prod.openeduhub.net
    b_api_base_url: str = "https://b-api.staging.openeduhub.net"

    # B-API OpenAI (OpenAI-compatible endpoint via B-API)
    # Derived from b_api_base_url if left empty
    b_api_openai_base: str = ""
    b_api_openai_model: str = "gpt-6-luna"

    # B-API AcademicCloud (AcademicCloud endpoint via B-API)
    # Derived from b_api_base_url if left empty.
    #
    # 'deepseek-v4-flash' beat 'openai-gpt-oss-120b' on both axes over four runs
    # of the same extraction (2026-08-12): 46s against 68s, and 37 of 50 fields
    # against 30 — including the one expected field the other missed. Both got
    # the exactly checkable values right.
    #
    # An earlier default 'deepseek-r1' answered 404 Model Not Found. A default
    # that cannot work is worse than none, because nothing in the failure names
    # the setting.
    b_api_academiccloud_base: str = ""
    b_api_academiccloud_model: str = "deepseek-v4-flash"

    # B-API router (llm_provider=b-api-router): the model field names a route, or
    # provider/model, which the router resolves without any route. The default
    # therefore works before a route exists; set the route name once it does.
    # Derived from b_api_base_url if left empty.
    b_api_router_base: str = ""
    b_api_router_model: str = "openai/gpt-6-luna"

    # Which request body the router calls carry (b-api-router only; the other
    # providers always decide by model name). The router forwards it unchanged
    # to every model of a route, and the families disagree: GPT-5/6 and o-models
    # want max_completion_tokens and no temperature, all others max_tokens.
    #   auto       decide by model name (gpt-5…, gpt-6…, o1/o3/o4, also behind
    #              "openai/"); a bare route name counts as classic
    #   reasoning  max_completion_tokens, verbosity, reasoning_effort
    #   classic    max_tokens, temperature
    llm_request_style: Literal["auto", "reasoning", "classic"] = "auto"

    # reasoning_effort for the router's classic body (b-api-router only).
    # Thinking models such as qwen3.6-35b-a3b need "none"; without it they
    # return no text. Empty: not sent - older classic models (gpt-4o) reject it.
    b_api_router_reasoning_effort: Literal["", "none", "low", "medium", "high"] = ""

    # The B-API answers word-identical requests from its cache, unmarked. True
    # appends ?ignore-caching=true to every B-API LLM call.
    b_api_ignore_cache: bool = False

    # Whether the page text ends with an explicit cache breakpoint (OpenAI
    # prompt cache; PROMPT_CACHE_PROVIDERS and the router's "openai/…" models).
    # With it the field calls only read the text from the cache; without it
    # each one also writes its own field instruction, at 1.25 times the input
    # price, for nobody to read.
    #   auto  for gpt-6… models; others answer 400 "prompt_cache_options is
    #         not supported on this model" (measured 2026-11-15: gpt-4.1-mini,
    #         gpt-5-mini, gpt-5.4-mini)
    #   on    for every model of those providers - a family the rule above
    #         does not know yet
    #   off   never: the request body of before
    # Its price: the same text extracted again within the lifetime of the
    # cache costs as much as the first time (measured 0.0065 $ instead of
    # 0.0018 $) - the field instructions are no longer in the cache.
    llm_cache_breakpoint: Literal["auto", "on", "off"] = "auto"

    # General LLM Settings. Bounded here: a value the API refuses would fail
    # every call instead of the start (audit 14.11., O-39).
    llm_temperature: float = Field(default=0.3, ge=0, le=2)
    llm_max_tokens: int = Field(default=2000, ge=1)

    # Only sent to reasoning models (GPT-5/GPT-6 families, o-series); older models
    # reject both. Empty means: do not send the parameter at all.
    #
    # 'low' rather than 'none': 'none' is roughly 40% faster and costs half the
    # output tokens, but in measurements it dropped the event date in 2 of 5
    # runs; 'low' found the expected fields every time. A missing event date is
    # not worth three seconds. 'minimal' is rejected by the API. Other values
    # are refused at the start: sent on, they fail every call of a reasoning
    # model (audit 14.11., O-39).
    llm_verbosity: Literal["", "low", "medium", "high"] = "low"
    llm_reasoning_effort: Literal["", "none", "low", "medium", "high"] = "low"
    # Attempts of one LLM call (1 = no repetition) - and how many extra calls an
    # invalid field value may cost. 0 would leave a call no attempt at all.
    llm_max_retries: int = Field(default=3, ge=1)
    llm_retry_delay: float = Field(default=1.0, ge=0)

    # How hard the gateway may be pushed. Unset means: use the default
    # for the provider (see PROVIDER_LIMITS). Both are enforced across the whole
    # process, not per request — the gateway counts them that way too.
    #   llm_max_concurrent_requests: requests allowed in flight at once
    #   llm_max_requests_per_second: 0 switches the rate cap off entirely;
    #   a negative rate did the same without saying so (audit 14.11., O-39)
    llm_max_concurrent_requests: Optional[int] = Field(default=None, ge=1)
    llm_max_requests_per_second: Optional[float] = Field(default=None, ge=0)

    # Worker Settings
    # Fan-out width of the parallel field extraction, used by /generate when a
    # request sets no max_workers (clamped to 1-MAX_WORKERS, see below). It is
    # capped by llm_max_concurrent_requests — asking for more workers than the
    # gateway allows in flight only queues them up.
    default_max_workers: int = MAX_WORKERS
    # /generate runs a pod takes on at once; one more is answered 503 with
    # Retry-After instead of sharing the LLM gate until all of them reach the
    # deadline together. Unset: no limit - the right number comes from a load
    # test (audit 13.11., P-12).
    max_concurrent_generations: Optional[int] = Field(default=None, ge=1)
    # Read timeout of one LLM call. 90 s leaves a buffer below the 300 s a
    # whole /generate may take; long pages need more than 60 s.
    request_timeout: int = Field(default=90, ge=1)

    # Upper bound for a whole /generate run (all field calls, retries,
    # geocoding), in seconds - and for /detect-content-type and /extract-field.
    # Below the 320 s of the ingress, so the client gets the agent's 504 with
    # request id instead of a bare one from the proxy, and the field calls stop
    # instead of running on for nobody. 300 s since 19.10.: the Goethe article
    # (412k characters, 56 LLM calls of 34 s on average) took 126 s. A B-API
    # proxy or edu-sharing in front needs a timeout of at least ~310 s.
    generate_timeout: float = Field(default=300, gt=0)

    # Upper bound for /upload and /nodes (screenshot, every write request; the
    # read-back of a finished node runs after it with its own 10 s,
    # READ_BACK_SECONDS, on a request that needs no cleanup), in seconds. Lower
    # than generate_timeout: when the limit hits, a node this call created is
    # looked up (if its id never arrived, up to 5 s) and discarded (up to 5 s);
    # deadline + both + preStop (5 s) must fit into the 320 s of ingress and
    # grace period.
    upload_timeout: float = Field(default=100, gt=0)

    # Seconds the platform gives the process after SIGTERM, when it differs
    # from the 320 s the documents assume (INGRESS_TIMEOUT_SECONDS). The Helm
    # chart sets it from terminationGracePeriodSeconds; the start warnings
    # compare the two deadlines above with it. It changes nothing else.
    grace_period_seconds: Optional[int] = Field(default=None, ge=1)

    # Repository URL (for NodeID input source and upload)
    # Staging: https://repository.staging.openeduhub.net/edu-sharing/rest
    # Prod:    https://redaktion.openeduhub.net/edu-sharing/rest
    repository_url: str = "https://repository.staging.openeduhub.net/edu-sharing/rest"

    # How the agent reaches text extraction and geocoder. Every service exists
    # once per environment; through the B-API's own paths (/api/v1/proxy/...)
    # they are always the ones that belong to that B-API, and the calls carry
    # the same key as the LLM calls.
    #   auto    through the B-API if the LLM provider is a B-API one, else direct
    #   proxy   always through the B-API
    #   direct  never: text extraction by the repository's environment, Photon
    b_api_services: Literal["auto", "proxy", "direct"] = "auto"

    # Text extraction (for URL input). Empty: chosen as b_api_services says.
    # Set it to name one yourself, e.g. the service inside the cluster.
    text_extraction_api_url: str = ""

    # WLO Repository Upload Credentials (for /upload endpoint)
    wlo_guest_username: str = Field(default="", alias="WLO_GUEST_USERNAME")
    wlo_guest_password: str = Field(default="", alias="WLO_GUEST_PASSWORD")

    # Whether /ready requires WLO_GUEST_USERNAME/PASSWORD. false: a deployment
    # that only generates (as 2.1.1 could) gets traffic without them; /upload
    # and /nodes then answer 503 and the start log warns.
    require_repository: bool = True

    # WLO Repository Inbox ID (where new upload nodes are created)
    wlo_inbox_id: str = "21144164-30c0-4c01-ae16-264452197063"

    # Screenshot Settings. Always captured locally, with the method a request
    # names. Bounds as for a request (models/schemas.py, ScreenshotRequest).
    screenshot_width: int = Field(default=1920, ge=320, le=3840)
    screenshot_height: int = Field(default=1080, ge=200, le=2160)
    screenshot_format: str = "png"
    screenshot_full_page: bool = False
    screenshot_delay: int = Field(default=2000, ge=0, le=10000)  # ms before capture

    # CORS Settings
    cors_origins: str = "*"  # Comma-separated origins, or '*' for all

    @field_validator("*", mode="before")
    @classmethod
    def empty_means_default(cls, v: Any, info: ValidationInfo) -> Any:
        """
        An empty environment variable means 'not set': the default applies.

        `METADATA_AGENT_LOG_FORMAT=` in a .env file or `""` in the chart values
        would otherwise fail validation and take the whole process down at
        import - or, for a number, be read as a value nobody chose. Not where
        empty is itself the setting (EMPTY_IS_A_VALUE).
        """
        if (
            isinstance(v, str)
            and not v.strip()
            and info.field_name
            and info.field_name not in EMPTY_IS_A_VALUE
        ):
            return cls.model_fields[info.field_name].default
        return v

    @field_validator(
        "b_api_auth_mode",
        "b_api_services",
        "llm_provider",
        "llm_request_style",
        "log_format",
        "b_api_router_reasoning_effort",
        "llm_verbosity",
        "llm_reasoning_effort",
        "llm_cache_breakpoint",
        mode="before",
    )
    @classmethod
    def lower_case_choice(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("log_level", mode="before")
    @classmethod
    def upper_case_level(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("allowed_llm_models")
    @classmethod
    def names_at_least_one_model(cls, v: str) -> str:
        """
        ' , ' names no model and was read as 'any model' - the opposite of what
        whoever set it meant (audit 14.11., O-39).
        """
        if v.strip() and not any(name.strip() for name in v.split(",")):
            raise ValueError(
                "METADATA_AGENT_ALLOWED_LLM_MODELS nennt kein Modell; leer lassen, "
                "um jedes Modell zu erlauben."
            )
        return v

    @field_validator("default_max_workers")
    @classmethod
    def clamp_to_the_request_range(cls, v: int) -> int:
        """
        Keep the worker default inside 1-MAX_WORKERS, the range requests accept.

        Outside it, every /generate request without max_workers would fail
        validation with 422; clamped here, the startup log shows what applies.
        """
        return min(max(v, 1), MAX_WORKERS)

    @model_validator(mode="after")
    def normalize_and_derive(self) -> "Settings":
        """Bring the URL settings into one form and derive B-API endpoints."""
        self.b_api_base_url = _normalized_url(self.b_api_base_url)
        self.text_extraction_api_url = _normalized_url(self.text_extraction_api_url)
        self.repository_url = _normalized_url(self.repository_url)
        self.openai_api_base = _normalized_url(self.openai_api_base)

        # Derive B-API endpoint paths from base URL
        if not self.b_api_openai_base:
            self.b_api_openai_base = f"{self.b_api_base_url}/api/v1/llm/openai"
        if not self.b_api_academiccloud_base:
            self.b_api_academiccloud_base = (
                f"{self.b_api_base_url}/api/v1/llm/academiccloud"
            )
        if not self.b_api_router_base:
            self.b_api_router_base = f"{self.b_api_base_url}/api/v1/llm/router"
        # Also when set explicitly: "/chat/completions" is appended to them.
        self.b_api_openai_base = _normalized_url(self.b_api_openai_base)
        self.b_api_academiccloud_base = _normalized_url(self.b_api_academiccloud_base)
        self.b_api_router_base = _normalized_url(self.b_api_router_base)

        if not self.text_extraction_api_url:
            self.text_extraction_api_url = (
                f"{self.b_api_base_url}/api/v1/proxy/text-extraction"
                if self.services_via_b_api
                else TEXT_EXTRACTION_URLS.get(
                    _environment_of(self.repository_url) or "", ""
                )
            )
        return self

    @property
    def _llm_calls_go_to_the_b_api(self) -> bool:
        """The LLM calls of the configured provider go to b_api_base_url."""
        if not self.llm_provider.startswith("b-api"):
            return False
        base = self.get_llm_config()["api_base"]
        return base == self.b_api_base_url or base.startswith(f"{self.b_api_base_url}/")

    @property
    def services_via_b_api(self) -> bool:
        """
        Text extraction and geocoder are reached through the B-API's paths.

        auto: only through the B-API the LLM calls go to. With an LLM base
        address of its own (b_api_openai_base ...) the key would otherwise go
        to a second B-API, the one b_api_base_url names (audit 13.11., S-24).
        """
        if self.b_api_services == "auto":
            return self._llm_calls_go_to_the_b_api
        return self.b_api_services == "proxy"

    @property
    def geocoder_api_url(self) -> str:
        if self.services_via_b_api:
            return f"{self.b_api_base_url}/api/v1/proxy/photon_geocoding/api"
        return PHOTON_URL

    model_config = {
        "env_prefix": "METADATA_AGENT_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,  # Allow both alias and field name
        "extra": "ignore",
    }

    def get_llm_config(
        self,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> dict:
        """
        Get LLM configuration based on selected provider.

        Args:
            provider_override: Override the default provider from .env
            model_override: Override the default model for the provider
        """
        provider = provider_override or self.llm_provider

        config: dict[str, Any]
        if provider == "b-api-openai":
            config = {
                "provider": "b-api-openai",
                "api_key": self.b_api_key,
                "api_base": self.b_api_openai_base,
                "model": model_override or self.b_api_openai_model,
                "temperature": self.llm_temperature,
                "requires_custom_header": True,  # X-API-KEY instead of Bearer
            }
        elif provider == "b-api-academiccloud":
            config = {
                "provider": "b-api-academiccloud",
                "api_key": self.b_api_key,
                "api_base": self.b_api_academiccloud_base,
                "model": model_override or self.b_api_academiccloud_model,
                "temperature": self.llm_temperature,
                "requires_custom_header": True,
            }
        elif provider == "b-api-router":
            config = {
                "provider": "b-api-router",
                "api_key": self.b_api_key,
                "api_base": self.b_api_router_base,
                "model": model_override or self.b_api_router_model,
                "temperature": self.llm_temperature,
                "requires_custom_header": True,
            }
        elif provider == "openai":
            config = {
                "provider": "openai",
                "api_key": self.openai_api_key,
                "api_base": self.openai_api_base,
                "model": model_override or self.openai_model,
                "temperature": (
                    self.llm_temperature
                    if self.openai_temperature is None
                    else self.openai_temperature
                ),
                "requires_custom_header": False,
            }
        else:
            raise ValueError(f"Unbekannter LLM-Provider: {provider!r}")

        config.update(self.get_throughput_limits(config["provider"]))
        return config

    def allowed_llm_model_names(self) -> set[str]:
        """METADATA_AGENT_ALLOWED_LLM_MODELS as a set; empty means: no restriction."""
        return {
            name.strip() for name in self.allowed_llm_models.split(",") if name.strip()
        }

    def config_warnings(self) -> list[str]:
        """
        Settings that cannot work together, printed at startup - never a secret.

        The URLs default to staging; a production deployment that forgets one of
        them silently mixes environments. That is what the address check catches.
        """
        warnings = []
        if self.llm_provider.startswith("b-api"):
            warnings.extend(self._key_warnings_with_a_b_api_provider())
        else:
            warnings.extend(self._key_warnings_without_a_b_api_provider())

        if self.debug_endpoints:
            warnings.append(
                "METADATA_AGENT_DEBUG_ENDPOINTS=true: /debug/auth und "
                "/debug/dependencies antworten jedem, der den Dienst erreicht. "
                "Nur zum Einrichten oder zur Fehlersuche; danach ausschalten."
            )

        if (
            self.llm_provider == "b-api-router"
            and "/" not in self.b_api_router_model
            and self.llm_request_style == "auto"
        ):
            warnings.append(
                f"Route '{self.b_api_router_model}': Der Name verrät nicht, welche "
                "Parameter ihre Modelle verstehen. METADATA_AGENT_LLM_REQUEST_STYLE "
                "auf reasoning (GPT-5/6) oder classic (alle anderen) setzen – sonst "
                "wird classic gesendet."
            )

        missing = [
            name
            for name, value in (
                ("WLO_GUEST_USERNAME", self.wlo_guest_username),
                ("WLO_GUEST_PASSWORD", self.wlo_guest_password),
            )
            if not value
        ]
        if missing:
            warnings.append(
                f"{' und '.join(missing)} {'fehlt' if len(missing) == 1 else 'fehlen'}: "
                + (
                    "/ready meldet 503, /upload und /nodes antworten mit 503."
                    if self.require_repository
                    else "/upload und /nodes antworten mit 503 (/ready bleibt bereit: "
                    "METADATA_AGENT_REQUIRE_REPOSITORY=false)."
                )
            )

        warnings.extend(self._deadline_warnings())
        warnings.extend(self._address_warnings())
        return warnings

    def _key_warnings_with_a_b_api_provider(self) -> list[str]:
        """The B-API key against the auth mode of the LLM calls."""
        warnings = []
        if self.b_api_auth_mode == "static" and not self.b_api_key:
            warnings.append(
                "B_API_KEY fehlt: im Modus static scheitert jeder KI-Aufruf."
            )
        elif self.b_api_auth_mode == "forward" and self.b_api_key:
            warnings.append(
                "B_API_KEY ist gesetzt, wird im Modus forward aber nie verwendet."
            )
        elif self.b_api_auth_mode == "auto" and not self.b_api_key:
            warnings.append(
                "Ohne B_API_KEY gelingen im Modus auto nur Aufrufe über den "
                "B-API-Proxy mit durchgereichtem Schlüssel."
            )
        return warnings

    def _deadline_warnings(self) -> list[str]:
        """The deadlines of /upload and /generate against ingress and grace period."""
        warnings = []
        # Deadline + what runs after it must fit the 320 s of ingress and grace
        # period: /upload looks up and discards its node (5 s each), both wait
        # for preStop (5 s); /generate needs its answer and preStop. Where the
        # grace period is configured (the chart does), that one counts: with
        # 320 s fixed, a raised period warned for nothing and a lowered one
        # not at all (audit 14.11.).
        limit = self.grace_period_seconds or INGRESS_TIMEOUT_SECONDS
        for name, deadline, after in (
            ("METADATA_AGENT_UPLOAD_TIMEOUT", self.upload_timeout, 15),
            ("METADATA_AGENT_GENERATE_TIMEOUT", self.generate_timeout, 10),
        ):
            if deadline + after > limit:
                warnings.append(
                    f"{name}={deadline:g}: mit {after} s danach (Aufräumen, preStop) "
                    f"über den {limit} s "
                    + (
                        "der Grace-Period (METADATA_AGENT_GRACE_PERIOD_SECONDS) – "
                        "beim Beenden des Pods wird die Anfrage abgebrochen."
                        if self.grace_period_seconds
                        else "von Ingress und Grace-Period – der Proxy bricht "
                        "vorher ab."
                    )
                )
        return warnings

    def _address_warnings(self) -> list[str]:
        """Addresses that do not fit together: LLM, text extraction, environments."""
        warnings = []
        if (
            self.llm_provider.startswith("b-api")
            and not self._llm_calls_go_to_the_b_api
            and self.b_api_services != "direct"
        ):
            llm_base = _without_credentials(self.get_llm_config()["api_base"])
            warnings.append(
                f"Die LLM-Adresse ({llm_base}) liegt nicht unter "
                "METADATA_AGENT_B_API_BASE_URL "
                f"({_without_credentials(self.b_api_base_url)}). "
                + (
                    "Textextraktion und Geocoder gehen trotzdem dorthin – mit dem "
                    "Schlüssel der LLM-Aufrufe (METADATA_AGENT_B_API_SERVICES=proxy)."
                    if self.b_api_services == "proxy"
                    else "Textextraktion und Geocoder werden deshalb direkt "
                    "aufgerufen, nicht über die B-API; dafür "
                    "METADATA_AGENT_B_API_BASE_URL auf dieselbe B-API setzen."
                )
            )

        if not self.text_extraction_api_url:
            warnings.append(
                "Keine Textextraktion: Zur Repository-Adresse ist keine bekannt "
                "(weder Staging noch Produktion). Erschließen aus einer URL "
                "scheitert, bis METADATA_AGENT_TEXT_EXTRACTION_API_URL gesetzt ist."
            )

        environments: dict[str, list[str]] = {}
        for name, url in (
            ("B-API", self.b_api_base_url),
            ("Repository", self.repository_url),
            ("Textextraktion", self.text_extraction_api_url),
        ):
            env = _environment_of(url)
            if env:
                environments.setdefault(env, []).append(name)
        if len(environments) > 1:
            warnings.append(
                "Die Adressen zeigen auf verschiedene Umgebungen – "
                + "; ".join(
                    f"{env}: {', '.join(names)}" for env, names in environments.items()
                )
            )
        return warnings

    def _key_warnings_without_a_b_api_provider(self) -> list[str]:
        """
        Provider openai: its own key, and the B-API key where text extraction
        and geocoder still go through the B-API (audit 14.11., O-38).
        """
        warnings = []
        if not self.openai_api_key:
            warnings.append(
                "OPENAI_API_KEY fehlt: mit dem Provider openai scheitert jeder "
                "KI-Aufruf, /ready meldet 503."
            )
        if self.services_via_b_api and not self.b_api_key:
            if self.b_api_auth_mode == "static":
                warnings.append(
                    "B_API_KEY fehlt: Textextraktion und Geocoder laufen über die "
                    "B-API (METADATA_AGENT_B_API_SERVICES) und scheitern im Modus "
                    "static ohne ihn."
                )
            elif self.b_api_auth_mode == "auto":
                warnings.append(
                    "Ohne B_API_KEY gelingen Textextraktion und Geocoder über die "
                    "B-API (METADATA_AGENT_B_API_SERVICES) nur bei Aufrufen mit "
                    "durchgereichtem Schlüssel."
                )
        return warnings

    def loggable_urls(self) -> dict[str, str]:
        """
        The addresses the start log names, without user and password: a URL
        setting may carry them (https://user:secret@host), a log must not.
        """
        return {
            "LLM API Base": _without_credentials(self.get_llm_config()["api_base"]),
            "B-API Base URL": _without_credentials(self.b_api_base_url),
            "Text Extraction URL": _without_credentials(self.text_extraction_api_url),
            "Geocoder URL": _without_credentials(self.geocoder_api_url),
            "Repository URL": _without_credentials(self.repository_url),
        }

    def get_throughput_limits(self, provider: str) -> dict:
        """
        How many requests this provider's gateway may be given, and how fast.

        The configured values win over the provider defaults; a rate of 0 means
        no cap and is deliberately distinguished from 'unset'.
        """
        # Each provider draws from its own budget.
        group = provider if provider in PROVIDER_LIMITS else "openai"
        defaults = PROVIDER_LIMITS[group]

        concurrent = self.llm_max_concurrent_requests
        per_second = self.llm_max_requests_per_second

        return {
            "limit_group": group,
            "max_concurrent_requests": max(
                1,
                defaults["max_concurrent_requests"]
                if concurrent is None
                else concurrent,
            ),
            "requests_per_second": float(
                defaults["requests_per_second"] if per_second is None else per_second
            ),
        }


# The settings of the process: one object for the app, the routers
# (deps.settings is this object) and the services. Tests put another one in
# force here and nowhere else (tests/conftest.py use_settings; audit 14.11.,
# Q-09).
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """The settings of the process: read on first use, the same object for all."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
