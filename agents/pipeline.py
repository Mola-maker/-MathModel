"""Data-driven pipeline runner.

Each phase is a `PhaseSpec` (name + runner callable + metadata). Runners return a
`PhaseOutcome` that may request a rollback or suppress experience recording.
The runner executes specs in order, honours rollbacks (bounded), and exposes
`on_phase_start` / `on_phase_end` hooks for UI/metrics subscribers.

This replaces the giant if/elif branch in `main.py` with a registry, so new
phases (P2.8 模型对比, future inspectors, etc.) can be wired in one line.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Literal

PhaseRunner = Callable[[dict], "PhaseOutcome"]
PhaseHook = Callable[[str, dict, float | None], None]
RollbackHook = Callable[[str, str, int], None]  # from, to, rollback_index


@dataclass(frozen=True)
class PhaseOutcome:
    """Result of running a single phase."""

    ctx: dict
    rollback_to: str | None = None   # phase name to jump back to
    skip_record: bool = False        # skip experience recording this run
    note: str = ""                   # short log line shown after phase


@dataclass(frozen=True)
class PhaseSpec:
    """Static description of a pipeline phase."""

    name: str
    run: PhaseRunner
    record_experience: bool = False
    on_error: Literal["stop", "skip"] = "stop"
    description: str = ""


@dataclass
class PipelineResult:
    ctx: dict
    rollback_count: int
    timings: dict[str, float] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class PipelineRunner:
    """Execute a list of PhaseSpec with rollback + hooks."""

    def __init__(
        self,
        registry: list[PhaseSpec],
        max_rollbacks: int = 2,
        record_experience_fn: Callable[[str], None] | None = None,
    ) -> None:
        if not registry:
            raise ValueError("registry must not be empty")
        self.registry = tuple(registry)
        self.order: tuple[str, ...] = tuple(s.name for s in registry)
        self.specs: dict[str, PhaseSpec] = {s.name: s for s in registry}
        self.max_rollbacks = max_rollbacks
        self._record = record_experience_fn
        self.on_phase_start: PhaseHook | None = None
        self.on_phase_end: PhaseHook | None = None
        self.on_rollback: RollbackHook | None = None

    def run(self, ctx: dict, start: str = "") -> PipelineResult:
        start = start or self.order[0]
        if start not in self.specs:
            raise ValueError(f"unknown start phase: {start}")

        i = self.order.index(start)
        rollbacks = 0
        result = PipelineResult(ctx=ctx, rollback_count=0)

        while i < len(self.order):
            name = self.order[i]
            spec = self.specs[name]

            self._banner(name)
            self._fire(self.on_phase_start, name, result.ctx, None)
            t0 = time.time()
            try:
                outcome = spec.run(result.ctx)
            except Exception as exc:  # noqa: BLE001
                dt = time.time() - t0
                result.timings[name] = dt
                result.errors[name] = f"{type(exc).__name__}: {exc}"
                tb = traceback.format_exc()
                self._fire(self.on_phase_end, name, result.ctx, dt)
                if spec.on_error == "skip":
                    print(f"[{name}-SKIP] {exc}")
                    i += 1
                    continue
                print(f"[{name}-ERR] {exc}\n{tb}")
                return result

            dt = time.time() - t0
            result.timings[name] = dt
            result.ctx = outcome.ctx
            if outcome.note:
                print(f"[{name}] {outcome.note}")
            self._fire(self.on_phase_end, name, result.ctx, dt)

            if outcome.rollback_to:
                if rollbacks >= self.max_rollbacks:
                    print(f"[{name}-WARN] 已达最大回滚次数 ({self.max_rollbacks})，继续")
                else:
                    if outcome.rollback_to not in self.specs:
                        print(f"[{name}-ERR] rollback 目标未知: {outcome.rollback_to}")
                        return result
                    rollbacks += 1
                    result.rollback_count = rollbacks
                    i = self.order.index(outcome.rollback_to)
                    print(f"[ROLLBACK] → {outcome.rollback_to} ({rollbacks}/{self.max_rollbacks})")
                    if self.on_rollback is not None:
                        try:
                            self.on_rollback(name, outcome.rollback_to, rollbacks)
                        except Exception as exc:  # noqa: BLE001
                            print(f"  [hook] rollback 钩子异常: {exc}")
                    continue

            if spec.record_experience and not outcome.skip_record and self._record:
                try:
                    self._record(name)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [experience] {name} 记录失败: {exc}")

            i += 1

        return result

    @staticmethod
    def _banner(name: str) -> None:
        print(f"\n{'=' * 50}\n  开始 {name}\n{'=' * 50}")

    @staticmethod
    def _fire(hook: PhaseHook | None, name: str, ctx: dict, dt: float | None) -> None:
        if hook is None:
            return
        try:
            hook(name, ctx, dt)
        except Exception as exc:  # noqa: BLE001
            print(f"  [hook] {name} 钩子异常: {exc}")
