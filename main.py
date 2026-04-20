from __future__ import annotations

from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from agents.orchestrator import load_context
from agents.pdf_agent import PdfAgent
from agents.question_extractor import QuestionExtractor
from agents.data_cleaning_agent import DataCleaningAgent
from agents.modeling_agent import ModelingAgent
from agents.matlab_viz import MatlabVizAgent
from agents.viz3d import Viz3DAgent
from agents.code_agent import CodeAgent
from agents.writing_agent import WritingAgent
from agents.latex_check_agent import LatexCheckAgent
from agents.review_agent import ReviewAgent
from agents.data_validator import DataValidator
from agents.data_recorder import get_recorder
from agents.experience_recorder import record_experience
from agents.llm_checker import run_startup_check

BASE_DIR = Path(__file__).resolve().parent
QUESTIONTEST_DIR = Path(os.getenv("QUESTIONTEST_DIR", BASE_DIR / "questiontest"))


PAPER_DIR = Path(os.getenv("PAPER_DIR", BASE_DIR / "paper"))
VOL_HOST = Path(os.getenv("VOL_HOST", BASE_DIR / "vol"))


def _find_problem_pdfs(base_dir: Path) -> list[Path]:
    """Recursively find problem PDFs and skip attachment folders."""
    if not base_dir.exists():
        return []
    return [p for p in base_dir.rglob("*.pdf") if "附件" not in p.as_posix()]


def run_pdf_stage() -> list[Path]:
    """P0b: convert all problem PDFs under questiontest/ into translation/*.md."""
    pdfs = sorted(_find_problem_pdfs(QUESTIONTEST_DIR), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdfs:
        raise FileNotFoundError(
            f"questiontest/ 中没有 PDF 文件，请将竞赛题目 PDF 放入 {QUESTIONTEST_DIR}"
        )

    agent = PdfAgent()
    print(f"\n[P0b] 发现 {len(pdfs)} 个赛题 PDF，开始全部转译...")
    md_paths = agent.run()
    print(f"[P0b-OK] 已生成 {len(md_paths)} 个 Markdown")
    for path in md_paths:
        print(f"  - {path}")
    return md_paths


MAX_ROLLBACKS = 2  # Prevent infinite rollback loops


def run_pipeline(start_phase: str = "P0b", selected_problem: str | None = None) -> dict:
    """Run full MCM workflow from a chosen phase, with rollback on data integrity failure."""
    run_startup_check()
    ctx = load_context()
    phase_order = ["P0b", "P1", "P1.5", "P2", "P2.5", "P2.7", "P3", "P3.5", "P4", "P4.5", "P5", "P5.5"]
    start_idx = phase_order.index(start_phase) if start_phase in phase_order else 0
    rollback_count = 0

    i = start_idx
    while i < len(phase_order):
        phase = phase_order[i]
        print(f"\n{'=' * 50}")
        print(f"  开始 {phase}")
        print(f"{'=' * 50}")

        if phase == "P0b":
            try:
                run_pdf_stage()
            except FileNotFoundError as e:
                print(f"[P0b-SKIP] {e}")
                print("[P0b-SKIP] 如已有 Markdown，可直接从 P1 开始")

        elif phase == "P1":
            extractor = QuestionExtractor()
            ctx = extractor.run(selected_problem=selected_problem)
            selected = ctx["competition"].get("selected_problem", "?")
            kw = ctx["competition"].get("keywords", [])
            print(f"[P1-OK] 选题: {selected}，关键词: {', '.join(kw[:5])}")
            record_experience("P1")

        elif phase == "P1.5":
            # ── Data cleaning + EDA ──
            cleaner = DataCleaningAgent()
            ctx = cleaner.run()
            dc = ctx.get("data_cleaning", {})
            results = dc.get("results", {})
            success = sum(1 for r in results.values() if r.get("status") == "success")
            print(f"[P1.5-OK] 数据清洗: {success}/{len(results)} 文件成功")
            figs = dc.get("all_figures", [])
            if figs:
                print(f"[P1.5-OK] EDA 图片: {len(figs)} 张")
            record_experience("P1.5")

        elif phase == "P2":
            agent = ModelingAgent()
            ctx = agent.run()
            model = ctx["modeling"].get("primary_model", {})
            print(f"[P2-OK] 模型: {model.get('model_name', '未知')}")
            print(f"[P2-OK] 类型: {ctx['modeling'].get('model_type', '未知')}")
            validation = ctx["modeling"].get("validation", {})
            print(f"[P2-OK] 验证: {validation.get('overall', '未知')}")
            record_experience("P2")

        elif phase == "P2.5":
            # ── MATLAB-style mathematical visualization ──
            try:
                viz_agent = MatlabVizAgent()
                viz_result = viz_agent.run(ctx=ctx)
                n_viz = len(viz_result.get("figures", []))
                print(f"[P2.5-OK] 数学可视化图片: {n_viz} 张")
                ctx = load_context()  # reload after matlab_viz saves context
            except Exception as e:
                print(f"[P2.5-SKIP] 数学可视化失败 (非阻断): {e}")

        elif phase == "P2.7":
            # ── 3D modeling (PyVista + Plotly + optional Octave) ──
            try:
                viz3d = Viz3DAgent()
                r3 = viz3d.run(ctx=ctx)
                n_png = len(r3.get("figures", []))
                n_html = len(r3.get("html", []))
                print(f"[P2.7-OK] 3D 图片: {n_png} 张, 交互 HTML: {n_html} 份")
                ctx = load_context()
            except Exception as e:
                print(f"[P2.7-SKIP] 3D 可视化失败 (非阻断): {e}")

        elif phase == "P3":
            try:
                agent = CodeAgent()
                ctx = agent.run()
            except Exception as e:
                print(f"[P3-ERR] 代码阶段失败: {e}")
                print("[P3-ERR] 已停止后续阶段，请检查日志后重试")
                return ctx
            if ctx.get("phase") == "P3_logic_err":
                print("[P3-WARN] 检测到逻辑错误，需要回到 P2 重新建模")
                if rollback_count < MAX_ROLLBACKS:
                    rollback_count += 1
                    i = phase_order.index("P2")
                    print(f"[ROLLBACK] → P2 (第 {rollback_count} 次回滚)")
                    continue
                print("[P3-WARN] 已达最大回滚次数，停止流程")
                return ctx
            print(f"[P3-OK] 产物: {ctx.get('code_execution', {}).get('artifacts', [])}")
            record_experience("P3")

        elif phase == "P3.5":
            # ── Pre-write data gate ──
            validator = DataValidator()
            result = validator.run_pre_write_gate()

            if not result["valid"] and result["rollback_to"]:
                target = result["rollback_to"]
                if rollback_count < MAX_ROLLBACKS and target in phase_order:
                    rollback_count += 1
                    i = phase_order.index(target)
                    print(f"[ROLLBACK] 数据完整性不通过 → {target} (第 {rollback_count} 次回滚)")
                    continue
                print(f"[WARN] 数据检查不通过但已达最大回滚次数，继续写作")

        elif phase == "P4":
            agent = WritingAgent()
            ctx = agent.run()
            print(f"[P4-OK] 论文已生成: {BASE_DIR / 'paper' / 'main.tex'}")
            record_experience("P4")

        elif phase == "P4.5":
            # ── LaTeX syntax check & fix ──
            checker = LatexCheckAgent()
            ctx = checker.run()
            status = ctx.get("latex_check", {}).get("status", "unknown")
            if status == "fail":
                print("[P4.5-WARN] LaTeX 仍有 critical 错误，论文可能无法编译")
            else:
                print("[P4.5-OK] LaTeX 语法检查通过")

        elif phase == "P5":
            agent = ReviewAgent()
            ctx = agent.run()
            suggestions = ctx.get("review", {}).get("suggestions", [])
            print(f"[P5-OK] 审校完成，建议数: {len(suggestions)}")
            record_experience("P5")

        elif phase == "P5.5":
            # ── Post-review data integrity check ──
            validator = DataValidator()
            result = validator.run_post_review_gate()

            if not result["valid"] and result["rollback_to"]:
                target = result["rollback_to"]
                if rollback_count < MAX_ROLLBACKS and target in phase_order:
                    rollback_count += 1
                    i = phase_order.index(target)
                    print(f"[ROLLBACK] 论文数据完整性不通过 → {target} (第 {rollback_count} 次回滚)")
                    print(f"  原因: 论文内容缺乏真实数据支撑")
                    for iss in result["issues"][:5]:
                        print(f"  - {iss}")
                    continue
                if not result["valid"]:
                    print("[WARN] 数据审计不通过但已达最大回滚次数，输出当前结果")
            if result.get("valid"):
                record_experience("P5.5")

        i += 1

    print(f"\n{'=' * 50}")
    print(f"  全流程完成 (回滚次数: {rollback_count})")
    print(f"{'=' * 50}")
    paper = BASE_DIR / "paper"
    print(f"论文:         {paper / 'main.tex'}")
    print(f"LaTeX检查:    {paper / 'latex_check_report.json'}")
    print(f"参考文献:     {paper / 'references_draft.bib'}")
    print(f"审校报告:     {paper / 'review_report.json'}")
    print(f"Context:      {BASE_DIR / 'context_store' / 'context.json'}")

    # Print token usage & cost summary
    recorder = get_recorder()
    recorder.print_summary()

    return ctx


if __name__ == "__main__":
    import argparse
    import sys

    # ── 特殊命令：不走 argparse，直接处理 ──
    _cmd = sys.argv[1].lstrip("/").lower() if len(sys.argv) > 1 else ""
    if _cmd == "override_model":
        from agents.model_override import run_override_cli
        run_override_cli()
        sys.exit(0)
    if _cmd == "checkllm":
        from agents.llm_checker import run_check_cli
        run_check_cli()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="MCM/ICM 全流程 MAS",
        epilog=(
            "特殊命令：\n"
            "  python main.py /override_model  — 交互式覆盖各阶段模型\n"
            "  python main.py /checkLLM        — 检查所有路由模型可用性"
        ),
    )
    parser.add_argument(
        "--start",
        default="P0b",
        choices=["P0b", "P1", "P1.5", "P2", "P2.5", "P2.7", "P3", "P3.5", "P4", "P4.5", "P5", "P5.5"],
        help="起始阶段，默认 P0b（即从 PDF 转换开始）",
    )
    parser.add_argument(
        "--problem",
        default=None,
        help="强制选题，如 A/B/C（可选，默认由 AI 推荐）",
    )
    args = parser.parse_args()

    run_pipeline(start_phase=args.start, selected_problem=args.problem)
