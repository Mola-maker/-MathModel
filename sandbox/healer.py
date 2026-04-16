"""Healer — 解析 traceback，调用 LLM 修复代码，最多 MAX_ITER 轮。"""

import os
import re
from pathlib import Path
from agents.orchestrator import call_model

MAX_ITER = int(os.getenv("MAX_HEAL_ITERATIONS", "5"))

SYSTEM_PROMPT = """你是一个 Python 调试专家。
用户会给你：原始代码 + 错误信息。
请输出修复后的完整 Python 代码，只输出代码，不要任何解释。
用 ```python ... ``` 包裹代码块。"""


def extract_traceback(stderr: str) -> str:
    """提取最后一个 Traceback 块。"""
    lines = stderr.strip().splitlines()
    tb_start = -1
    for i, line in enumerate(lines):
        if line.startswith("Traceback"):
            tb_start = i
    return "\n".join(lines[tb_start:]) if tb_start >= 0 else stderr[-2000:]


def extract_code(response: str) -> str:
    """从 LLM 响应中提取代码块。"""
    match = re.search(r"```python\s*(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()


def is_logic_error(stderr: str) -> bool:
    """判断是否是数学/逻辑错误（不可通过语法修复）。"""
    logic_patterns = [
        "infeasible", "Infeasible", "unbounded",
        "nan", "NaN", "inf", "Inf",
        "singular matrix", "LinAlgError",
        "diverged", "not converged",
    ]
    return any(p in stderr for p in logic_patterns)


def heal(script_path: str, stderr: str, iteration: int) -> tuple[str, bool]:
    """
    修复脚本。
    返回 (fixed_code_or_empty, is_logic_err)。
    如果是逻辑错误或超过最大迭代次数，返回 ("", True)。
    """
    if iteration >= MAX_ITER:
        return "", True

    if is_logic_error(stderr):
        return "", True

    code = Path(script_path).read_text(encoding="utf-8")
    tb = extract_traceback(stderr)

    prompt = f"代码：\n```python\n{code}\n```\n\n错误：\n{tb}"
    response = call_model(SYSTEM_PROMPT, prompt, task="healing")
    fixed = extract_code(response)

    return fixed, False
