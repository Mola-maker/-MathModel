"""Extension system — plugins, MCP, skills, personas.

AstrBot-inspired design. Four customization surfaces:

    1. Plugins  : Python packages under plugins/<name>/ with a Plugin subclass.
                  Register tools / personas / skills / event handlers via
                  decorators.
    2. MCP      : External tool servers (stdio transport) declared in
                  config/mcp_servers.json. Their tools merge into the
                  tool registry automatically.
    3. Skills   : Markdown snippets with YAML frontmatter under
                  config/skills/*.md. Loaded into prompts on demand.
    4. Personas : Already exist in persona_mgr.py; plugins can add new ones.

Boot order (see registry.load_all):
    1. Discover plugins/ dir and import each package.
    2. Instantiate Plugin subclasses, run initialize() with per-plugin config.
    3. Collect decorated tools/personas/skills/event_handlers.
    4. Load MCP servers, list their tools, wrap as OpenAI schemas.
    5. Load local skills.
"""

from __future__ import annotations

from .plugin import Plugin, on_event, persona, skill, tool
from .registry import ExtensionRegistry, get_registry, load_all
from .skills import Skill, load_skills, match_skills

__all__ = [
    "Plugin",
    "tool",
    "persona",
    "skill",
    "on_event",
    "ExtensionRegistry",
    "get_registry",
    "load_all",
    "Skill",
    "load_skills",
    "match_skills",
]
