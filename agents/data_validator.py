"""Data Validator — checks that paper content is backed by real computation data.

Performs integrity checks at two points:
1. Pre-write gate (before P4): ensures P3 produced real results
2. Post-review gate (during P5): ensures paper doesn't contain fabricated data

If validation fails, returns a rollback target phase so the pipeline can retry.
"""

from __future__ import annotations

import json
import re
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import parse_json as _parse_json


BASE_DIR = Path(__file__).resolve().parent.parent
VOL_DIR = Path(os.getenv("VOL_DIR", BASE_DIR / "vol"))
PAPER_DIR = Path(os.getenv("PAPER_DIR", BASE_DIR / "paper"))

# Patterns that indicate fabricated/placeholder data in LaTeX
FABRICATION_PATTERNS = [
    (r"(?:placeholder|lorem ipsum|TODO|FIXME|TBD|xxx|yyy|待填)", "占位符文本"),
    (r"(?:假设|假定).*(?:结果|数据).*(?:为|是)\s*\d", "假设性数据声明"),
    (r"\\textbf\{[?\?]+\}", "问号占位符"),
    (r"(?:如图|如表).*(?:所示|可见).*(?:但|然而).*(?:无|没有|缺少)", "引用了不存在的图表"),
]

# Minimum thresholds for real data
MIN_STDOUT_LENGTH = 50        # solver output should have substance
MIN_ARTIFACTS_COUNT = 0       # at least some output files (relaxed: figures optional)
MIN_FIGURES_FOR_RESULTS = 0   # figures are encouraged but not strictly required

SYSTEM_DATA_AUDIT = """你是一位严格的数据审计员。审查以下论文内容，判断其中的数据和结果是否有真实计算支撑。

检查项：
1. 论文中引用的数值结果是否可能来自真实计算（而非编造）
2. 图表引用是否对应实际存在的文件
3. 是否存在含糊的定性描述替代应有的定量结果
4. 数学公式推导后是否有对应的数值验证

输出严格 JSON（不含 markdown 代码块），结构：
{
  "data_integrity": "pass|fail|warning",
  "fabrication_risks": [
    {"location": "章节或段落", "issue": "描述", "severity": "critical|warning", "evidence": "原文引用"}
  ],
  "missing_data": ["缺失的数据项1", "缺失的数据项2"],
  "recommendation": "pass|rollback_to_P3|rollback_to_P2",
  "reason": "判断理由"
}"""


class DataValidator:
    """Validates that pipeline outputs are backed by real data."""

    # ── Pre-write gate (before P4) ──

    def validate_pre_write(self, ctx: dict) -> dict:
        """Check that P3 produced real computation results before writing.

        Returns:
            {"valid": bool, "rollback_to": str|None, "issues": list[str]}
        """
        issues: list[str] = []

        # Check solver stdout
        solver_stdout = ctx.get("results", {}).get("solver_stdout", "")
        if not solver_stdout or len(solver_stdout.strip()) < MIN_STDOUT_LENGTH:
            issues.append(
                f"求解器输出过短或为空 (长度={len(solver_stdout.strip())}), "
                f"最低要求 {MIN_STDOUT_LENGTH} 字符"
            )

        # Check for fallback markers
        if "fallback" in solver_stdout.lower() or "baseline_solver" in solver_stdout.lower():
            issues.append("检测到 fallback 求解器输出，非真实计算结果")

        # Check per-step results
        per_step = ctx.get("results", {}).get("per_step_results", {})
        if not per_step:
            issues.append("缺少分步求解结果 (per_step_results 为空)")
        else:
            logic_errors = [
                k for k, v in per_step.items()
                if isinstance(v, str) and "LOGIC_ERROR" in v
            ]
            if logic_errors:
                issues.append(f"以下步骤存在逻辑错误: {', '.join(logic_errors)}")

            # Check that at least some steps have real output
            real_steps = [
                k for k, v in per_step.items()
                if isinstance(v, str) and len(v.strip()) > 20 and "LOGIC_ERROR" not in v
            ]
            if not real_steps:
                issues.append("所有步骤的输出均无效或过短")

        # Check code_execution status
        exec_status = ctx.get("code_execution", {}).get("status", "")
        if exec_status in ("failed", "logic_err"):
            issues.append(f"代码执行状态异常: {exec_status}")

        # Check artifacts
        artifacts = ctx.get("code_execution", {}).get("artifacts", [])
        existing_artifacts = [a for a in artifacts if Path(a).exists()]

        # Check figures directory
        fig_dir = VOL_DIR / "outputs" / "figures"
        actual_figures = list(fig_dir.glob("*.*")) if fig_dir.exists() else []

        # Determine rollback target
        has_critical = any(
            "fallback" in iss or "所有步骤" in iss or "代码执行状态" in iss
            for iss in issues
        )
        has_no_results = not solver_stdout.strip() or len(solver_stdout.strip()) < 10

        rollback_to = None
        if has_no_results:
            rollback_to = "P2"  # No results at all → re-model
        elif has_critical:
            rollback_to = "P3"  # Bad results → re-code

        return {
            "valid": len(issues) == 0,
            "rollback_to": rollback_to,
            "issues": issues,
            "artifacts_count": len(existing_artifacts),
            "figures_count": len(actual_figures),
        }

    # ── Post-review gate (during/after P5) ──

    def validate_paper_content(self, ctx: dict) -> dict:
        """Deep check: does the paper content match actual computation outputs?

        Returns:
            {"valid": bool, "rollback_to": str|None, "issues": list, "audit": dict}
        """
        issues: list[str] = []

        # 1. Static pattern scan on paper text
        tex_files = {}
        if PAPER_DIR.exists():
            for tex in PAPER_DIR.glob("*.tex"):
                tex_files[tex.name] = tex.read_text(encoding="utf-8")

        all_text = "\n".join(tex_files.values())

        for pattern, desc in FABRICATION_PATTERNS:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            if matches:
                issues.append(f"检测到可疑内容 ({desc}): 共 {len(matches)} 处")

        # 2. Check figure references vs actual files
        fig_refs = re.findall(r"\\includegraphics.*?\{([^}]+)\}", all_text)
        fig_dir = PAPER_DIR / "figures"
        for ref in fig_refs:
            fig_name = Path(ref).name
            if not (fig_dir / fig_name).exists():
                # Also check without extension
                candidates = list(fig_dir.glob(f"{Path(fig_name).stem}.*")) if fig_dir.exists() else []
                if not candidates:
                    issues.append(f"论文引用了不存在的图片: {ref}")

        # 3. Cross-check: paper claims numerical results, but solver had no real output
        solver_stdout = ctx.get("results", {}).get("solver_stdout", "")
        if len(solver_stdout.strip()) < MIN_STDOUT_LENGTH:
            # Check if paper contains specific numbers
            numbers_in_paper = re.findall(
                r"(?:结果|值|为|=)\s*[\d.]+", all_text
            )
            if len(numbers_in_paper) > 3:
                issues.append(
                    f"论文包含 {len(numbers_in_paper)} 处定量结果，但求解器输出不足 — "
                    f"数据可能是编造的"
                )

        # 4. LLM audit on key sections (results + sensitivity)
        audit_result = {}
        sections_to_audit = ["results_analysis", "sensitivity", "solution"]
        audit_text = ""
        for section in sections_to_audit:
            fname = f"{section}.tex"
            if fname in tex_files:
                audit_text += f"\n=== {section} ===\n{tex_files[fname][:2000]}\n"

        if audit_text:
            user_prompt = (
                f"论文关键章节：\n{audit_text}\n\n"
                f"实际求解器输出摘要：\n{solver_stdout[:1500]}\n\n"
                f"实际图片文件：{[f.name for f in (fig_dir.iterdir() if fig_dir.exists() else [])]}"
            )
            audit_result = _parse_json(
                call_model(SYSTEM_DATA_AUDIT, user_prompt, task="review")
            )

            if audit_result.get("data_integrity") == "fail":
                fab_risks = audit_result.get("fabrication_risks", [])
                critical_risks = [r for r in fab_risks if r.get("severity") == "critical"]
                if critical_risks:
                    for risk in critical_risks:
                        issues.append(
                            f"[LLM审计] {risk.get('location', '?')}: {risk.get('issue', '?')}"
                        )

        # Determine rollback
        critical_count = sum(
            1 for iss in issues
            if "编造" in iss or "不存在" in iss or "LLM审计" in iss
        )

        rollback_to = None
        if critical_count >= 2:
            # Multiple data integrity failures → need real computation
            recommendation = audit_result.get("recommendation", "")
            if recommendation == "rollback_to_P2":
                rollback_to = "P2"
            else:
                rollback_to = "P3"
        elif critical_count == 1:
            rollback_to = "P4"  # Rewrite the paper with existing data

        return {
            "valid": critical_count == 0,
            "rollback_to": rollback_to,
            "issues": issues,
            "audit": audit_result,
            "critical_count": critical_count,
        }

    def run_pre_write_gate(self) -> dict:
        """Run pre-write validation and save to context."""
        ctx = load_context()
        result = self.validate_pre_write(ctx)

        print(f"\n[DATA-CHECK] Pre-write gate: {'PASS' if result['valid'] else 'FAIL'}")
        if result["issues"]:
            for iss in result["issues"]:
                print(f"  - {iss}")
        if result["rollback_to"]:
            print(f"  → 建议回滚到: {result['rollback_to']}")

        ctx.setdefault("data_validation", {})["pre_write"] = {
            "valid": result["valid"],
            "rollback_to": result["rollback_to"],
            "issues": result["issues"],
        }
        save_context(ctx)
        return result

    def run_post_review_gate(self) -> dict:
        """Run post-review validation and save to context."""
        ctx = load_context()
        result = self.validate_paper_content(ctx)

        print(f"\n[DATA-CHECK] Post-review gate: {'PASS' if result['valid'] else 'FAIL'}")
        if result["issues"]:
            for iss in result["issues"]:
                print(f"  - {iss}")
        if result["rollback_to"]:
            print(f"  → 建议回滚到: {result['rollback_to']}")

        ctx.setdefault("data_validation", {})["post_review"] = {
            "valid": result["valid"],
            "rollback_to": result["rollback_to"],
            "issues": result["issues"],
            "critical_count": result["critical_count"],
        }
        save_context(ctx)
        return result
