"""Application configuration."""

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, model_validator
from functools import lru_cache
from typing import Any, Literal, Optional
from urllib.parse import urlsplit


# Default throughput per provider. Each provider has its own budget.
#
# b-api-academiccloud (measured 2026-08-12, staging): two requests in flight,
# about two per second. A third parallel request is refused with 429 at once,
# without `retry-after`, so a client has to stay under the limit; at 3 req/s
# the effective throughput falls below what 2 req/s delivers.
#
# b-api-openai: a real /generate run on 2026-10-06 with gpt-6-luna and 20
# requests in flight finished without a 429. The August measurement had found
# one budget per API key for both b-api paths — if the gateway answers 429,
# lower METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS. That override applies to
# every provider: a value above 2 also lifts AcademicCloud past its limit.
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

# provider name -> which budget it draws from
LIMIT_GROUPS = {
    "b-api-openai": "b-api-openai",
    "b-api-academiccloud": "b-api-academiccloud",
    "b-api-router": "b-api-router",
    "openai": "openai",
}


def _environment_of(url: str) -> Optional[str]:
    """'Staging' or 'Produktion' for the known OpenEduHub hosts, else None."""
    host = urlsplit(url).hostname or ""
    if "staging" in host:
        return "Staging"
    if ".prod." in f".{host}" or host == "redaktion.openeduhub.net":
        return "Produktion"
    return None


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # API Settings
    app_name: str = "Metadata Agent API"
    app_version: str = "2.1.0"

    # Logs go to stdout. text: the message alone, as the agent always wrote
    # them; json: one object per line with level, logger, time and trace id.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    # Second, internal port for /health, /ready and /metrics (infra.py), not to
    # be exposed by the ingress. Unset: no second server and no /metrics.
    internal_port: Optional[int] = Field(default=None, ge=1, le=65535)

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
    openai_temperature: Optional[float] = None

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

    # General LLM Settings
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2000

    # Only sent to reasoning models (GPT-5/GPT-6 families, o-series); older models
    # reject both. Empty means: do not send the parameter at all.
    #
    # Measured against gpt-5.6-luna on 2026-08-11, five runs per level on event
    # texts. 'none' is roughly 40% faster (6.9-8.9s vs 9.6-15.3s) and costs half
    # the output tokens — but dropped ccm:oeh_event_begin in 2 of 5 runs and
    # once destabilised the content-type detection, while 'low' hit 8/9 expected
    # fields every time. A missing event date is not worth three seconds.
    # 'minimal' is rejected by the API; valid: none, low, medium, high.
    llm_verbosity: str = "low"
    llm_reasoning_effort: str = "low"
    llm_max_retries: int = 3
    llm_retry_delay: float = 1.0

    # How hard the gateway may be pushed. Unset means: use the default
    # for the provider (see PROVIDER_LIMITS). Both are enforced across the whole
    # process, not per request — the gateway counts them that way too.
    #   llm_max_concurrent_requests: requests allowed in flight at once
    #   llm_max_requests_per_second: 0 switches the rate cap off entirely
    llm_max_concurrent_requests: Optional[int] = None
    llm_max_requests_per_second: Optional[float] = None

    # Worker Settings
    # Fan-out width of the parallel field extraction, used by /generate when a
    # request sets no max_workers (clamped to 1-MAX_WORKERS, see below). It is
    # capped by llm_max_concurrent_requests — asking for more workers than the
    # gateway allows in flight only queues them up.
    default_max_workers: int = MAX_WORKERS
    # Read timeout of one LLM call. 90 s leaves a buffer below the 120 s the
    # ingress allows for a whole /generate; long pages need more than 60 s.
    request_timeout: int = 90

    # Default Schema Settings
    default_context: str = "default"

    # Repository URL (for NodeID input source and upload)
    # Staging: https://repository.staging.openeduhub.net/edu-sharing/rest
    # Prod:    https://redaktion.openeduhub.net/edu-sharing/rest
    repository_url: str = "https://repository.staging.openeduhub.net/edu-sharing/rest"

    # Text Extraction API Settings (for URL input source)
    # Staging: https://text-extraction.staging.openeduhub.net
    # Prod:    https://text-extraction.prod.openeduhub.net
    text_extraction_api_url: str = "https://text-extraction.staging.openeduhub.net"

    # WLO Repository Upload Credentials (for /upload endpoint)
    wlo_guest_username: str = Field(default="", alias="WLO_GUEST_USERNAME")
    wlo_guest_password: str = Field(default="", alias="WLO_GUEST_PASSWORD")

    # WLO Repository Inbox ID (where new upload nodes are created)
    wlo_inbox_id: str = "21144164-30c0-4c01-ae16-264452197063"

    # Screenshot Settings
    # Always captured locally; 'pageshot' is accepted as a legacy alias.
    screenshot_method: str = "playwright"
    screenshot_width: int = 1920
    screenshot_height: int = 1080
    screenshot_format: str = "png"
    screenshot_full_page: bool = False
    screenshot_delay: int = 2000  # ms to wait before capture

    # CORS Settings
    cors_origins: str = "*"  # Comma-separated origins, or '*' for all

    @field_validator(
        "openai_temperature",
        "llm_max_concurrent_requests",
        "llm_max_requests_per_second",
        mode="before",
    )
    @classmethod
    def empty_means_unset(cls, v: Any) -> Any:
        """
        An empty environment variable means 'not configured', not '0'.

        `METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND=` in a .env file would
        otherwise fail validation and take the whole process down at import.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("b_api_auth_mode", mode="before")
    @classmethod
    def normalize_auth_mode(cls, v: Any) -> Any:
        """'FORWARD', ' static ' and an empty value all mean what they say."""
        if isinstance(v, str):
            return v.strip().lower() or "auto"
        return v

    @field_validator(
        "llm_provider",
        "llm_request_style",
        "log_format",
        "b_api_router_reasoning_effort",
        mode="before",
    )
    @classmethod
    def lower_case_choice(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("log_level", mode="before")
    @classmethod
    def upper_case_level(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

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
        """Strip trailing slashes from URLs and derive B-API endpoints."""
        # Strip trailing slashes from all URL settings
        self.b_api_base_url = self.b_api_base_url.rstrip("/")
        self.text_extraction_api_url = self.text_extraction_api_url.rstrip("/")
        self.repository_url = self.repository_url.rstrip("/")
        self.openai_api_base = self.openai_api_base.rstrip("/")

        # Derive B-API endpoint paths from base URL
        if not self.b_api_openai_base:
            self.b_api_openai_base = f"{self.b_api_base_url}/api/v1/llm/openai"
        if not self.b_api_academiccloud_base:
            self.b_api_academiccloud_base = (
                f"{self.b_api_base_url}/api/v1/llm/academiccloud"
            )
        if not self.b_api_router_base:
            self.b_api_router_base = f"{self.b_api_base_url}/api/v1/llm/router"
        self.b_api_router_base = self.b_api_router_base.rstrip("/")
        return self

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

    def config_warnings(self) -> list[str]:
        """
        Settings that cannot work together, printed at startup - never a secret.

        The URLs default to staging; a production deployment that forgets one of
        them silently mixes environments. That is what the address check catches.
        """
        warnings = []
        if self.llm_provider.startswith("b-api"):
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

    def get_throughput_limits(self, provider: str) -> dict:
        """
        How many requests this provider's gateway may be given, and how fast.

        The configured values win over the provider defaults; a rate of 0 means
        no cap and is deliberately distinguished from 'unset'.
        """
        group = LIMIT_GROUPS.get(provider, "openai")
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


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
