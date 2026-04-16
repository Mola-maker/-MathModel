"""Research Agent — 领域调研、文献速读、建模路径预判、选题评分矩阵。"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import parse_json as _parse_json

SYSTEM_DOMAIN = """你是一位数学建模竞赛专家，擅长快速分析题目领域背景。
根据题目文本，输出严格 JSON（不含 markdown 代码块），结构：
{
  "domain": "领域名称",
  "key_concepts": ["概念1", "概念2"],
  "data_hints": ["数据需求1", "数据需求2"],
  "model_paths": [
    {"name": "方案名", "type": "模型类型", "pros": "优点", "cons": "缺点"},
    ...
  ],
  "domain_summary": "3-5句领域速读"
}"""

SYSTEM_SCORING = """你是一位有丰富竞赛经验的教练。
根据提供的题目列表和每道题的域分析，输出严格 JSON（不含 markdown 代码块），结构：
{
  "scores": [
    {
      "problem": "A",
      "difficulty": 1-10,
      "data_availability": 1-10,
      "innovation_space": 1-10,
      "math_depth": 1-10,
      "total": 合计分,
      "recommendation": "推荐/备选/不推荐",
      "reason": "一句话理由"
    }
  ],
  "final_recommendation": "A",
  "rationale": "选题理由"
}"""

SYSTEM_ASSUMPTION = """你是一位严谨的数学建模专家。
根据题目和选定的建模路径，生成合理假设列表。
输出严格 JSON（不含 markdown 代码块），结构：
{
  "assumptions": [
    {"id": "A1", "text": "假设内容", "justification": "理由", "impact": "影响范围"},
    ...
  ],
  "variables": {
    "symbol": {"meaning": "含义", "unit": "单位", "range": "范围"},
    ...
  }
}"""


class ResearchAgent:
    """P1 题目解读与领域调研。"""

    def analyze_domain(self, problem_text: str) -> dict:
        """对单道题目做领域分析。"""
        raw = call_model(SYSTEM_DOMAIN, f"题目：\n{problem_text}")
        return _parse_json(raw)

    def score_problems(self, problems: dict[str, str]) -> dict:
        """
        对多道题目打分，返回选题建议。
        problems: {"A": "题目文本A", "B": "题目文本B", ...}
        """
        analyses = {k: self.analyze_domain(t) for k, t in problems.items()}
        user_prompt = (
            "题目列表与领域分析：\n"
            + json.dumps(analyses, ensure_ascii=False, indent=2)
        )
        return _parse_json(call_model(SYSTEM_SCORING, user_prompt))

    def generate_assumptions(self, problem_text: str, model_path: str) -> dict:
        """为选定题目和建模路径生成假设与变量表。"""
        user_prompt = f"题目：\n{problem_text}\n\n选定建模路径：{model_path}"
        return _parse_json(call_model(SYSTEM_ASSUMPTION, user_prompt))

    def run(
        self,
        problems: dict[str, str],
        selected_problem: str | None = None,
    ) -> dict:
        """
        完整 P1 流程。
        1. 分析所有题目
        2. 生成选题评分矩阵
        3. 对选定题目生成假设框架
        4. 写入 context_store

        Args:
            problems: {"A": "...", "B": "...", ...}  题目文本字典
            selected_problem: 若已人工选题则传入 ("A"/"B"/...)，否则用 AI 建议

        Returns:
            更新后的 context dict
        """
        ctx = load_context()

        # Step 1 & 2: 评分选题
        scoring = self.score_problems(problems)
        recommended = scoring.get("final_recommendation", "A")
        selected = selected_problem or recommended

        # Step 3: 假设与变量
        problem_text = problems.get(selected, list(problems.values())[0])
        domain_info = self.analyze_domain(problem_text)
        model_path = (
            domain_info.get("model_paths", [{}])[0].get("name", "未确定")
            if domain_info.get("model_paths") else "未确定"
        )
        assumption_data = self.generate_assumptions(problem_text, model_path)

        # Step 4: 写入 context
        ctx["phase"] = "P1_complete"
        ctx["competition"]["problem_text"] = problem_text
        ctx["competition"]["selected_problem"] = selected
        ctx["competition"]["tasks"] = domain_info.get("key_concepts", [])
        ctx["research"]["domain_summary"] = domain_info.get("domain_summary", "")
        ctx["research"]["references"] = domain_info.get("data_hints", [])
        ctx["research"]["scoring"] = scoring  # type: ignore[index]
        ctx["modeling"]["assumptions"] = assumption_data.get("assumptions", [])
        ctx["modeling"]["variables"] = assumption_data.get("variables", {})
        ctx["modeling"]["model_type"] = model_path

        save_context(ctx)
        return ctx
