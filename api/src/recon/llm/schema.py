"""Structured output contract for adjudication.

`strict: true` on the tool guarantees the response validates against this
schema, so nothing downstream has to defend against a malformed decision. The
enum is closed deliberately -- an unrecognised decision string reaching the
commit path would be a silent, unauditable outcome.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

DECISIONS = ("match", "no_match", "split_match", "insufficient_evidence")
COMMITTABLE = frozenset({"match", "split_match"})

ADJUDICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "candidate_ids", "confidence", "rationale", "evidence"],
    "properties": {
        "decision": {
            "type": "string",
            "enum": list(DECISIONS),
            "description": (
                "insufficient_evidence is a first-class answer. Use it whenever "
                "two candidates are equally consistent with the payment."
            ),
        },
        "candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Candidate ids supporting the decision. Exactly one for 'match'; "
                "the ids of the chosen combination for 'split_match'; empty for "
                "'no_match' and 'insufficient_evidence'."
            ),
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": (
                "Probability a careful bookkeeper shown the rationale would agree. "
                "At or above 0.90 the match is committed with no human review."
            ),
        },
        "rationale": {
            "type": "string",
            "description": (
                "One sentence a bookkeeper understands, naming the concrete thing "
                "that decided it. Never restate the confidence."
            ),
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Narrative tokens or field values that drove the decision.",
        },
    },
}

TOOL_NAME = "record_adjudication"


def adjudication_tool() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Record the adjudication decision for this bank line.",
        "input_schema": ADJUDICATION_SCHEMA,
        "strict": True,
    }


class InvalidAdjudicationError(ValueError):
    """The model returned something the commit path must not act on."""


def validate(payload: dict[str, Any], candidate_ids: set[str]) -> dict[str, Any]:
    """Check the parts a JSON schema cannot express.

    Schema validation guarantees shape. It cannot guarantee that the model
    named candidates that were actually offered, or that a 'match' names
    exactly one -- and either of those reaching the commit path would produce
    a decision pointing at a ledger item nobody proposed.
    """
    decision = payload["decision"]
    ids = list(payload["candidate_ids"])

    unknown = [i for i in ids if i not in candidate_ids]
    if unknown:
        raise InvalidAdjudicationError(f"named candidates that were not offered: {unknown}")

    if decision == "match" and len(ids) != 1:
        raise InvalidAdjudicationError(f"'match' must name exactly one candidate, got {len(ids)}")
    if decision == "split_match" and len(ids) != 1:
        raise InvalidAdjudicationError(
            f"'split_match' must name exactly one combination candidate, got {len(ids)}"
        )
    if decision in {"no_match", "insufficient_evidence"} and ids:
        raise InvalidAdjudicationError(f"'{decision}' must not name candidates")

    if not str(payload["rationale"]).strip():
        raise InvalidAdjudicationError("rationale is empty; #3 requires an explanation")

    # Confidence arrives as a JSON number. It becomes Decimal here, once, before
    # anything compares it to a threshold or writes it to numeric(4,3).
    payload["confidence_decimal"] = Decimal(str(payload["confidence"])).quantize(Decimal("0.001"))
    return payload
