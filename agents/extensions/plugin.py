"""Plugin base class and decorators.

A plugin is a class that extends `Plugin` and whose instance methods are
decorated with one of `@tool`, `@persona`, `@skill`, `@on_event`. The registry
scans an instantiated plugin for these markers and wires them up.

Decorators attach a namedtuple-style dict to the method via a well-known
attribute (`_ext_kind` / `_ext_meta`). This keeps the plugin class pure
Python — no metaclass, no import-time side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolMeta:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class PersonaMeta:
    id: str
    name: str
    description: str
    allowed_tools: tuple[str, ...] = ()


@dataclass
class SkillMeta:
    name: str
    description: str
    triggers: tuple[str, ...] = ()


@dataclass
class EventMeta:
    kind: str


def _mark(kind: str, meta: Any) -> Callable:
    def decorator(fn: Callable) -> Callable:
        fn._ext_kind = kind  # type: ignore[attr-defined]
        fn._ext_meta = meta  # type: ignore[attr-defined]
        return fn
    return decorator


def tool(name: str, description: str, parameters: dict | None = None) -> Callable:
    """Register the decorated method as an OpenAI function-calling tool.

    `parameters` is a JSON schema object. If omitted, the tool accepts no args.
    The method signature should accept `**kwargs` matching the schema and
    return a JSON-serializable value (string or dict).
    """
    return _mark("tool", ToolMeta(
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
    ))


def persona(persona_id: str, name: str, description: str, allowed_tools: tuple[str, ...] = ()) -> Callable:
    """Register the decorated method as a persona factory.

    The method should return a dict with at least a `system_prompt` key.
    Optionally `allowed_tools` (overrides decorator) and extra metadata.
    """
    return _mark("persona", PersonaMeta(
        id=persona_id, name=name, description=description, allowed_tools=allowed_tools,
    ))


def skill(name: str, description: str, triggers: tuple[str, ...] = ()) -> Callable:
    """Register the decorated method as a skill body provider.

    The method should return a markdown string (the skill body) — typically a
    short instruction block that gets injected into the system prompt when the
    user message matches any of the triggers.
    """
    return _mark("skill", SkillMeta(name=name, description=description, triggers=triggers))


def on_event(kind: str) -> Callable:
    """Subscribe the decorated method to pipeline events of the given kind.

    Known kinds: phase_start, phase_end, rollback, pipeline_start, pipeline_end.
    Handlers receive the event dict as their single positional arg.
    """
    return _mark("event", EventMeta(kind=kind))


class Plugin:
    """Base class for all plugins.

    Subclasses set `name` + `version` as class attrs. Override `initialize`
    to receive per-plugin config (from config/plugins.toml) at load time, and
    `terminate` to clean up on shutdown.
    """

    name: str = ""
    version: str = "0.0.0"
    description: str = ""

    def initialize(self, config: dict[str, Any]) -> None:  # pragma: no cover - hook
        """Called once after instantiation with merged config."""

    def terminate(self) -> None:  # pragma: no cover - hook
        """Called on graceful shutdown."""
