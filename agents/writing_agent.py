"""Writing Agent — 按 MCM 标准结构逐章生成论文，输出 LaTeX。

Upgraded with paragraph-style writing rules and figure insertion from MathModelAgent.
"""

import json
import os
import shutil
from pathlib import Path
from string import Template
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.prompts.writer import get_writer_section_prompts, MCM_LATEX_TEMPLATE
from agents.experience_recorder import get_relevant_experience

_BASE = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.getenv("VOL_HOST", str(_BASE / "vol"))) / "outputs"
PAPER_DIR = Path(os.getenv("PAPER_DIR", str(_BASE / "paper")))

SECTIONS = [
    "abstract",
    "introduction",
    "assumptions",
    "model_formulation",
    "solution",
    "results_analysis",
    "sensitivity",
    "conclusion",
    "references",
]

SYSTEM_SECTION = get_writer_section_prompts()


class WritingAgent:
    """P4 论文撰写：逐章生成 LaTeX 内容（段落式写作 + 图片引用）。"""

    def _discover_figures(self) -> list[str]:
        """Discover available figure files for LaTeX inclusion."""
        fig_src = OUTPUT_DIR / "figures"
        if not fig_src.exists():
            return []
        return [
            f.name
            for f in sorted(fig_src.glob("*.*"))
            if f.suffix.lower() in (".png", ".pdf", ".jpg", ".eps")
        ]

    def write_section(
        self,
        section: str,
        ctx: dict,
        available_figures: list[str] | None = None,
        experience_hint: str = "",
    ) -> str:
        """生成单个章节内容。"""
        system = SYSTEM_SECTION.get(section, "请写该章节内容，输出 LaTeX 格式。")

        # Build richer context with per-step results
        per_step = ctx.get("results", {}).get("per_step_results", {})
        context_summary = {
            "problem": ctx["competition"]["problem_text"][:600],
            "model": ctx["modeling"].get("primary_model", {}),
            "assumptions": ctx["modeling"].get("assumptions", []),
            "variables": ctx["modeling"].get("variables", {}),
            "results_summary": ctx.get("results", {}).get("solver_stdout", "")[:1000],
            "per_step_results": {k: v[:400] for k, v in per_step.items()} if per_step else {},
            "references": ctx["research"].get("references", []),
        }

        user_prompt = (
            f"论文上下文信息：\n{json.dumps(context_summary, ensure_ascii=False, indent=2)}"
        )

        if experience_hint:
            user_prompt += experience_hint

        # Add available figures for results/sensitivity sections
        if available_figures and section in ("results_analysis", "sensitivity"):
            fig_list = "\n".join(f"  - {f}" for f in available_figures)
            user_prompt += (
                f"\n\n可用图片文件（在 figures/ 目录中）：\n{fig_list}\n"
                f"请在论文中用 LaTeX figure 环境引用这些图片，每张图至少配3行分析。"
            )

        return call_model(system, user_prompt, task="writing")

    def _get_experience_hint(self) -> str:
        """Inject past writing experience into the first section's context."""
        exp = get_relevant_experience("P4", max_entries=2, max_chars=1500)
        return f"\n\n# 历次写作经验参考\n{exp}" if exp else ""

    def run(self) -> dict:
        """
        完整 P4 流程：
        1. 发现可用图片
        2. 逐章生成内容
        3. 组装 LaTeX 全文（使用改进模板）
        4. 保存 .tex 文件
        5. 写入 context_store
        """
        ctx = load_context()
        PAPER_DIR.mkdir(parents=True, exist_ok=True)

        available_figures = self._discover_figures()
        if available_figures:
            print(f"[P4] 发现 {len(available_figures)} 张图片可引用")

        experience_hint = self._get_experience_hint()
        sections_content: dict[str, str] = {}

        for idx, section in enumerate(SECTIONS):
            print(f"[P4] 生成章节: {section} ...")
            # Inject experience hint into abstract and introduction only
            hint = experience_hint if idx < 2 else ""
            content = self.write_section(section, ctx, available_figures, hint)
            sections_content[section] = content
            (PAPER_DIR / f"{section}.tex").write_text(content, encoding="utf-8")

        # Generate strengths/weaknesses
        print("[P4] 生成 Strengths & Weaknesses ...")
        model_name = ctx["modeling"].get("primary_model", {}).get("model_name", "the model")
        strengths = call_model(
            "列出模型的3-4个优势，用 LaTeX itemize 格式。",
            f"模型: {model_name}\n结果: {ctx.get('results', {}).get('solver_stdout', '')[:500]}",
            task="writing",
        )
        weaknesses = call_model(
            "列出模型的2-3个局限性，用 LaTeX itemize 格式。",
            f"模型: {model_name}",
            task="writing",
        )

        # Determine title
        problem_label = ctx["competition"].get("selected_problem", "X")
        title = f"MCM/ICM Problem {problem_label} — {model_name}"

        # Assemble full document using improved template
        tex = Template(MCM_LATEX_TEMPLATE).safe_substitute(
            title=title,
            abstract=sections_content.get("abstract", ""),
            introduction=sections_content.get("introduction", ""),
            assumptions=sections_content.get("assumptions", ""),
            model_formulation=sections_content.get("model_formulation", ""),
            solution=sections_content.get("solution", ""),
            results_analysis=sections_content.get("results_analysis", ""),
            sensitivity=sections_content.get("sensitivity", ""),
            strengths=strengths,
            weaknesses=weaknesses,
            conclusion=sections_content.get("conclusion", ""),
        )

        # Copy generated figures to paper/figures/
        fig_src = OUTPUT_DIR / "figures"
        fig_dst = PAPER_DIR / "figures"
        fig_dst.mkdir(parents=True, exist_ok=True)
        if fig_src.exists():
            for img in fig_src.glob("*.*"):
                if img.suffix.lower() in (".png", ".pdf", ".jpg", ".eps"):
                    shutil.copy2(img, fig_dst / img.name)
                    print(f"[P4] 复制图片: {img.name}")

        main_tex = PAPER_DIR / "main.tex"
        main_tex.write_text(tex, encoding="utf-8")

        bib = sections_content.get("references", "")
        (PAPER_DIR / "references.bib").write_text(bib, encoding="utf-8")

        ctx["phase"] = "P4_complete"
        ctx.setdefault("paper", {})["sections"] = {k: str(PAPER_DIR / f"{k}.tex") for k in SECTIONS}
        ctx["paper"]["bibtex"] = str(PAPER_DIR / "references.bib")
        ctx["paper"]["pdf_path"] = str(PAPER_DIR / "main.tex")

        save_context(ctx)
        return ctx
