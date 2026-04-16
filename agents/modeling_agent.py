"""Modeling Agent — 决策树驱动的模型推导、公式生成、量纲验证、模型对比。"""

import json
import re
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import parse_json as _parse_json
from agents.prompts import MODELER_PROMPT
from agents.experience_recorder import get_relevant_experience

SYSTEM_DERIVE = MODELER_PROMPT

SYSTEM_COMPARE = """你是一位数学建模竞赛评委。
对比给定的多个建模方案，从严谨性、可实现性、竞赛得分潜力三个维度分析。
输出严格 JSON（不含 markdown 代码块），结构：
{
  "comparison": [
    {
      "model": "方案名",
      "rigor": 1-10,
      "feasibility": 1-10,
      "score_potential": 1-10,
      "verdict": "推荐/备选/不推荐",
      "note": "一句话说明"
    }
  ],
  "winner": "推荐方案名",
  "reason": "选择理由"
}"""

SYSTEM_VALIDATE = """你是一位严谨的数学教授，专门检查量纲一致性和边界条件。
检查给定的数学模型，输出严格 JSON（不含 markdown 代码块），结构：
{
  "dimension_check": [
    {"equation": "E1", "status": "pass|fail|warning", "note": "说明"}
  ],
  "boundary_check": [
    {"condition": "条件描述", "status": "pass|fail|warning", "note": "说明"}
  ],
  "overall": "pass|fail|warning",
  "suggestions": ["改进建议1", "改进建议2"]
}"""


class ModelingAgent:
    """P2 数学建模：推导核心模型、对比方案、验证合理性。"""

    def derive_model(
        self,
        problem_text: str,
        assumptions: list[dict],
        variables: dict,
        model_type_hint: str = "",
    ) -> dict:
        """推导核心数学模型，输出含 LaTeX 公式的结构化结果。"""
        past_exp = get_relevant_experience("P2", max_entries=2, max_chars=2000)
        exp_section = f"\n\n{past_exp}\n" if past_exp else ""
        user_prompt = (
            f"题目：\n{problem_text}\n\n"
            f"假设：\n{json.dumps(assumptions, ensure_ascii=False)}\n\n"
            f"变量表：\n{json.dumps(variables, ensure_ascii=False)}\n\n"
            f"建模方向提示：{model_type_hint}"
            f"{exp_section}"
        )
        raw = call_model(SYSTEM_DERIVE, user_prompt, task="modeling")
        return _parse_json(raw)

    def compare_models(self, models: list[dict]) -> dict:
        """对比多个建模方案，返回推荐结果。"""
        user_prompt = f"建模方案列表：\n{json.dumps(models, ensure_ascii=False, indent=2)}"
        raw = call_model(SYSTEM_COMPARE, user_prompt, task="modeling")
        return _parse_json(raw)

    def validate_model(self, model: dict, variables: dict) -> dict:
        """检查量纲一致性和边界条件合理性。"""
        user_prompt = (
            f"模型：\n{json.dumps(model, ensure_ascii=False, indent=2)}\n\n"
            f"变量表：\n{json.dumps(variables, ensure_ascii=False)}"
        )
        raw = call_model(SYSTEM_VALIDATE, user_prompt, task="modeling")
        return _parse_json(raw)

    def run(self) -> dict:
        """
        完整 P2 流程：
        1. 从 context_store 读取 P1 结果
        2. 推导主模型（+ 备选模型）
        3. 对比选优
        4. 量纲/边界验证
        5. 写入 context_store
        """
        ctx = load_context()
        problem_text = ctx["competition"]["problem_text"]
        assumptions = ctx["modeling"].get("assumptions", [])
        variables = ctx["modeling"].get("variables", {})
        model_type_hint = ctx["modeling"].get("model_type", "")

        # 主模型推导
        primary = self.derive_model(problem_text, assumptions, variables, model_type_hint)

        # 备选模型（换一种思路）
        alt_hint = "请提供一种与上次不同的建模思路"
        alternative = self.derive_model(problem_text, assumptions, variables, alt_hint)

        # 对比
        comparison = self.compare_models([primary, alternative])
        winner_name = comparison.get("winner", primary.get("model_name", "主模型"))
        winner = primary if winner_name == primary.get("model_name") else alternative

        # 验证
        validation = self.validate_model(winner, variables)

        # 写入 context
        ctx["phase"] = "P2_complete"
        ctx["modeling"]["model_type"] = winner.get("model_type", model_type_hint)
        ctx["modeling"]["equations_latex"] = json.dumps(
            winner.get("equations", []), ensure_ascii=False
        )
        ctx["modeling"]["primary_model"] = winner          # type: ignore[index]
        ctx["modeling"]["comparison"] = comparison         # type: ignore[index]
        ctx["modeling"]["validation"] = validation         # type: ignore[index]
        ctx["modeling"]["solution_method"] = winner.get("solution_method", "")  # type: ignore[index]

        save_context(ctx)
        return ctx
