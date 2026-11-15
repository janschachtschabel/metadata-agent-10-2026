"""Detecting which content type (schema) a text belongs to."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from .llm_errors import (
    LLMError,
)

logger = logging.getLogger(__name__)


@dataclass
class ContentTypeDetection:
    """
    What the LLM said about the content type.

    schema_file is None when the answer was unusable (no JSON, or a value
    outside the offered content types); `problem` then says why. The caller
    decides about a fallback - and has to label it as one.
    """

    schema_file: Optional[str]
    problem: Optional[str] = None


def _named_type(result: Any) -> Optional[str]:
    """The content type a parsed answer names, or None when it names none."""
    # The system prompt (shared with field extraction) asks for "value";
    # some models follow it instead of the "content_type" asked for here.
    answer = (
        (result.get("content_type") or result.get("value"))
        if isinstance(result, dict)
        else None
    )
    # One type wrapped in a list still names one type; anything else that
    # is not a plain string is no answer (and must not raise).
    if isinstance(answer, list) and len(answer) == 1:
        answer = answer[0]
    if not isinstance(answer, str):
        answer = None
    return answer


class ContentTypeMixin:
    """
    Content type detection by the model (part of LLMService).

    Needs LLMTransportMixin, PromptMixin and ValueNormalizerMixin; the methods
    it uses from them are declared below for mypy.
    """

    if TYPE_CHECKING:

        async def _call_llm(
            self,
            messages: list[dict[str, str]],
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
        ) -> dict[str, Any]: ...
        def _get_system_prompt(self, language: str) -> str: ...
        def _get_localized(self, obj: dict[str, str], language: str) -> str: ...
        def _parse_json_response(self, content: str) -> Optional[dict[str, Any]]: ...

    async def detect_content_type(
        self,
        text: str,
        content_types: list[dict[str, Any]],
        language: str = "de",
        prompt_hint: Optional[str] = None,
    ) -> ContentTypeDetection:
        """
        Ask the LLM which of the offered content types the text is.

        Never invents a result: an unusable answer comes back with schema_file
        None and the reason, so the caller can fall back openly.

        Raises:
            LLMError: a fatal call failure (refused key, quota, no gateway).
        """
        if not content_types:
            return ContentTypeDetection(None, "keine Inhaltsarten zur Auswahl")
        allowed = {
            ct.get("schema_file") for ct in content_types if ct.get("schema_file")
        }

        prompt = self._detection_prompt(text, content_types, language, prompt_hint)

        try:
            response = await self._call_llm(
                messages=[
                    {"role": "system", "content": self._get_system_prompt(language)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=100,
            )
            choice = response.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            # Cut off at the token limit: said in the reason, as extract_field
            # does - it was "no usable answer" (audit 14.11., L-N).
            truncated = choice.get("finish_reason") == "length"
            result = self._parse_json_response(content)
        except LLMError as e:
            if e.fatal:
                raise
            return ContentTypeDetection(None, f"KI-Dienst nicht nutzbar: {e.message}")
        except Exception as e:
            # Not a model answer but a fault in the agent: the keyword fallback
            # still runs, the traceback goes to the log.
            logger.exception("Content-type detection failed")
            return ContentTypeDetection(
                None, f"Antwort nicht lesbar ({type(e).__name__})"
            )

        answer = _named_type(result)
        if answer in allowed:
            return ContentTypeDetection(answer)
        if truncated:
            return ContentTypeDetection(
                None,
                "die Antwort des KI-Diensts wurde an der Tokengrenze abgeschnitten "
                "(finish_reason=length)",
            )
        if answer:
            return ContentTypeDetection(None, f"unbekannte Inhaltsart '{answer}'")
        return ContentTypeDetection(None, "keine auswertbare Antwort")

    def _detection_prompt(
        self,
        text: str,
        content_types: list[dict[str, Any]],
        language: str,
        prompt_hint: Optional[str],
    ) -> str:
        """The question: the start of the text and the content types on offer."""
        # Truncate text for content type detection — only first ~5000 chars needed
        detection_text = text[:5000] if len(text) > 5000 else text

        # Build options list (Label → schema_file)
        options = []
        for ct in content_types:
            label = self._get_localized(ct.get("label", {}), language)
            schema_file = ct.get("schema_file", "")
            options.append(f"- {label} → {schema_file}")

        options_block = chr(10).join(options)

        # Include classification hints from schema prompt if available
        hint_block = ""
        if prompt_hint:
            hint_block = f"\n{prompt_hint}\n"

        if language == "de":
            prompt = f"""Analysiere folgenden Text und bestimme den passenden Inhaltstyp.

WICHTIG: Bewerte die DARGESTELLTE RESSOURCE auf der Seite, NICHT den Betreiber der Website.
Beispiel: Eine Veranstaltungsseite auf weimar.de ist eine Veranstaltung, NICHT eine Organisation.
Frage dich: Was wird dem Besucher auf dieser konkreten Seite präsentiert?
{hint_block}
Erlaubte Werte (wähle GENAU EINEN der folgenden schema_file-Werte):
{options_block}

--- TEXT ---
{detection_text}
--- ENDE ---

Antworte mit JSON: {{"content_type": "<schema_file>"}}"""
        else:
            prompt = f"""Analyze the following text and determine the appropriate content type.

IMPORTANT: Evaluate the RESOURCE PRESENTED on the page, NOT the website operator.
Example: An event page on a city website is an Event, NOT an Organization.
Ask yourself: What is being presented to the visitor on this specific page?
{hint_block}
Allowed values (choose EXACTLY ONE of the following schema_file values):
{options_block}

--- TEXT ---
{detection_text}
--- END ---

Respond with JSON: {{"content_type": "<schema_file>"}}"""
        return prompt
