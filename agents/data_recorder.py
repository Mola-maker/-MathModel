"""Data recorder — tracks token usage and cost per agent.

Adapted from MathModelAgent reference project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class DataRecorder:
    """Tracks token usage and API cost per agent across the pipeline."""

    def __init__(self, log_dir: str | Path = ""):
        self.total_cost: float = 0.0
        self.token_usage: dict[str, dict[str, Any]] = {}
        self.log_dir = Path(log_dir) if log_dir else None

    # ── Model pricing (per 1M tokens, USD) ──
    MODEL_PRICES: dict[str, dict[str, float]] = {
        "deepseek-chat": {"prompt": 0.27, "completion": 1.10},
        "deepseek-reasoner": {"prompt": 0.55, "completion": 2.19},
        "gpt-4o": {"prompt": 2.50, "completion": 10.00},
        "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
        "claude-sonnet-4-6": {"prompt": 3.00, "completion": 15.00},
        "claude-opus-4-6": {"prompt": 15.00, "completion": 75.00},
        "claude-haiku-4-5": {"prompt": 0.80, "completion": 4.00},
    }

    def record_completion(
        self,
        agent_name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Record a single API call's token usage."""
        if agent_name not in self.token_usage:
            self.token_usage[agent_name] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "chat_count": 0,
                "cost": 0.0,
                "model": model,
            }

        entry = self.token_usage[agent_name]
        entry["prompt_tokens"] += prompt_tokens
        entry["completion_tokens"] += completion_tokens
        entry["total_tokens"] += prompt_tokens + completion_tokens
        entry["chat_count"] += 1
        entry["model"] = model

        cost = self._calculate_cost(model, prompt_tokens, completion_tokens)
        entry["cost"] += cost
        self.total_cost += cost

        if self.log_dir:
            self._save_to_file()

    def record_from_response(self, agent_name: str, response: object) -> None:
        """Record from an OpenAI-compatible response object."""
        usage = getattr(response, "usage", None)
        model = getattr(response, "model", "unknown")
        if usage is None:
            return
        self.record_completion(
            agent_name=agent_name,
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
        )

    def _calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate cost in USD."""
        # Try exact match first, then partial match
        prices = self.MODEL_PRICES.get(model)
        if prices is None:
            for key, val in self.MODEL_PRICES.items():
                if key in model:
                    prices = val
                    break
        if prices is None:
            prices = {"prompt": 0.10, "completion": 0.30}  # conservative default

        prompt_cost = (prompt_tokens / 1_000_000) * prices["prompt"]
        completion_cost = (completion_tokens / 1_000_000) * prices["completion"]
        return prompt_cost + completion_cost

    def print_summary(self) -> None:
        """Print a formatted summary table."""
        if not self.token_usage:
            print("\n[DataRecorder] No API calls recorded.")
            return

        print("\n" + "=" * 80)
        print("  Token Usage & Cost Summary")
        print("=" * 80)
        print(
            f"{'Agent':<20} {'Model':<25} {'Calls':>5} "
            f"{'Prompt':>8} {'Compl':>8} {'Total':>8} {'Cost($)':>8}"
        )
        print("-" * 80)

        total_calls = 0
        total_prompt = 0
        total_compl = 0
        total_tokens = 0

        for agent, usage in self.token_usage.items():
            total_calls += usage["chat_count"]
            total_prompt += usage["prompt_tokens"]
            total_compl += usage["completion_tokens"]
            total_tokens += usage["total_tokens"]

            model_short = usage["model"]
            if len(model_short) > 24:
                model_short = model_short[:21] + "..."

            print(
                f"{agent:<20} {model_short:<25} {usage['chat_count']:>5} "
                f"{usage['prompt_tokens']:>8} {usage['completion_tokens']:>8} "
                f"{usage['total_tokens']:>8} {usage['cost']:>8.4f}"
            )

        print("-" * 80)
        print(
            f"{'TOTAL':<20} {'':<25} {total_calls:>5} "
            f"{total_prompt:>8} {total_compl:>8} "
            f"{total_tokens:>8} {self.total_cost:>8.4f}"
        )
        print("=" * 80)

    def _save_to_file(self) -> None:
        """Save token usage to JSON file."""
        if not self.log_dir:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / "token_usage.json"
        data = {
            "total_cost_usd": round(self.total_cost, 6),
            "agents": self.token_usage,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_summary_dict(self) -> dict:
        """Return summary as a dict for context storage."""
        return {
            "total_cost_usd": round(self.total_cost, 6),
            "total_tokens": sum(u["total_tokens"] for u in self.token_usage.values()),
            "total_calls": sum(u["chat_count"] for u in self.token_usage.values()),
            "per_agent": {
                name: {
                    "tokens": u["total_tokens"],
                    "cost": round(u["cost"], 6),
                    "calls": u["chat_count"],
                }
                for name, u in self.token_usage.items()
            },
        }


# Global singleton
_recorder: DataRecorder | None = None


def get_recorder() -> DataRecorder:
    """Get or create the global DataRecorder."""
    global _recorder
    if _recorder is None:
        _base = Path(__file__).resolve().parent.parent
        log_dir = os.getenv("VOL_HOST", str(_base / "vol"))
        _recorder = DataRecorder(log_dir=Path(log_dir) / "logs")
    return _recorder
