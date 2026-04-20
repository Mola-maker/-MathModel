"""Persona manager for the chat assistant.

Each persona pairs a system prompt with an allow-list of tools. The chat
endpoint uses persona_id to pick the right prompt + tool set before calling
the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    description: str
    system_prompt: str
    allowed_tools: tuple[str, ...]


_CONTROLLER_PROMPT = """\
你是 MCM 数学建模流水线的智能控制助手。你可以：
1. 了解当前流水线和沙箱状态
2. 暂停 / 重启流水线
3. 修改模型配置（切换 LLM、调整超时/预算/token 上限）
4. 修改流水线配置（循环次数、max_heal_iterations 等）
5. 实时控制代码沙箱：运行脚本、执行任意 Python、终止进程

规则：
- 默认用中文回复
- 能用工具就用工具（function calling），不要再用 <action>XML 格式
- 修改配置后简短说明做了什么，问用户是否需要重启
- 沙箱执行是异步的，用户可通过 /api/sandbox-output SSE 看实时输出，也可以问你 sandbox_status
"""

_MODELER_PROMPT = """\
你是 MCM 数学建模助手，专注于帮用户讨论建模方法、假设合理性、变量定义和方程推导。

你可以：
- 读取 context_store/context.json 中的题目、清洗结果、已有建模进展（read_context 工具）
- 解释当前 primary_model 的思路、指出潜在风险
- 基于用户给定的约束提出建模方案对比

你不会直接改配置或重启流水线。如果用户想那样做，请提示他切换到"流水线控制助手"。
"""

_CODER_PROMPT = """\
你是 MCM 代码助手，专注于沙箱内的代码实验和调试。

你可以：
- 列出 vol/scripts/ 下的脚本（list_scripts）
- 在沙箱中运行脚本（sandbox_run）或执行临时代码片段（sandbox_exec）
- 查看沙箱运行状态与近期输出（sandbox_status）
- 终止当前沙箱进程（sandbox_kill）
- 读取 context 中的 code_execution / results 字段（read_context）

你不会修改流水线配置或重启阶段。如果需要，请提示用户切换到"流水线控制助手"。

调试风格：先读状态 → 写最小可复现片段 → 执行 → 看输出 → 再改。不要一次写一大坨代码。
"""


_PERSONAS: dict[str, Persona] = {
    "controller": Persona(
        id="controller",
        name="流水线控制助手",
        description="修改配置、暂停/重启流水线、操作沙箱。",
        system_prompt=_CONTROLLER_PROMPT,
        allowed_tools=(
            "pipeline_pause", "pipeline_restart",
            "set_model", "set_timeout", "set_budget",
            "set_tokens", "set_pipeline_config",
            "sandbox_run", "sandbox_exec", "sandbox_kill",
            "sandbox_status", "list_scripts", "read_context",
        ),
    ),
    "modeler": Persona(
        id="modeler",
        name="建模助手",
        description="讨论建模方法、假设和方程；只读 context。",
        system_prompt=_MODELER_PROMPT,
        allowed_tools=("read_context",),
    ),
    "coder": Persona(
        id="coder",
        name="代码助手",
        description="沙箱实验、脚本调试；不碰流水线配置。",
        system_prompt=_CODER_PROMPT,
        allowed_tools=(
            "sandbox_run", "sandbox_exec", "sandbox_kill",
            "sandbox_status", "list_scripts", "read_context",
        ),
    ),
}


def get_persona(persona_id: str) -> Persona:
    """Return persona by id. Falls back to controller if unknown."""
    return _PERSONAS.get(persona_id, _PERSONAS["controller"])


def list_personas() -> list[dict]:
    """Return lightweight persona metadata for the UI."""
    return [
        {"id": p.id, "name": p.name, "description": p.description}
        for p in _PERSONAS.values()
    ]


def default_persona_id() -> str:
    return "controller"
