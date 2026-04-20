"""Minimal MCP (Model Context Protocol) client — stdio transport.

Spawns each configured MCP server as a subprocess, speaks JSON-RPC 2.0 over
stdin/stdout, caches the tool list, and exposes `call_tool(server, name, args)`
for the chat loop.

Scope: stdio only (the common case). SSE transport is future work. Each server
runs in its own thread that reads stdout line-by-line.

Config file: config/mcp_servers.json

    {
      "servers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "./vol"],
          "env": {}
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MCP_CONFIG_PATH = Path(os.getenv("MCP_CONFIG", BASE_DIR / "config" / "mcp_servers.json"))


@dataclass
class MCPServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    proc: subprocess.Popen | None = None
    tools: list[dict] = field(default_factory=list)
    _rpc_id: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)


class MCPClient:
    """Manages N MCP server subprocesses and their tool catalogs."""

    def __init__(self) -> None:
        self.servers: dict[str, MCPServer] = {}

    def load_config(self, path: Path | None = None) -> None:
        target = path or MCP_CONFIG_PATH
        if not target.exists():
            return
        try:
            cfg = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"  [mcp] 配置解析失败: {exc}")
            return
        for name, spec in (cfg.get("servers") or {}).items():
            self.servers[name] = MCPServer(
                name=name,
                command=spec.get("command", ""),
                args=list(spec.get("args", []) or []),
                env=dict(spec.get("env", {}) or {}),
            )

    def start_all(self) -> None:
        for srv in self.servers.values():
            self._start(srv)
            self._handshake(srv)
            srv.tools = self._list_tools(srv)

    def _start(self, srv: MCPServer) -> None:
        env = os.environ.copy()
        env.update(srv.env)
        try:
            srv.proc = subprocess.Popen(
                [srv.command, *srv.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            print(f"  [mcp:{srv.name}] 启动失败: {exc}")
            srv.proc = None

    def _rpc(self, srv: MCPServer, method: str, params: dict | None = None) -> dict:
        if srv.proc is None or srv.proc.stdin is None or srv.proc.stdout is None:
            return {"error": "server not running"}
        with srv._lock:
            srv._rpc_id += 1
            req = {"jsonrpc": "2.0", "id": srv._rpc_id, "method": method}
            if params is not None:
                req["params"] = params
            try:
                srv.proc.stdin.write(json.dumps(req) + "\n")
                srv.proc.stdin.flush()
                line = srv.proc.stdout.readline()
                if not line:
                    return {"error": "eof"}
                return json.loads(line)
            except (OSError, json.JSONDecodeError) as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

    def _handshake(self, srv: MCPServer) -> None:
        self._rpc(srv, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mathmodel", "version": "1.0"},
        })
        # Per MCP spec, send initialized notification
        if srv.proc and srv.proc.stdin:
            try:
                srv.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                srv.proc.stdin.flush()
            except OSError:
                pass

    def _list_tools(self, srv: MCPServer) -> list[dict]:
        resp = self._rpc(srv, "tools/list", {})
        result = resp.get("result") or {}
        return list(result.get("tools") or [])

    def call_tool(self, server: str, name: str, arguments: dict[str, Any]) -> dict:
        srv = self.servers.get(server)
        if srv is None:
            return {"error": f"unknown server: {server}"}
        resp = self._rpc(srv, "tools/call", {"name": name, "arguments": arguments})
        return resp.get("result") or resp

    def shutdown(self) -> None:
        for srv in self.servers.values():
            if srv.proc and srv.proc.poll() is None:
                try:
                    srv.proc.terminate()
                except OSError:
                    pass

    def as_openai_tools(self) -> list[tuple[str, str, dict]]:
        """Return (server, tool_name, openai_schema) tuples for all MCP tools.

        Tool names are prefixed with `mcp__<server>__` so they can't collide
        with built-in tool names. The prefix is stripped before dispatch.
        """
        out: list[tuple[str, str, dict]] = []
        for srv in self.servers.values():
            for t in srv.tools:
                tname = t.get("name", "")
                if not tname:
                    continue
                full = f"mcp__{srv.name}__{tname}"
                schema = {
                    "type": "function",
                    "function": {
                        "name": full,
                        "description": t.get("description", "")[:1024],
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                }
                out.append((srv.name, tname, schema))
        return out


_client: MCPClient | None = None


def get_client() -> MCPClient:
    global _client
    if _client is None:
        _client = MCPClient()
    return _client
