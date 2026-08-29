"""Cost accounting.

Integer nano-USD throughout, for the same reason money is minor units: a cost
ceiling that halts a run is a decision, and decisions are not made on floats.

Rates are per million tokens, so nano-USD per token is the rate itself times
1,000 -- which happens to make every figure an exact integer.
"""

from __future__ import annotations

from dataclasses import dataclass

# Nano-USD per token. Sonnet 5 is $2/MTok in, $10/MTok out; cache reads are a
# tenth of input and cache writes a quarter more than input.
RATES_NANO: dict[str, dict[str, int]] = {
    "claude-sonnet-5": {"input": 2_000, "output": 10_000, "cache_read": 200, "cache_write": 2_500},
    "claude-opus-5": {"input": 5_000, "output": 25_000, "cache_read": 500, "cache_write": 6_250},
    "claude-haiku-4-5": {"input": 1_000, "output": 5_000, "cache_read": 100, "cache_write": 1_250},
}


class UnknownModelError(KeyError):
    """Refuse to price a model we have no published rate for.

    Guessing would make the cost ceiling and the per-1,000-lines figure in the
    client deck quietly wrong, which is worse than failing to start.
    """


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost_nano(self, model: str) -> int:
        try:
            rate = RATES_NANO[model]
        except KeyError as exc:
            raise UnknownModelError(
                f"no published rate for {model!r}; add it to RATES_NANO rather than "
                f"letting the cost ceiling run on a guess"
            ) from exc
        return (
            self.input_tokens * rate["input"]
            + self.output_tokens * rate["output"]
            + self.cache_read_tokens * rate["cache_read"]
            + self.cache_write_tokens * rate["cache_write"]
        )

    def cost_micro(self, model: str) -> int:
        return self.cost_nano(model) // 1_000


def format_micro(cost_micro: int) -> str:
    return f"${cost_micro / 1_000_000:,.4f}"
