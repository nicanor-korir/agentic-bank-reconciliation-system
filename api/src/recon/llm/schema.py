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
                "Exactly one id for 'match'. One combination id for "
                "'split_match'. Empty for 'no_match'. For "
                "'insufficient_evidence', optionally the ids that could not be "
                "separated -- these are shown to the reviewer as the candidates "
                "in contention and are never committed."
            ),
        },
        "confidence": {
            "type": "number",
            # No `minimum`/`maximum`: strict tool use rejects numeric bounds
            # outright ("For 'number' type, properties maximum, minimum are not
            # supported"). The range is enforced in `validate` instead, which
            # is the better home anyway -- the schema guarantees shape, this
            # module guarantees meaning.
            "description": (
                "Probability, between 0 and 1, that a careful bookkeeper shown the "
                "rationale would agree. At or above 0.90 the match is committed "
                "with no human review."
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
    # `insufficient_evidence` MAY name candidates: they are the ones in
    # contention, and telling the reviewer which two could not be separated is
    # more useful than telling them nothing. This is safe because
    # `insufficient_evidence` is not committable, so nothing acts on them.
    # Rejecting it was my contract being wrong, not the model.
    #
    # `no_match` may not: naming a candidate while asserting nothing matches is
    # a contradiction, and one that would reach a reviewer as a recommendation.
    if decision == "no_match" and ids:
        raise InvalidAdjudicationError(
            "'no_match' must not name candidates; naming one while asserting "
            "nothing matches is a contradiction"
        )

    if not str(payload["rationale"]).strip():
        raise InvalidAdjudicationError("rationale is empty; #3 requires an explanation")

    try:
        confidence = Decimal(str(payload["confidence"]))
    except (ArithmeticError, ValueError) as exc:
        raise InvalidAdjudicationError(
            f"confidence {payload['confidence']!r} is not a number"
        ) from exc
    if not Decimal("0") <= confidence <= Decimal("1"):
        # A confidence outside [0,1] compared against the auto-commit threshold
        # would silently decide the wrong way, so it is rejected rather than
        # clamped.
        raise InvalidAdjudicationError(f"confidence {confidence} is outside [0, 1]")

    # Confidence arrives as a JSON number. It becomes Decimal here, once, before
    # anything compares it to a threshold or writes it to numeric(4,3).
    payload["confidence_decimal"] = Decimal(str(payload["confidence"])).quantize(Decimal("0.001"))
    return payload
