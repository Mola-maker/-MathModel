"""ExtensionRegistry — central coordinator for plugins / MCP / skills / personas.

Discovery order:
    1. Load config/plugins.toml (enabled list + per-plugin config).
    2. For each enabled plugin, import plugins.<name> and look for a Plugin
       subclass (any class that inherits from plugin.Plugin).
    3. Instantiate, call initialize(config), scan for decorated methods.
    4. Collect tools / personas / skills / event handlers into the registry.
    5. Load MCP servers from config/mcp_servers.json and merge their tools.
    6. Load local skills from config/skills/*.md.

The registry is a singleton — get_registry() returns the process-wide instance.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .mcp import MCPClient, get_client
from .plugin import EventMeta, PersonaMeta, Plugin, SkillMeta, ToolMeta
from .skills import Skill, load_skills

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = Path(os.getenv("PLUGINS_DIR", BASE_DIR / "plugins"))
PLUGINS_CONFIG = Path(os.getenv("PLUGINS_CONFIG", BASE_DIR / "config" / "plugins.toml"))


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import tomllib  # Py 3.11+
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            import tomli  # type: ignore
            return tomli.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}


@dataclass
class ToolEntry:
    meta: ToolMeta
    handler: Callable[..., Any]
    source: str  # "plugin:<name>" or "mcp:<server>"


@dataclass
class PersonaEntry:
    meta: PersonaMeta
    factory: Callable[[], dict]
    source: str


@dataclass
class SkillEntry:
    skill: Skill


@dataclass
class EventEntry:
    kind: str
    handler: Callable[[dict], None]
    source: str


@dataclass
class ExtensionRegistry:
    plugins: list[Plugin] = field(default_factory=list)
    tools: dict[str, ToolEntry] = field(default_factory=dict)
    personas: dict[str, PersonaEntry] = field(default_factory=dict)
    skills: list[SkillEntry] = field(default_factory=list)
    event_handlers: list[EventEntry] = field(default_factory=list)
    mcp: MCPClient | None = None

    def emit_event(self, kind: str, payload: dict) -> None:
        """Dispatch an event to all subscribed handlers. Never raises."""
        for entry in self.event_handlers:
            if entry.kind != kind:
                continue
            try:
                entry.handler(payload)
            except Exception as exc:  # noqa: BLE001
                print(f"  [ext:{entry.source}] event {kind} 处理异常: {exc}")

    def match_skills(self, text: str, limit: int = 3) -> list[Skill]:
        """Return skills whose triggers match the text. Plugin skills included."""
        from .skills import match_skills as _match
        return _match(text, [e.skill for e in self.skills], limit=limit)

    def tool_schemas(self) -> list[dict]:
        """Return all extension tools as OpenAI function schemas."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.meta.name,
                    "description": t.meta.description,
                    "parameters": t.meta.parameters,
                },
            }
            for t in self.tools.values()
        ]

    def persona_dicts(self) -> list[dict]:
        """Return persona metadata (for UI listing)."""
        return [
            {"id": p.meta.id, "name": p.meta.name, "description": p.meta.description,
             "allowed_tools": list(p.meta.allowed_tools), "source": p.source}
            for p in self.personas.values()
        ]


_registry: ExtensionRegistry | None = None


def get_registry() -> ExtensionRegistry:
    global _registry
    if _registry is None:
        _registry = ExtensionRegistry()
    return _registry


def _scan_plugin(instance: Plugin, registry: ExtensionRegistry) -> None:
    """Scan a Plugin instance for decorated methods and register them.

    Dunder names (`__init__`, etc.) are skipped but single-underscore methods
    are kept so users can mark private event handlers with `@on_event`.
    """
    src = f"plugin:{instance.name}"
    for attr_name in dir(instance):
        if attr_name.startswith("__"):
            continue
        attr = getattr(instance, attr_name, None)
        if not callable(attr):
            continue
        kind = getattr(attr, "_ext_kind", None)
        meta = getattr(attr, "_ext_meta", None)
        if kind is None or meta is None:
            continue
        if kind == "tool" and isinstance(meta, ToolMeta):
            registry.tools[meta.name] = ToolEntry(meta=meta, handler=attr, source=src)
        elif kind == "persona" and isinstance(meta, PersonaMeta):
            registry.personas[meta.id] = PersonaEntry(meta=meta, factory=attr, source=src)
        elif kind == "skill" and isinstance(meta, SkillMeta):
            try:
                body = attr()
            except Exception as exc:  # noqa: BLE001
                print(f"  [{src}] skill {meta.name} 加载异常: {exc}")
                body = ""
            registry.skills.append(SkillEntry(skill=Skill(
                name=meta.name, description=meta.description,
                triggers=tuple(t.lower() for t in meta.triggers),
                body=str(body), source=src,
            )))
        elif kind == "event" and isinstance(meta, EventMeta):
            registry.event_handlers.append(EventEntry(kind=meta.kind, handler=attr, source=src))


def _load_plugin_module(name: str) -> list[Plugin]:
    """Import plugins.<name> and instantiate every Plugin subclass it exports."""
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        module = importlib.import_module(f"plugins.{name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [plugin:{name}] 导入失败: {exc}")
        return []
    out: list[Plugin] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is Plugin:
            continue
        if not issubclass(obj, Plugin):
            continue
        if obj.__module__ != module.__name__:
            continue  # imported reference, skip
        try:
            out.append(obj())
        except Exception as exc:  # noqa: BLE001
            print(f"  [plugin:{name}] 实例化失败: {exc}")
    return out


def load_all() -> ExtensionRegistry:
    """Load plugins + MCP + local skills. Idempotent — safe to call twice."""
    registry = get_registry()
    # 1. Plugins
    plugins_cfg = _load_toml(PLUGINS_CONFIG)
    enabled = plugins_cfg.get("enabled")
    plugin_configs: dict = plugins_cfg.get("plugin", {}) or {}

    if PLUGINS_DIR.exists():
        # All subdirectories with an __init__.py are candidates
        all_dirs = sorted(
            p.name for p in PLUGINS_DIR.iterdir()
            if p.is_dir() and not p.name.startswith(".") and (p / "__init__.py").exists()
        )
        # Auto-discovery skips leading-underscore dirs (convention: disabled)
        discovered = [n for n in all_dirs if not n.startswith("_")]
    else:
        all_dirs = []
        discovered = []

    if enabled is None:
        targets = discovered
    else:
        # Explicit enable list overrides the underscore convention
        targets = [n for n in enabled if n in all_dirs]

    for name in targets:
        instances = _load_plugin_module(name)
        for inst in instances:
            try:
                inst.initialize(plugin_configs.get(name, {}) or {})
            except Exception as exc:  # noqa: BLE001
                print(f"  [plugin:{name}] initialize 失败: {exc}")
                continue
            registry.plugins.append(inst)
            _scan_plugin(inst, registry)
            print(f"  [plugin] 已加载 {inst.name} v{inst.version}")

    # 2. MCP servers
    client = get_client()
    client.load_config()
    if client.servers:
        client.start_all()
        registry.mcp = client
        for server, tname, schema in client.as_openai_tools():
            full = schema["function"]["name"]
            # Bind a handler that dispatches back through the MCP client
            def _make_handler(s: str, n: str):
                def _h(**kwargs):
                    return client.call_tool(s, n, kwargs)
                return _h
            registry.tools[full] = ToolEntry(
                meta=ToolMeta(name=full, description=schema["function"]["description"],
                              parameters=schema["function"]["parameters"]),
                handler=_make_handler(server, tname),
                source=f"mcp:{server}",
            )
        print(f"  [mcp] 加载 {len(client.servers)} 个服务器，{sum(len(s.tools) for s in client.servers.values())} 个工具")

    # 3. File-based skills
    for sk in load_skills():
        registry.skills.append(SkillEntry(skill=sk))

    return registry


def shutdown() -> None:
    """Terminate MCP servers and plugins. Safe to call multiple times."""
    reg = get_registry()
    for p in reg.plugins:
        try:
            p.terminate()
        except Exception as exc:  # noqa: BLE001
            print(f"  [plugin:{p.name}] terminate 异常: {exc}")
    if reg.mcp is not None:
        reg.mcp.shutdown()
