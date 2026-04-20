"""Reference plugin — demonstrates all four extension points.

This plugin is disabled by default (leading underscore). To enable it, add
`"_example"` to `enabled` in config/plugins.toml, or drop the underscore.

Shows:
    - @tool         register a function-calling tool
    - @persona      register an assistant persona
    - @skill        register a prompt-injectable skill
    - @on_event     subscribe to pipeline events
"""

from __future__ import annotations

import time
from typing import Any

from agents.extensions import Plugin, on_event, persona, skill, tool


class ExamplePlugin(Plugin):
    name = "example"
    version = "0.1.0"
    description = "Reference plugin — tools, personas, skills, events."

    def initialize(self, config: dict[str, Any]) -> None:
        self.greeting = config.get("greeting", "hello")
        self.phase_log: list[tuple[str, float]] = []

    # ── Tool ──────────────────────────────────────────────────────
    @tool(
        name="example_echo",
        description="Echo the given text back, prefixed with the plugin greeting.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    def echo(self, text: str = "") -> str:
        return f"{self.greeting}: {text}"

    # ── Persona ───────────────────────────────────────────────────
    @persona(
        persona_id="example_critic",
        name="范本批评者",
        description="示例插件提供的人格：对建模思路提出尖锐质疑",
        allowed_tools=("read_context",),
    )
    def critic(self) -> dict:
        return {
            "system_prompt": (
                "你是一位严厉的建模批评者。对用户提出的任何建模思路，"
                "先指出其最大的假设漏洞或数据缺陷，再给出修正方向。"
                "回复控制在 120 字以内，直接、不客套。"
            ),
            "allowed_tools": ("read_context",),
        }

    # ── Skill ─────────────────────────────────────────────────────
    @skill(
        name="example_debug_p3",
        description="Diagnostic checklist for P3 solver failures",
        triggers=("solver failed", "p3 error", "infeasible", "nan"),
    )
    def debug_skill(self) -> str:
        return (
            "When P3 solver fails:\n"
            "1. Check vol/logs/run.log for the traceback root cause.\n"
            "2. Verify input data in vol/data/cleaned_*.csv is non-empty.\n"
            "3. If infeasible, relax constraints one at a time.\n"
            "4. If NaN, check for zero-division in the objective function."
        )

    # ── Event subscriber ──────────────────────────────────────────
    @on_event("phase_end")
    def _track_phase(self, event: dict) -> None:
        self.phase_log.append((event.get("phase", "?"), event.get("duration") or 0.0))

    @on_event("pipeline_end")
    def _summarize(self, event: dict) -> None:
        if not self.phase_log:
            return
        total = sum(d for _, d in self.phase_log)
        print(f"  [example] 本次运行共 {len(self.phase_log)} 阶段，总计 {total:.1f}s")

    def terminate(self) -> None:
        self.phase_log.clear()
