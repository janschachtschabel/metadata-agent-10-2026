"""The prompts: system prompts, the per-field extraction prompt and the normalization prompt."""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from .llm_transport import CACHE_BREAKPOINT


# How many concepts of a vocabulary the extraction prompt lists. It was 20:
# subjects have 69 (Physik is the 46th), learning resource types 220, so a model
# that keeps to the list could not pick the right one. 300 covers every
# vocabulary except the two occupation lists (894), which stay truncated.
_VOCABULARY_PROMPT_LIMIT = 300


# The current value of a field in its prompt, bounded like the values of the
# first round in a correction (input_resolver.MAX_PREVIOUS_VALUE_LENGTH): a
# caller's existing_metadata went into the prompt in full, megabytes per field
# call (audit 14.11., S-35).
MAX_CURRENT_VALUE_LENGTH = 2_000

# Heads the correction section of a re-extraction (input_resolver.with_correction).
CORRECTION_HEADER = "--- KORREKTUR / UPDATE ---"

# Set by /generate for a re-extraction with a correction instruction. Read by
# the field prompt instead of looking for the header in the text: a fetched
# page may contain any line (audit L-13). Field tasks inherit it (asyncio
# copies the context into each task).
_CORRECTION_ROUND: ContextVar[bool] = ContextVar("correction_round", default=False)


@contextmanager
def correction_round(active: bool) -> Iterator[None]:
    """Mark the field prompts built inside as part of a correction round."""
    token = _CORRECTION_ROUND.set(active)
    try:
        yield
    finally:
        _CORRECTION_ROUND.reset(token)


class PromptMixin:
    """
    Building the messages sent to the model (part of LLMService).

    Needs: nothing from LLMService.__init__; LLMService overrides
    _marks_cache_breakpoint.
    """

    def _marks_cache_breakpoint(self) -> bool:
        """Whether the model takes a cache breakpoint; LLMService decides."""
        return False

    def _text_messages(self, text: str, language: str) -> list[dict[str, Any]]:
        """
        System prompt and page text: the prefix every field call shares.

        Field calls and the cache primer build it here, so it is byte-identical
        in all of them — the cache only hits on an exact match. Where the model
        takes it, the text ends with an explicit cache breakpoint: the primer
        writes the prefix up to there, the field calls look it up there and
        write nothing of their own (llm_transport.CACHE_BREAKPOINT).
        """
        content: Any = f"--- TEXT ---\n{text}\n--- ENDE ---"
        if self._marks_cache_breakpoint():
            content = [
                {
                    "type": "text",
                    "text": content,
                    "prompt_cache_breakpoint": CACHE_BREAKPOINT,
                }
            ]
        return [
            {"role": "system", "content": self._get_system_prompt(language)},
            {"role": "user", "content": content},
        ]

    def _get_system_prompt(self, language: str) -> str:
        """Get the system prompt for extraction."""
        if language == "de":
            return """Du bist ein Experte für Metadaten-Extraktion im Bildungsbereich.
Deine Aufgabe ist es, strukturierte Metadaten aus Texten zu extrahieren und zu generieren.

WICHTIGE REGELN:
1. Für TITEL und BESCHREIBUNG: Generiere passende Texte basierend auf dem Inhalt, auch wenn kein expliziter Titel vorhanden ist
2. Für andere Felder: Extrahiere nur Informationen, die im Text enthalten sind
3. Erfinde KEINE faktischen Informationen (Daten, Orte, Namen) - aber generiere passende Titel/Beschreibungen
4. Bei Unsicherheit für nicht-generierbare Felder: Wert auf null setzen
5. Antworte IMMER im JSON-Format mit dem Schlüssel "value"
6. Bei Vokabular-Feldern: Verwende EXAKT die vorgegebenen URIs
7. Bei Mehrfachwerten: Gib ein Array zurück
8. Bei Datumsangaben: ISO 8601 Format (YYYY-MM-DD)"""
        else:
            return """You are an expert in metadata extraction for educational content.
Your task is to extract and generate structured metadata from texts.

IMPORTANT RULES:
1. For TITLE and DESCRIPTION: Generate suitable texts based on the content, even if no explicit title is present
2. For other fields: Extract only information present in the text
3. Do NOT invent factual information (dates, places, names) - but generate suitable titles/descriptions
4. If uncertain for non-generatable fields: set value to null
5. ALWAYS respond in JSON format with the key "value"
6. For vocabulary fields: Use EXACTLY the provided URIs
7. For multiple values: Return an array
8. For dates: Use ISO 8601 format (YYYY-MM-DD)"""

    def _build_extraction_prompt(
        self,
        field: dict[str, Any],
        text: str,
        existing_value: Any = None,
        language: str = "de",
        retry_count: int = 0,
        last_attempt: bool = False,
    ) -> str:
        """
        The prompt for one field: retry hint, task, field description, vocabulary,
        examples, the current value and the answer format.

        last_attempt: no retry follows - the hint says so and allows null.

        The page text is not part of it: it travels as a message of its own in
        front of it (_text_messages), so it can be cached.
        """
        de = language == "de"
        label = self._get_localized(field.get("label", {}), language)
        system = field.get("system", {})

        parts = _retry_lines(retry_count, de, last_attempt)
        parts += _task_lines(field.get("id", ""), label, de)
        parts += self._field_lines(field, system, language)
        parts += self._vocabulary_lines(
            system.get("vocabulary", {}), language, retry_count
        )
        examples = field.get("examples", {})
        parts += _example_lines(examples.get(language, examples.get("de", [])), de)
        if existing_value is not None:
            parts += _existing_value_lines(existing_value, label, de)
        parts += _answer_lines(system.get("required", False), de)
        return "\n".join(parts)

    def _field_lines(
        self, field: dict[str, Any], system: dict, language: str
    ) -> list[str]:
        """Description, hint, datatype, multiplicity and object structure."""
        de = language == "de"
        description = self._get_localized(field.get("description", {}), language)
        prompt_hint = self._get_localized(field.get("prompt", {}), language)
        lines = []
        if description:
            lines.append(
                f"Beschreibung: {description}" if de else f"Description: {description}"
            )
        if prompt_hint:
            lines.append(f"Hinweis: {prompt_hint}" if de else f"Hint: {prompt_hint}")
        datatype = system.get("datatype", "string")
        lines.append(f"Datentyp: {datatype}" if de else f"Datatype: {datatype}")
        if system.get("multiple", False):
            lines.append(
                "Mehrfachwerte erlaubt (Array)"
                if de
                else "Multiple values allowed (Array)"
            )
        items = system.get("items", {})
        if items and items.get("datatype") == "object":
            structure_info = self._build_structure_info(items, language)
            if structure_info:
                lines.append(structure_info)
        return lines

    def _vocabulary_lines(
        self, vocabulary: dict, language: str, retry_count: int
    ) -> list[str]:
        """The allowed concepts (label: URI), at most _VOCABULARY_PROMPT_LIMIT of them."""
        concepts = vocabulary.get("concepts", []) if vocabulary else []
        if not concepts:
            return []
        de = language == "de"
        vocab_type = vocabulary.get("type", "open")
        if de:
            lines = [
                f"\nVokabular ({vocab_type}):",
                "⚠️ WICHTIG: Verwende NUR die exakten URIs aus dieser Liste!",
            ]
            if retry_count > 0:
                lines.append("❌ Erfinde KEINE eigenen URIs oder Labels!")
        else:
            lines = [
                f"\nVocabulary ({vocab_type}):",
                "⚠️ IMPORTANT: Use ONLY the exact URIs from this list!",
            ]
            if retry_count > 0:
                lines.append("❌ Do NOT invent your own URIs or labels!")

        for concept in concepts[:_VOCABULARY_PROMPT_LIMIT]:
            uri = concept.get("uri", concept.get("value", ""))
            concept_label = self._get_localized(concept.get("label", {}), language)
            lines.append(f"  - {concept_label}: {uri}")
        if len(concepts) > _VOCABULARY_PROMPT_LIMIT:
            hidden = len(concepts) - _VOCABULARY_PROMPT_LIMIT
            lines.append(
                f"  ... und {hidden} weitere" if de else f"  ... and {hidden} more"
            )
        return lines

    def _get_localized(self, obj: dict[str, str], language: str) -> str:
        """Get localized string with fallback."""
        if isinstance(obj, str):
            return obj
        return obj.get(language, obj.get("de", obj.get("en", "")))

    def _build_structure_info(self, items: dict[str, Any], language: str) -> str:
        """
        Build structure information for complex object fields.

        Generates prompt text describing the expected object structure
        including nested fields from ALL variants with their descriptions and prompts.
        """
        parts = []

        if language == "de":
            parts.append(
                "\n📋 OBJEKTSTRUKTUR - Gib ein Array von Objekten mit folgender Struktur zurück:"
            )
        else:
            parts.append(
                "\n📋 OBJECT STRUCTURE - Return an array of objects with the following structure:"
            )

        # Get discriminator field name (e.g., "@type")
        discriminator = items.get("discriminator", "")

        # Get variants (different object types)
        variants = items.get("variants", [])

        for vi, variant in enumerate(variants):
            self._append_variant_info(
                parts, variant, vi, len(variants), discriminator, language
            )

        # Build a dynamic example from first variant
        if variants:
            example = self._build_dynamic_example(variants, discriminator)
            if example:
                if language == "de":
                    parts.append("\nBeispiel-Ausgabe:")
                else:
                    parts.append("\nExample output:")
                parts.append(
                    f"```json\n{json.dumps({'value': [example]}, indent=2, ensure_ascii=False)}\n```"
                )

        return "\n".join(parts)

    def _append_variant_info(
        self,
        parts: list[str],
        variant: dict[str, Any],
        vi: int,
        variant_count: int,
        discriminator: str,
        language: str,
    ) -> None:
        """Heading and fields of the variant at position `vi` of `variant_count`."""
        variant_type = variant.get("@type", "Object")
        variant_label = self._get_localized(variant.get("label", {}), language)
        variant_desc = self._get_localized(variant.get("description", {}), language)

        # Skip empty variants (like PostalAddress with no fields)
        variant_fields = variant.get("fields", [])
        if not variant_fields and variant_type != "Default":
            return

        if variant_count > 1 and variant_type != "Default":
            if language == "de":
                parts.append(
                    f"\n--- Variante {vi + 1}: {variant_label} ({discriminator}: {variant_type}) ---"
                )
            else:
                parts.append(
                    f"\n--- Variant {vi + 1}: {variant_label} ({discriminator}: {variant_type}) ---"
                )
            if variant_desc:
                parts.append(f"  {variant_desc}")
        elif variant_type != "Default":
            if language == "de":
                parts.append(
                    f"\nTyp: {variant_label} ({discriminator}: {variant_type})"
                )
            else:
                parts.append(
                    f"\nType: {variant_label} ({discriminator}: {variant_type})"
                )

        if language == "de":
            parts.append("Felder:")
        else:
            parts.append("Fields:")

        # Add fields from variant with descriptions and prompts
        for field in variant_fields:
            self._append_field_info(parts, field, language, indent=1)

    def _append_field_info(
        self,
        parts: list[str],
        field: dict[str, Any],
        language: str,
        indent: int = 1,
    ) -> None:
        """Append field info including description and prompt to parts list."""
        prefix = "  " * indent
        field_id = field.get("id", "")
        field_label = self._get_localized(field.get("label", {}), language)
        field_desc = self._get_localized(field.get("description", {}), language)
        field_prompt = self._get_localized(field.get("prompt", {}), language)
        field_system = field.get("system", {})
        field_datatype = field_system.get("datatype", "string")

        # Check for nested fields (e.g., address with streetAddress, postalCode)
        nested_fields = field.get("fields", [])
        if nested_fields:
            desc_suffix = f" — {field_desc}" if field_desc else ""
            parts.append(f"{prefix}- {field_id}: ({field_label}, Objekt){desc_suffix}")
            if field_prompt:
                parts.append(f"{prefix}  💡 {field_prompt}")
            for nested in nested_fields:
                self._append_field_info(parts, nested, language, indent=indent + 1)
        else:
            desc_suffix = f" — {field_desc}" if field_desc else ""
            parts.append(
                f"{prefix}- {field_id}: {field_label} ({field_datatype}){desc_suffix}"
            )
            if field_prompt:
                parts.append(f"{prefix}  💡 {field_prompt}")

    def _build_dynamic_example(
        self,
        variants: list[dict[str, Any]],
        discriminator: str,
    ) -> Optional[dict[str, Any]]:
        """Build a dynamic example object from the first non-empty variant."""
        for variant in variants:
            variant_type = variant.get("@type", "")
            variant_fields = variant.get("fields", [])
            if not variant_fields:
                continue

            example = {}
            if variant_type and variant_type != "Default" and discriminator:
                example[discriminator] = variant_type

            for field in variant_fields:
                field_id = field.get("id", "")
                nested = field.get("fields", [])
                if nested:
                    sub_example = {}
                    for nf in nested:
                        sub_example[nf.get("id", "")] = "..."
                    example[field_id] = sub_example
                else:
                    example[field_id] = "..."

            return example

        return None


# ------------------------------------------------- sections of the field prompt

_RETRY_HINTS: dict[bool, dict[Any, Any]] = {
    True: {
        "head": "⚠️ VERSUCH {n} - Bitte genauer prüfen!",
        1: [
            "Der vorherige Versuch lieferte kein gültiges Ergebnis.",
            "Analysiere den Text genauer und suche nach impliziten Informationen.",
        ],
        2: [
            "LETZTER VERSUCH - Suche auch nach indirekten Hinweisen.",
            "Falls keine Information findbar: null zurückgeben.",
        ],
    },
    False: {
        "head": "⚠️ ATTEMPT {n} - Please check more carefully!",
        1: [
            "The previous attempt did not yield a valid result.",
            "Analyze the text more carefully and look for implicit information.",
        ],
        2: [
            "LAST ATTEMPT - Also look for indirect hints.",
            "If no information can be found: return null.",
        ],
    },
}


def _retry_lines(retry_count: int, de: bool, last_attempt: bool) -> list[str]:
    """After a failed attempt: look closer; on the last one, null is an answer."""
    if retry_count <= 0:
        return []
    hints = _RETRY_HINTS[de]
    return [
        hints["head"].format(n=retry_count + 1),
        *hints[2 if last_attempt else 1],
        "",
    ]


# Fields the model writes from the text rather than finds in it.
_GENERATIVE_FIELDS = ("cclom:title", "cclom:general_description")


def _task_lines(field_id: str, label: str, de: bool) -> list[str]:
    if field_id in _GENERATIVE_FIELDS:
        if de:
            return [
                f"🎯 GENERIERE das Feld '{label}' (ID: {field_id}) basierend auf dem Text oben:",
                "HINWEIS: Erstelle einen passenden, aussagekräftigen Wert basierend auf dem Textinhalt!",
            ]
        return [
            f"🎯 GENERATE the field '{label}' (ID: {field_id}) based on the text above:",
            "NOTE: Create a suitable, meaningful value based on the text content!",
        ]
    if de:
        return [f"Extrahiere das Feld '{label}' (ID: {field_id}) aus dem Text oben:"]
    return [f"Extract the field '{label}' (ID: {field_id}) from the text above:"]


def _example_lines(example_list: list, de: bool) -> list[str]:
    if not example_list:
        return []
    return [
        "\nBeispiele:" if de else "\nExamples:",
        *(f"  - {ex}" for ex in example_list[:3]),
    ]


def _current_value(existing_value: Any) -> str:
    """
    The current value as JSON: of a text its first MAX_CURRENT_VALUE_LENGTH
    characters, of anything else that many characters of its JSON form; a cut
    value ends in " …".
    """
    if isinstance(existing_value, str):
        if len(existing_value) <= MAX_CURRENT_VALUE_LENGTH:
            return json.dumps(existing_value, ensure_ascii=False)
        # Cut before the dump (it would escape megabytes to keep 2,000
        # characters); the closing quote goes, the value is not complete.
        kept = existing_value[:MAX_CURRENT_VALUE_LENGTH]
        return json.dumps(kept, ensure_ascii=False)[:-1] + " …"
    current = json.dumps(existing_value, ensure_ascii=False)
    if len(current) > MAX_CURRENT_VALUE_LENGTH:
        current = current[:MAX_CURRENT_VALUE_LENGTH] + " …"
    return current


def _existing_value_lines(existing_value: Any, label: str, de: bool) -> list[str]:
    """
    The current value and how to treat it: in a correction round only the
    field the instruction names may change; otherwise update on new information.
    """
    current = _current_value(existing_value)
    # Only a round /generate marks as correction (correction_round): a page
    # that happens to contain the header is no correction (audit L-13).
    if _CORRECTION_ROUND.get():
        if de:
            return [
                f"\n🔒 AKTUELLER WERT: {current}",
                "⚠️ KORREKTUR-MODUS — WICHTIGE REGELN:",
                "1. Der Text enthält einen KORREKTUR-Abschnitt mit einer gezielten Änderungsanweisung.",
                f"2. Prüfe: Bezieht sich die Korrekturanweisung EXPLIZIT auf das Feld '{label}'?",
                "3. WENN JA → Führe NUR die angeforderte Änderung durch (z.B. kürzen, ergänzen, korrigieren)",
                "4. WENN NEIN → Gib den AKTUELLEN WERT EXAKT und UNVERÄNDERT zurück!",
                "5. Ändere NIEMALS Felder, die nicht in der Korrekturanweisung genannt werden.",
            ]
        return [
            f"\n🔒 CURRENT VALUE: {current}",
            "⚠️ CORRECTION MODE — IMPORTANT RULES:",
            "1. The text contains a CORRECTION section with a targeted change instruction.",
            f"2. Check: Does the correction instruction EXPLICITLY refer to the field '{label}'?",
            "3. IF YES → Apply ONLY the requested change (e.g. shorten, add, correct)",
            "4. IF NO → Return the CURRENT VALUE EXACTLY and UNCHANGED!",
            "5. NEVER modify fields that are not mentioned in the correction instruction.",
        ]
    if de:
        return [
            f"\n📋 AKTUELLER WERT: {current}",
            "AKTUALISIERUNGS-REGELN:",
            "1. Wenn der Text NEUE oder GEÄNDERTE Informationen für dieses Feld enthält → Aktualisiere den Wert",
            "2. Wenn der Text KEINE Informationen zu diesem Feld enthält → Behalte den aktuellen Wert",
            "3. Prüfe genau auf: Datumsänderungen, Ortsänderungen, Namensänderungen etc.",
        ]
    return [
        f"\n📋 CURRENT VALUE: {current}",
        "UPDATE RULES:",
        "1. If the text contains NEW or CHANGED information for this field → Update the value",
        "2. If the text contains NO information about this field → Keep the current value",
        "3. Check carefully for: date changes, location changes, name changes etc.",
    ]


def _answer_lines(required: bool, de: bool) -> list[str]:
    if de:
        lines = ['\nAntworte mit JSON: {"value": <extrahierter_wert>}']
        if not required:
            lines.append('Wenn keine Information gefunden: {"value": null}')
        return lines
    lines = ['\nRespond with JSON: {"value": <extracted_value>}']
    if not required:
        lines.append('If no information found: {"value": null}')
    return lines
