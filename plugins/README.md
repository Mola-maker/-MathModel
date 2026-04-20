# Plugins

AstrBot-inspired extension layer. Each subdirectory here is one plugin — a
Python package whose `__init__.py` exports a `Plugin` subclass.

## Quick start

```python
# plugins/my_plugin/__init__.py
from agents.extensions import Plugin, tool, persona, skill, on_event

class MyPlugin(Plugin):
    name = "my_plugin"
    version = "0.1.0"

    def initialize(self, config):
        self.api_key = config.get("api_key", "")

    @tool(
        name="greet",
        description="Say hi",
        parameters={"type": "object", "properties": {"who": {"type": "string"}}},
    )
    def greet(self, who: str = "world") -> str:
        return f"hello {who}"

    @persona("my_persona", name="My Persona", description="...", allowed_tools=("greet",))
    def my_persona(self):
        return {"system_prompt": "你是..."}

    @skill("cheat_sheet", description="...", triggers=("help", "how to"))
    def cheat_sheet(self):
        return "Useful instructions here."

    @on_event("phase_end")
    def on_phase_end(self, event):
        print(event["phase"], event["duration"])
```

Enable it in `config/plugins.toml`:

```toml
enabled = ["my_plugin"]

[plugin.my_plugin]
api_key = "..."
```

## Four extension points

| Decorator | Registers | Consumed by |
|-----------|-----------|-------------|
| `@tool` | OpenAI function-calling tool | Chat tool loop (`/api/chat`) |
| `@persona` | Assistant persona factory | `/api/personas`, chat endpoint |
| `@skill` | Prompt-injectable snippet | Prepended to system prompt when user message matches a trigger |
| `@on_event` | Event subscriber | `phase_start/end`, `rollback`, `pipeline_start/end` |

## MCP (Model Context Protocol)

Declare stdio servers in `config/mcp_servers.json`. Their tools are auto-merged
into the chat tool registry with prefix `mcp__<server>__<tool>`.

```json
{
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./vol"]
    }
  }
}
```

## File-based skills

Drop markdown files in `config/skills/*.md` with YAML-ish frontmatter:

```markdown
---
name: debug-p3
description: P3 solver debugging checklist
triggers: [solver failed, infeasible, nan]
---
When the P3 solver fails:
1. ...
```

These behave exactly like `@skill`-decorated methods.

## Reference plugin

`_example/` shows all four extension points wired up. Underscored directories
are not auto-discovered — rename (drop the leading `_`) or add to `enabled`
to load it.
