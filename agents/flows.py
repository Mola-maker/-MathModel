"""Flow manager — structured pipeline for per-question coder→writer flow.

Adapted from MathModelAgent Flows class.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SolutionStep:
    """One step in the solution flow (coder prompt + writer prompt)."""
    key: str
    coder_prompt: str
    writer_template_key: str = ""


@dataclass
class FlowPlan:
    """Complete flow plan for all questions."""
    eda: SolutionStep | None = None
    questions: list[SolutionStep] = field(default_factory=list)
    sensitivity: SolutionStep | None = None

    # Write-only steps (no coder)
    write_only: list[dict[str, str]] = field(default_factory=list)


class Flows:
    """Builds structured execution flows from modeling output."""

    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.tasks = ctx.get("competition", {}).get("tasks", [])
        self.problem_text = ctx.get("competition", {}).get("problem_text", "")
        self.model_info = ctx.get("modeling", {})

    def build_solution_flows(self) -> FlowPlan:
        """Build the full solution flow: EDA → per-question → sensitivity."""
        plan = FlowPlan()
        model = self.model_info.get("primary_model", {})
        model_name = model.get("model_name", "")
        model_type = self.model_info.get("model_type", "")
        solution_method = self.model_info.get("solution_method", "")
        variables = self.model_info.get("variables", {})
        viz_plan = model.get("visualization_plan", [])
        eda_plan = model.get("eda_plan", "对数据进行探索性分析")

        # ── EDA step ──
        plan.eda = SolutionStep(
            key="eda",
            coder_prompt=(
                f"对数据进行 EDA 分析（数据清洗、缺失值处理、异常值检测、分布可视化、相关性分析）。\n"
                f"建模手的 EDA 方案：{eda_plan}\n"
                f"不需要复杂模型，清洗后的数据保存到当前目录。\n"
                f"图片保存到 /workspace/vol/outputs/figures/"
            ),
        )

        # ── Per-question steps ──
        equations = model.get("equations", [])
        equations_str = "\n".join(
            f"  {eq.get('id', '')}: {eq.get('latex', '')} — {eq.get('description', '')}"
            for eq in equations
            if isinstance(eq, dict)
        )

        for i, task in enumerate(self.tasks, 1):
            task_str = task if isinstance(task, str) else str(task)
            viz_for_q = [v for v in viz_plan if isinstance(v, dict)]

            plan.questions.append(SolutionStep(
                key=f"ques{i}",
                coder_prompt=(
                    f"问题 {i}：{task_str}\n\n"
                    f"模型类型：{model_type}\n"
                    f"模型名称：{model_name}\n"
                    f"求解方法：{solution_method}\n"
                    f"核心方程：\n{equations_str}\n\n"
                    f"可视化方案：{viz_for_q}\n"
                    f"请生成完整可运行的 Python 脚本求解此问题。\n"
                    f"图片保存到 /workspace/vol/outputs/figures/"
                ),
            ))

        # ── Sensitivity step ──
        sensitivity_plan = model.get("sensitivity_plan", "对模型关键参数进行敏感性分析")
        plan.sensitivity = SolutionStep(
            key="sensitivity_analysis",
            coder_prompt=(
                f"对模型进行敏感性分析。\n"
                f"建模手方案：{sensitivity_plan}\n"
                f"模型类型：{model_type}，模型名称：{model_name}\n"
                f"变量：{variables}\n"
                f"图片保存到 /workspace/vol/outputs/figures/"
            ),
        )

        return plan

    def build_write_flows(self, results_summary: str) -> list[dict[str, str]]:
        """Build write-only flows for non-code sections."""
        model = self.model_info.get("primary_model", {})
        model_name = model.get("model_name", "")

        return [
            {
                "key": "strengths",
                "prompt": (
                    f"基于模型 {model_name} 的求解结果，\n"
                    f"结果摘要：{results_summary[:500]}\n"
                    f"写出模型的 3-4 个优势，用 LaTeX itemize 格式。"
                ),
            },
            {
                "key": "weaknesses",
                "prompt": (
                    f"基于模型 {model_name} 的求解结果，\n"
                    f"写出模型的 2-3 个局限性，用 LaTeX itemize 格式。"
                ),
            },
        ]

    def get_all_steps(self) -> list[SolutionStep]:
        """Return all solution steps in order."""
        plan = self.build_solution_flows()
        steps = []
        if plan.eda:
            steps.append(plan.eda)
        steps.extend(plan.questions)
        if plan.sensitivity:
            steps.append(plan.sensitivity)
        return steps
