"""Tool registry for the chat assistant (OpenAI function-calling compatible).

Each tool is defined as an OpenAI tool schema. Handlers live in ui.server where
they have access to process state (_pipeline_proc, _sandbox_proc, etc.).

Personas (see persona_mgr.py) restrict which tool names an assistant session
can see — the registry here is the full universe.
"""

from __future__ import annotations


_TOOLS: list[dict] = [
    # ── Pipeline control ────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "pipeline_pause",
            "description": "Terminate the currently running pipeline process (main.py subprocess).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pipeline_restart",
            "description": "Stop current pipeline (if any) and start a new run from the given phase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phase": {
                        "type": "string",
                        "description": "Phase to start from",
                        "enum": [
                            "P0b", "P1", "P1.5", "P2", "P2.5",
                            "P3", "P3.5", "P4", "P4.5", "P5", "P5.5",
                        ],
                    }
                },
                "required": ["phase"],
            },
        },
    },
    # ── Config ──────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "set_model",
            "description": "Set the preferred LLM model for a given task. Moves the chosen model to the top of model_routes[task].models.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "enum": ["modeling", "codegen", "writing", "extraction", "default", "review", "healing"],
                    },
                    "model": {
                        "type": "string",
                        "description": "Model spec with prefix, e.g. 'or:anthropic/claude-sonnet-4-6' or 'ds:deepseek-chat'.",
                    },
                },
                "required": ["task", "model"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timeout",
            "description": "Set timeout_seconds for a task in model_routes.toml.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "value": {"type": "integer", "minimum": 10},
                },
                "required": ["task", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_budget",
            "description": "Set budget_seconds (total wall-clock budget including fallbacks) for a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "value": {"type": "integer", "minimum": 30},
                },
                "required": ["task", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_tokens",
            "description": "Set max_tokens for a task in pipeline.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "value": {"type": "integer", "minimum": 256},
                },
                "required": ["task", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_pipeline_config",
            "description": "Set a top-level key in pipeline.json (e.g. max_rollbacks, max_heal_iterations).",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {},
                },
                "required": ["key", "value"],
            },
        },
    },
    # ── Sandbox ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "sandbox_run",
            "description": "Run a script from vol/scripts/ inside the Docker sandbox. Output is streamed on /api/sandbox-output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "File name inside vol/scripts/, e.g. 'solver.py'.",
                    }
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandbox_exec",
            "description": "Write the given Python code to vol/scripts/_assistant_exec.py and execute it in the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Raw Python source to execute."}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandbox_kill",
            "description": "Terminate the current sandbox subprocess if it is running.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandbox_status",
            "description": "Report whether the sandbox is running and return the last ~20 output lines.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scripts",
            "description": "List all .py scripts available in vol/scripts/.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_context",
            "description": "Read a top-level key from context_store/context.json (returns JSON). Use sparingly and ask for narrow keys like 'modeling' or 'data_cleaning', not the whole context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Top-level key such as 'competition', 'modeling', 'code_execution', 'data_cleaning', 'review'.",
                    }
                },
                "required": ["key"],
            },
        },
    },
]


def all_tools() -> list[dict]:
    """Return every registered tool schema (OpenAI-compatible)."""
    return [dict(t) for t in _TOOLS]


def tools_for(names: list[str] | None) -> list[dict]:
    """Filter tools by allowed name list. None = all tools."""
    if names is None:
        return all_tools()
    allow = set(names)
    return [dict(t) for t in _TOOLS if t["function"]["name"] in allow]


def tool_names() -> list[str]:
    return [t["function"]["name"] for t in _TOOLS]
