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
from agents.model_compare import ModelCompareAgent
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
from agents.pipeline import PhaseOutcome, PhaseSpec, PipelineRunner
from agents import events
from agents.extensions import load_all as load_extensions

BASE_DIR = Path(__file__).resolve().parent
QUESTIONTEST_DIR = Path(os.getenv("QUESTIONTEST_DIR", BASE_DIR / "questiontest"))
PAPER_DIR = Path(os.getenv("PAPER_DIR", BASE_DIR / "paper"))
VOL_HOST = Path(os.getenv("VOL_HOST", BASE_DIR / "vol"))

MAX_ROLLBACKS = 2


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


def _build_registry(selected_problem: str | None) -> list[PhaseSpec]:
    """Build phase registry. Closures capture selected_problem for P1."""

    def p0b(ctx: dict) -> PhaseOutcome:
        try:
            run_pdf_stage()
            return PhaseOutcome(ctx=ctx, note="PDF 转译完成")
        except FileNotFoundError as e:
            return PhaseOutcome(ctx=ctx, note=f"SKIP: {e}（如已有 Markdown，可从 P1 开始）")

    def p1(ctx: dict) -> PhaseOutcome:
        new_ctx = QuestionExtractor().run(selected_problem=selected_problem)
        selected = new_ctx["competition"].get("selected_problem", "?")
        kw = new_ctx["competition"].get("keywords", [])
        note = f"选题: {selected}，关键词: {', '.join(kw[:5])}"
        return PhaseOutcome(ctx=new_ctx, note=note)

    def p1_5(ctx: dict) -> PhaseOutcome:
        new_ctx = DataCleaningAgent().run()
        dc = new_ctx.get("data_cleaning", {})
        results = dc.get("results", {})
        success = sum(1 for r in results.values() if r.get("status") == "success")
        figs = dc.get("all_figures", [])
        note = f"数据清洗: {success}/{len(results)} 文件成功" + (f"，EDA 图片 {len(figs)} 张" if figs else "")
        return PhaseOutcome(ctx=new_ctx, note=note)

    def p2(ctx: dict) -> PhaseOutcome:
        new_ctx = ModelingAgent().run()
        model = new_ctx["modeling"].get("primary_model", {})
        validation = new_ctx["modeling"].get("validation", {})
        note = (
            f"模型: {model.get('model_name', '未知')}; "
            f"类型: {new_ctx['modeling'].get('model_type', '未知')}; "
            f"验证: {validation.get('overall', '未知')}"
        )
        return PhaseOutcome(ctx=new_ctx, note=note)

    def p2_8(ctx: dict) -> PhaseOutcome:
        new_ctx = ModelCompareAgent().run()
        cmp = new_ctx.get("modeling", {}).get("comparison_v2", {})
        n = len(cmp.get("candidates", []))
        winner = cmp.get("winner_id", "?")
        method = cmp.get("method", "?")
        note = f"候选 {n} 个, winner={winner} ({method})" if n else "无候选模型，跳过"
        return PhaseOutcome(ctx=new_ctx, note=note)

    def p2_5(ctx: dict) -> PhaseOutcome:
        viz_result = MatlabVizAgent().run(ctx=ctx)
        n_viz = len(viz_result.get("figures", []))
        return PhaseOutcome(ctx=load_context(), note=f"数学可视化图片: {n_viz} 张")

    def p2_7(ctx: dict) -> PhaseOutcome:
        r3 = Viz3DAgent().run(ctx=ctx)
        n_png = len(r3.get("figures", []))
        n_html = len(r3.get("html", []))
        return PhaseOutcome(ctx=load_context(), note=f"3D 图片: {n_png} 张, 交互 HTML: {n_html} 份")

    def p3(ctx: dict) -> PhaseOutcome:
        new_ctx = CodeAgent().run()
        if new_ctx.get("phase") == "P3_logic_err":
            return PhaseOutcome(
                ctx=new_ctx,
                rollback_to="P2",
                skip_record=True,
                note="检测到逻辑错误，回滚到 P2 重新建模",
            )
        artifacts = new_ctx.get("code_execution", {}).get("artifacts", [])
        return PhaseOutcome(ctx=new_ctx, note=f"产物: {artifacts}")

    def p3_5(ctx: dict) -> PhaseOutcome:
        result = DataValidator().run_pre_write_gate()
        if not result["valid"] and result["rollback_to"]:
            return PhaseOutcome(
                ctx=ctx,
                rollback_to=result["rollback_to"],
                skip_record=True,
                note=f"数据完整性不通过 → {result['rollback_to']}",
            )
        return PhaseOutcome(ctx=ctx, skip_record=True, note="数据完整性检查通过")

    def p4(ctx: dict) -> PhaseOutcome:
        new_ctx = WritingAgent().run()
        return PhaseOutcome(ctx=new_ctx, note=f"论文已生成: {BASE_DIR / 'paper' / 'main.tex'}")

    def p4_5(ctx: dict) -> PhaseOutcome:
        new_ctx = LatexCheckAgent().run()
        status = new_ctx.get("latex_check", {}).get("status", "unknown")
        note = "LaTeX 仍有 critical 错误" if status == "fail" else "LaTeX 语法检查通过"
        return PhaseOutcome(ctx=new_ctx, skip_record=True, note=note)

    def p5(ctx: dict) -> PhaseOutcome:
        new_ctx = ReviewAgent().run()
        suggestions = new_ctx.get("review", {}).get("suggestions", [])
        return PhaseOutcome(ctx=new_ctx, note=f"审校完成，建议数: {len(suggestions)}")

    def p5_5(ctx: dict) -> PhaseOutcome:
        result = DataValidator().run_post_review_gate()
        if not result["valid"] and result["rollback_to"]:
            for iss in result["issues"][:5]:
                print(f"  - {iss}")
            return PhaseOutcome(
                ctx=ctx,
                rollback_to=result["rollback_to"],
                skip_record=True,
                note=f"论文数据完整性不通过 → {result['rollback_to']}（论文内容缺乏真实数据支撑）",
            )
        return PhaseOutcome(ctx=ctx, note="论文数据完整性检查通过")

    return [
        PhaseSpec(name="P0b", run=p0b, on_error="skip", description="PDF → Markdown"),
        PhaseSpec(name="P1", run=p1, record_experience=True, description="题目解析 + 三手分发"),
        PhaseSpec(name="P1.5", run=p1_5, record_experience=True, description="数据清洗 + EDA"),
        PhaseSpec(name="P2", run=p2, record_experience=True, description="数学建模"),
        PhaseSpec(name="P2.8", run=p2_8, on_error="skip", description="多模型对比（LLM + 指标）"),
        PhaseSpec(name="P2.5", run=p2_5, on_error="skip", description="MATLAB 风格可视化"),
        PhaseSpec(name="P2.7", run=p2_7, on_error="skip", description="3D 建模（PyVista + Plotly + Octave）"),
        PhaseSpec(name="P3", run=p3, record_experience=True, description="代码求解"),
        PhaseSpec(name="P3.5", run=p3_5, description="pre-write 数据门"),
        PhaseSpec(name="P4", run=p4, record_experience=True, description="LaTeX 论文撰写"),
        PhaseSpec(name="P4.5", run=p4_5, description="LaTeX 语法检查"),
        PhaseSpec(name="P5", run=p5, record_experience=True, description="审校"),
        PhaseSpec(name="P5.5", run=p5_5, record_experience=True, description="post-review 数据门"),
    ]


def run_pipeline(start_phase: str = "P0b", selected_problem: str | None = None) -> dict:
    """Run full MCM workflow from a chosen phase via data-driven PipelineRunner."""
    run_startup_check()
    load_extensions()
    ctx = load_context()
    events.reset()
    events.emit("pipeline_start", start=start_phase, selected_problem=selected_problem)

    registry = _build_registry(selected_problem)
    runner = PipelineRunner(
        registry=registry,
        max_rollbacks=MAX_ROLLBACKS,
        record_experience_fn=record_experience,
    )
    runner.on_phase_start = lambda name, ctx, dt: events.emit("phase_start", phase=name)
    runner.on_phase_end = lambda name, ctx, dt: events.emit(
        "phase_end", phase=name, duration=dt, current_phase=ctx.get("phase")
    )
    runner.on_rollback = lambda frm, to, idx: events.emit(
        "rollback", from_phase=frm, to_phase=to, index=idx
    )
    result = runner.run(ctx, start=start_phase)

    events.emit(
        "pipeline_end",
        rollbacks=result.rollback_count,
        errors=list(result.errors.keys()),
        timings=result.timings,
    )

    print(f"\n{'=' * 50}")
    print(f"  全流程完成 (回滚次数: {result.rollback_count})")
    print(f"{'=' * 50}")
    paper = BASE_DIR / "paper"
    print(f"论文:         {paper / 'main.tex'}")
    print(f"LaTeX检查:    {paper / 'latex_check_report.json'}")
    print(f"参考文献:     {paper / 'references_draft.bib'}")
    print(f"审校报告:     {paper / 'review_report.json'}")
    print(f"Context:      {BASE_DIR / 'context_store' / 'context.json'}")

    if result.errors:
        print("\n阶段错误:")
        for name, msg in result.errors.items():
            print(f"  [{name}] {msg}")

    get_recorder().print_summary()
    return result.ctx


if __name__ == "__main__":
    import argparse
    import sys

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
        choices=["P0b", "P1", "P1.5", "P2", "P2.8", "P2.5", "P2.7", "P3", "P3.5", "P4", "P4.5", "P5", "P5.5"],
        help="起始阶段，默认 P0b",
    )
    parser.add_argument(
        "--problem",
        default=None,
        help="强制选题，如 A/B/C（可选）",
    )
    args = parser.parse_args()

    run_pipeline(start_phase=args.start, selected_problem=args.problem)
