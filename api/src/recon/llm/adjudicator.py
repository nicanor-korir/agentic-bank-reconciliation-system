"""Tier 3 adjudication.

Three implementations behind one protocol, and which one is in use is recorded
on the run:

  * `AnthropicAdjudicator` calls the model and records every request/response
    pair keyed by the hash of the request body.
  * `RecordedAdjudicator` serves those recordings and **fails on a miss**. This
    is the replay path (NOTES.md 0.4a): a miss means the input to the model
    changed, which is the regression worth catching, so it must never fall
    back to calling live.
  * `StubAdjudicator` is not a model. It exists so the graph, checkpointing,
    interrupts, audit chain and cost ceiling can be exercised without an API
    key. Its output must never be reported as model quality, and the run row
    records `adjudicator="stub"` so a report cannot pretend otherwise.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Protocol

from recon.llm.pricing import Usage, format_micro
from recon.llm.request import request_hash
from recon.llm.schema import TOOL_NAME, InvalidAdjudicationError, validate


@dataclass(frozen=True, slots=True)
class Adjudication:
    decision: str
    candidate_ids: tuple[str, ...]
    confidence: Decimal
    rationale: str
    evidence: tuple[str, ...]
    usage: Usage
    cost_micro: int
    latency_ms: int
    request: dict[str, Any]
    request_hash: str
    response: dict[str, Any]
    served_from_recording: bool


class CostCeilingError(RuntimeError):
    """The run spent its budget. The graph halts rather than quietly continuing."""


class ReplayMissError(RuntimeError):
    """A replay found no recording for this exact request.

    Reported as a diff, never repaired by calling the model: the whole claim is
    that replay reproduces the stored run, and a live call would make that claim
    false while appearing to succeed.
    """


class Adjudicator(Protocol):
    name: str

    def adjudicate(self, request: dict[str, Any], candidate_ids: set[str]) -> Adjudication: ...


class CostMeter:
    """Enforces the per-run ceiling before each call, not after."""

    def __init__(self, ceiling_micro: int) -> None:
        self.ceiling_micro = ceiling_micro
        self.spent_micro = 0
        self.calls = 0

    def check(self) -> None:
        if self.spent_micro >= self.ceiling_micro:
            raise CostCeilingError(
                f"run halted after {self.calls} call(s): spent "
                f"{format_micro(self.spent_micro)} of a "
                f"{format_micro(self.ceiling_micro)} ceiling"
            )

    def record(self, cost_micro: int) -> None:
        self.spent_micro += cost_micro
        self.calls += 1


def _from_payload(
    payload: dict[str, Any],
    candidate_ids: set[str],
    request: dict[str, Any],
    response: dict[str, Any],
    usage: Usage,
    latency_ms: int,
    served_from_recording: bool,
) -> Adjudication:
    validated = validate(dict(payload), candidate_ids)
    model = str(request.get("model", ""))
    return Adjudication(
        decision=validated["decision"],
        candidate_ids=tuple(validated["candidate_ids"]),
        confidence=validated["confidence_decimal"],
        rationale=validated["rationale"],
        evidence=tuple(validated["evidence"]),
        usage=usage,
        cost_micro=usage.cost_micro(model) if model else 0,
        latency_ms=latency_ms,
        request=request,
        request_hash=request_hash(request),
        response=response,
        served_from_recording=served_from_recording,
    )


class AnthropicAdjudicator:
    name = "anthropic"

    def __init__(self, client: Any) -> None:
        self._client = client

    def adjudicate(self, request: dict[str, Any], candidate_ids: set[str]) -> Adjudication:
        started = time.perf_counter()
        response = self._client.messages.create(**request)
        latency_ms = int((time.perf_counter() - started) * 1000)

        payload: dict[str, Any] | None = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                payload = dict(block.input)
                break
        if payload is None:
            raise InvalidAdjudicationError(
                f"model returned no {TOOL_NAME} tool call "
                f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
            )

        raw = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=getattr(raw, "input_tokens", 0) or 0,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        )
        return _from_payload(
            payload,
            candidate_ids,
            request,
            {"decision": payload, "usage": asdict(usage)},
            usage,
            latency_ms,
            served_from_recording=False,
        )


class RecordedAdjudicator:
    """Serves stored calls. A miss is a failure, never a live call."""

    name = "recorded"

    def __init__(self, recordings: dict[str, dict[str, Any]]) -> None:
        self._recordings = recordings
        self.misses: list[str] = []

    def adjudicate(self, request: dict[str, Any], candidate_ids: set[str]) -> Adjudication:
        digest = request_hash(request)
        recording = self._recordings.get(digest)
        if recording is None:
            self.misses.append(digest)
            raise ReplayMissError(
                f"no recorded call for request {digest[:12]}; the input to the "
                f"model changed since the recorded run"
            )
        payload = recording["response"]["decision"]
        usage_raw = recording["response"].get("usage", {})
        usage = Usage(**{k: int(v) for k, v in usage_raw.items()})
        return _from_payload(
            payload,
            candidate_ids,
            request,
            recording["response"],
            usage,
            int(recording.get("latency_ms", 0)),
            served_from_recording=True,
        )


class StubAdjudicator:
    """NOT A MODEL. Exercises the machinery without an API key.

    Commits only when a single candidate matches the payment exactly, and
    answers `insufficient_evidence` otherwise -- the safe default, and the same
    thing the prompt asks a real model to do when nothing separates the
    candidates. It cannot demonstrate judgement, and nothing it produces belongs
    in a quality report.
    """

    name = "stub"

    def __init__(self, autocommit_confidence: Decimal) -> None:
        self._confidence = autocommit_confidence

    def adjudicate(self, request: dict[str, Any], candidate_ids: set[str]) -> Adjudication:
        import json

        payload = json.loads(request["messages"][0]["content"])
        candidates = payload["candidates"]
        exact = [c for c in candidates if c["difference_from_payment_minor"] == 0]

        if len(exact) == 1:
            candidate = exact[0]
            decision = "split_match" if candidate["kind"] == "subset" else "match"
            refs = ", ".join(i["doc_ref"] or "?" for i in candidate["items"])
            result = {
                "decision": decision,
                "candidate_ids": [candidate["id"]],
                "confidence": float(self._confidence),
                "rationale": (
                    f"{refs} is the only open item matching this payment exactly "
                    f"on amount and payer."
                ),
                "evidence": [f"amount_minor:{candidate['total_minor']}"],
            }
        else:
            result = {
                "decision": "insufficient_evidence",
                "candidate_ids": [],
                "confidence": 0.3,
                "rationale": (
                    f"{len(exact)} candidates match this payment equally well; "
                    f"nothing in the narrative separates them."
                ),
                "evidence": [],
            }

        usage = Usage()
        return _from_payload(
            result,
            candidate_ids,
            request,
            {"decision": result, "usage": asdict(usage), "stub": True},
            usage,
            0,
            served_from_recording=False,
        )
