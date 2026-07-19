"""Token counting and cost estimation for LLM API usage."""

from __future__ import annotations

from dataclasses import dataclass, field


# Pricing per million tokens (USD, as of 2025).
# Cache pricing: cache_read ≈ 10% of input, cache_creation ≈ 125% of input.
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-20250414": {"input": 0.80, "output": 4.0},
    # Aliases for convenience
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "claude-opus-4": {"input": 15.0, "output": 75.0},
    "claude-haiku-4": {"input": 0.80, "output": 4.0},
}

# Cache multipliers relative to input price
_CACHE_READ_RATIO = 0.10
_CACHE_CREATION_RATIO = 1.25


@dataclass
class _UsageAccumulator:
    """Internal mutable token counters."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    requests: int = 0


class CostTracker:
    """Track token usage and estimate API costs.

    Accumulates input/output/cache token counts across multiple API calls
    and computes an estimated USD cost based on published per-model pricing.

    Example::

        tracker = CostTracker(model="claude-sonnet-4-20250514")
        tracker.record_usage(input_tokens=1500, output_tokens=800)
        print(tracker.get_summary())
    """

    def __init__(self, model: str = "") -> None:
        """Initialise the tracker.

        Args:
            model: Model identifier used for pricing lookup.
        """
        self.model = model
        self._usage = _UsageAccumulator()

    # -- recording ---------------------------------------------------------

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> None:
        """Record tokens consumed by a single API call.

        Args:
            input_tokens: Prompt tokens (excluding cache).
            output_tokens: Completion tokens.
            cache_read: Tokens served from prompt cache.
            cache_creation: Tokens written to prompt cache.
        """
        self._usage.input_tokens += input_tokens
        self._usage.output_tokens += output_tokens
        self._usage.cache_read_tokens += cache_read
        self._usage.cache_creation_tokens += cache_creation
        self._usage.requests += 1

    # -- queries -----------------------------------------------------------

    def get_total_tokens(self) -> dict[str, int]:
        """Return a breakdown of all accumulated token counts.

        Returns:
            Dict with keys: input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, total_tokens, requests.
        """
        u = self._usage
        return {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_creation_tokens": u.cache_creation_tokens,
            "total_tokens": u.input_tokens + u.output_tokens,
            "requests": u.requests,
        }

    def get_estimated_cost(self) -> float:
        """Estimate total cost in USD based on accumulated usage.

        Returns:
            Estimated cost in US dollars. Returns 0.0 if model pricing
            is unknown.
        """
        pricing = _PRICING.get(self.model)
        if pricing is None:
            # Try partial match
            for key, val in _PRICING.items():
                if key in self.model or self.model in key:
                    pricing = val
                    break
        if pricing is None:
            return 0.0

        input_price = pricing["input"] / 1_000_000
        output_price = pricing["output"] / 1_000_000
        cache_read_price = input_price * _CACHE_READ_RATIO
        cache_creation_price = input_price * _CACHE_CREATION_RATIO

        u = self._usage
        cost = (
            u.input_tokens * input_price
            + u.output_tokens * output_price
            + u.cache_read_tokens * cache_read_price
            + u.cache_creation_tokens * cache_creation_price
        )
        return round(cost, 6)

    def get_summary(self) -> str:
        """Return a human-readable usage summary.

        Returns:
            Multi-line string with token counts and estimated cost.
        """
        tokens = self.get_total_tokens()
        cost = self.get_estimated_cost()
        model_label = self.model or "unknown model"

        lines = [
            f"Model: {model_label}",
            f"Requests: {tokens['requests']}",
            f"Input tokens: {tokens['input_tokens']:,}",
            f"Output tokens: {tokens['output_tokens']:,}",
        ]
        if tokens["cache_read_tokens"]:
            lines.append(f"Cache read: {tokens['cache_read_tokens']:,}")
        if tokens["cache_creation_tokens"]:
            lines.append(f"Cache creation: {tokens['cache_creation_tokens']:,}")
        lines.append(f"Total tokens: {tokens['total_tokens']:,}")
        lines.append(f"Estimated cost: ${cost:.4f} USD")
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all accumulated counters to zero."""
        self._usage = _UsageAccumulator()

    def set_model(self, model: str) -> None:
        """Change the model used for cost estimation.

        Does not reset accumulated counters.

        Args:
            model: New model identifier.
        """
        self.model = model
