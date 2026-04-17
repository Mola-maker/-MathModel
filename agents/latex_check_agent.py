"""LaTeX Check Agent — P4.5: validate LaTeX syntax, fix errors, ensure compilability.

Sits between P4 (writing) and P5 (review). Performs:
1. Structural validation (matched begin/end, braces, math delimiters)
2. Common error detection (double backslash issues, bad commands, encoding)
3. Auto-fix for simple issues
4. LLM-assisted fix for complex issues
5. Outputs check report to paper/latex_check_report.json
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import parse_json as _parse_json

_BASE = Path(__file__).resolve().parent.parent
PAPER_DIR = Path(os.getenv("PAPER_DIR", str(_BASE / "paper")))

# Maximum LLM fix attempts per file
MAX_FIX_ROUNDS = 3

SYSTEM_LATEX_FIX = """你是一位 LaTeX 排版专家。你的任务是修复 LaTeX 代码中的语法错误。

规则：
1. 只修复语法错误，不改变内容含义
2. 确保所有 \\begin{} 和 \\end{} 配对
3. 确保所有大括号 {} 配对
4. 确保数学模式 $ 和 $$ 配对
5. 修复常见错误：多余的 \\\\、缺少 & 的表格行、错误的命令名
6. 保持原始格式和缩进风格
7. 如果代码没有语法错误，原样返回

输出格式：直接输出修复后的完整 LaTeX 代码，不要加 markdown 代码块。"""

SYSTEM_LATEX_VALIDATE = """你是 LaTeX 语法检查器。检查以下 LaTeX 代码片段的语法正确性。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "errors": [
    {"line": 行号或0, "type": "错误类型", "message": "描述", "severity": "critical|warning|minor", "fixable": true}
  ],
  "is_valid": true或false,
  "summary": "总体评价"
}

检查项：
- begin/end 环境配对
- 大括号配对
- 数学模式定界符配对
- 表格/矩阵列数一致性
- 命令参数完整性
- 引用/标签格式
- 特殊字符转义"""


class LatexCheckAgent:
    """P4.5 LaTeX 语法检查与修复。"""

    def _load_tex_files(self) -> dict[str, str]:
        """Load all .tex files from paper directory."""
        files: dict[str, str] = {}
        if not PAPER_DIR.exists():
            return files
        for tex in sorted(PAPER_DIR.glob("*.tex")):
            files[tex.name] = tex.read_text(encoding="utf-8")
        return files

    # ── Static checks (no LLM needed) ──

    def _check_brace_balance(self, content: str) -> list[dict]:
        """Check that curly braces are balanced."""
        issues: list[dict] = []
        depth = 0
        for i, ch in enumerate(content):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    line_no = content[:i].count("\n") + 1
                    issues.append({
                        "line": line_no,
                        "type": "unmatched_brace",
                        "message": f"多余的右大括号 '}}' (位置 {i})",
                        "severity": "critical",
                        "fixable": False,
                    })
                    depth = 0
        if depth > 0:
            issues.append({
                "line": 0,
                "type": "unmatched_brace",
                "message": f"缺少 {depth} 个右大括号 '}}'",
                "severity": "critical",
                "fixable": False,
            })
        return issues

    def _check_env_balance(self, content: str) -> list[dict]:
        """Check that \\begin{env} and \\end{env} are matched."""
        issues: list[dict] = []
        begins = re.findall(r"\\begin\{(\w+)\}", content)
        ends = re.findall(r"\\end\{(\w+)\}", content)

        begin_counts: dict[str, int] = {}
        end_counts: dict[str, int] = {}
        for env in begins:
            begin_counts[env] = begin_counts.get(env, 0) + 1
        for env in ends:
            end_counts[env] = end_counts.get(env, 0) + 1

        all_envs = set(begin_counts) | set(end_counts)
        for env in all_envs:
            b = begin_counts.get(env, 0)
            e = end_counts.get(env, 0)
            if b > e:
                issues.append({
                    "line": 0,
                    "type": "unmatched_env",
                    "message": f"\\begin{{{env}}} 比 \\end{{{env}}} 多 {b - e} 个",
                    "severity": "critical",
                    "fixable": True,
                })
            elif e > b:
                issues.append({
                    "line": 0,
                    "type": "unmatched_env",
                    "message": f"\\end{{{env}}} 比 \\begin{{{env}}} 多 {e - b} 个",
                    "severity": "critical",
                    "fixable": True,
                })
        return issues

    def _check_math_delimiters(self, content: str) -> list[dict]:
        """Check that $ and $$ math delimiters are balanced."""
        issues: list[dict] = []

        # Remove escaped dollars \$
        cleaned = content.replace("\\$", "")

        # Check $$ pairs
        dd_count = cleaned.count("$$")
        if dd_count % 2 != 0:
            issues.append({
                "line": 0,
                "type": "unmatched_math",
                "message": "display math $$ 定界符未配对（奇数个 $$）",
                "severity": "critical",
                "fixable": True,
            })

        # Check single $ pairs (after removing $$)
        no_dd = cleaned.replace("$$", "")
        single_count = no_dd.count("$")
        if single_count % 2 != 0:
            issues.append({
                "line": 0,
                "type": "unmatched_math",
                "message": "inline math $ 定界符未配对（奇数个 $）",
                "severity": "warning",
                "fixable": True,
            })

        return issues

    def _check_common_errors(self, content: str) -> list[dict]:
        """Detect common LaTeX mistakes."""
        issues: list[dict] = []
        lines = content.split("\n")

        for i, line in enumerate(lines, 1):
            # Empty \cite{} or \ref{}
            if re.search(r"\\(cite|ref|label)\{\s*\}", line):
                issues.append({
                    "line": i,
                    "type": "empty_reference",
                    "message": "空的引用/标签命令",
                    "severity": "warning",
                    "fixable": False,
                })

            # Double backslash at end of last row in tabular (causes extra empty row)
            # This is a heuristic — flag but don't auto-fix
            if re.search(r"\\\\\s*\\end\{tabular", line):
                issues.append({
                    "line": i,
                    "type": "trailing_newline_tabular",
                    "message": "tabular 最后一行末尾多余的 \\\\",
                    "severity": "minor",
                    "fixable": True,
                })

            # \being instead of \begin (common typo)
            if "\\being{" in line:
                issues.append({
                    "line": i,
                    "type": "typo",
                    "message": "可能是 \\begin 的拼写错误：\\being",
                    "severity": "critical",
                    "fixable": True,
                })

            # Unescaped % in text (not comments)
            # Only flag if it looks like it's meant as a percent sign
            stripped = line.lstrip()
            if not stripped.startswith("%") and re.search(r"(?<!\\)\d+%", line):
                issues.append({
                    "line": i,
                    "type": "unescaped_percent",
                    "message": "数字后的 % 可能需要转义为 \\%",
                    "severity": "minor",
                    "fixable": True,
                })

            # Unescaped & outside tabular/align
            # (simplified: just flag bare & if not in known environments)
            if "&" in line and not re.search(r"\\&", line):
                # Only flag if we're not inside a tabular-like env — rough heuristic
                if not any(env in content for env in [
                    "\\begin{tabular", "\\begin{align", "\\begin{array",
                    "\\begin{matrix", "\\begin{cases}", "\\begin{split",
                ]):
                    pass  # Don't flag if any table-like env exists

        return issues

    def _check_required_structure(self, content: str) -> list[dict]:
        """Check that main.tex has basic required structure."""
        issues: list[dict] = []

        required = [
            (r"\\documentclass", "缺少 \\documentclass 声明"),
            (r"\\begin\{document\}", "缺少 \\begin{document}"),
            (r"\\end\{document\}", "缺少 \\end{document}"),
        ]

        for pattern, msg in required:
            if not re.search(pattern, content):
                issues.append({
                    "line": 0,
                    "type": "missing_structure",
                    "message": msg,
                    "severity": "critical",
                    "fixable": False,
                })

        return issues

    # ── Auto-fix simple issues ──

    def _auto_fix(self, content: str) -> tuple[str, list[str]]:
        """Apply automatic fixes for simple issues. Returns (fixed_content, fix_log)."""
        fixes: list[str] = []

        # Fix: \being → \begin
        if "\\being{" in content:
            content = content.replace("\\being{", "\\begin{")
            fixes.append("修正拼写错误：\\being → \\begin")

        # Fix: trailing \\ before \end{tabular}
        new_content = re.sub(
            r"\\\\\s*(\\end\{tabular)",
            r"\n\1",
            content,
        )
        if new_content != content:
            content = new_content
            fixes.append("移除 tabular 最后一行多余的 \\\\")

        # Fix: unescaped % after digits
        new_content = re.sub(r"(\d)%(?!\s*$)", r"\1\\%", content)
        if new_content != content:
            content = new_content
            fixes.append("转义数字后的 % 为 \\%")

        return content, fixes

    # ── LLM-assisted fix ──

    def _llm_fix(self, filename: str, content: str, issues: list[dict]) -> str:
        """Use LLM to fix complex LaTeX issues."""
        issues_desc = "\n".join(
            f"- [{iss['severity']}] 第{iss['line']}行: {iss['message']}"
            for iss in issues
            if iss["severity"] in ("critical", "warning")
        )

        # Only send truncated content to LLM
        max_len = 12000
        truncated = content[:max_len]
        if len(content) > max_len:
            truncated += "\n\n% ... [TRUNCATED] ...\n"
            # Also send the tail for \end{document} matching
            truncated += content[-2000:]

        user_prompt = (
            f"文件：{filename}\n"
            f"发现的问题：\n{issues_desc}\n\n"
            f"LaTeX 代码：\n{truncated}"
        )

        fixed = call_model(SYSTEM_LATEX_FIX, user_prompt, task="review")
        return fixed

    # ── LLM validation (for complex checks static analysis can't do) ──

    def _llm_validate(self, filename: str, content: str) -> list[dict]:
        """Use LLM to perform deep validation."""
        truncated = content[:8000]
        user_prompt = f"文件：{filename}\n\nLaTeX 代码：\n{truncated}"

        result = _parse_json(call_model(SYSTEM_LATEX_VALIDATE, user_prompt, task="review"))
        return result.get("errors", [])

    # ── Main entry ──

    def check_file(self, filename: str, content: str) -> dict:
        """Run all checks on a single file. Returns per-file report."""
        all_issues: list[dict] = []

        # Static checks
        all_issues.extend(self._check_brace_balance(content))
        all_issues.extend(self._check_env_balance(content))
        all_issues.extend(self._check_math_delimiters(content))
        all_issues.extend(self._check_common_errors(content))

        if filename == "main.tex":
            all_issues.extend(self._check_required_structure(content))

        # Auto-fix pass
        fixed_content, auto_fixes = self._auto_fix(content)
        auto_fixed = bool(auto_fixes)

        # If critical issues remain after auto-fix, try LLM fix
        critical_after_autofix = [
            iss for iss in all_issues
            if iss["severity"] == "critical"
        ]

        llm_fixed = False
        if critical_after_autofix:
            # Re-check after auto-fix
            recheck_issues = (
                self._check_brace_balance(fixed_content)
                + self._check_env_balance(fixed_content)
                + self._check_math_delimiters(fixed_content)
            )
            remaining_critical = [i for i in recheck_issues if i["severity"] == "critical"]

            if remaining_critical:
                print(f"  [P4.5] {filename}: {len(remaining_critical)} critical issues, calling LLM fix...")
                for attempt in range(MAX_FIX_ROUNDS):
                    fixed_content = self._llm_fix(filename, fixed_content, remaining_critical)

                    # Re-validate
                    recheck = (
                        self._check_brace_balance(fixed_content)
                        + self._check_env_balance(fixed_content)
                        + self._check_math_delimiters(fixed_content)
                    )
                    remaining_critical = [i for i in recheck if i["severity"] == "critical"]
                    if not remaining_critical:
                        llm_fixed = True
                        print(f"  [P4.5] {filename}: LLM 修复成功 (第 {attempt + 1} 轮)")
                        break
                else:
                    print(f"  [P4.5] {filename}: LLM 修复未能完全解决所有 critical 问题")

        # Final re-check on fixed content
        final_issues: list[dict] = []
        final_issues.extend(self._check_brace_balance(fixed_content))
        final_issues.extend(self._check_env_balance(fixed_content))
        final_issues.extend(self._check_math_delimiters(fixed_content))
        final_issues.extend(self._check_common_errors(fixed_content))
        if filename == "main.tex":
            final_issues.extend(self._check_required_structure(fixed_content))

        # Write back if any fixes were applied
        if auto_fixed or llm_fixed:
            (PAPER_DIR / filename).write_text(fixed_content, encoding="utf-8")

        critical_count = sum(1 for i in final_issues if i["severity"] == "critical")
        warning_count = sum(1 for i in final_issues if i["severity"] == "warning")

        return {
            "filename": filename,
            "issues_found": len(all_issues),
            "issues_remaining": len(final_issues),
            "critical_remaining": critical_count,
            "warning_remaining": warning_count,
            "auto_fixes": auto_fixes,
            "llm_fixed": llm_fixed,
            "remaining_issues": final_issues,
            "status": "pass" if critical_count == 0 else "fail",
        }

    def run(self) -> dict:
        """
        Complete P4.5 flow:
        1. Load all .tex files
        2. Run static checks + auto-fix + LLM fix on each
        3. Generate latex_check_report.json
        4. Update context_store
        """
        ctx = load_context()
        tex_files = self._load_tex_files()

        if not tex_files:
            print("[P4.5] 未找到 .tex 文件，跳过 LaTeX 检查")
            return ctx

        print(f"[P4.5] 检查 {len(tex_files)} 个 LaTeX 文件...")

        file_reports: list[dict] = []
        total_fixed = 0
        total_critical = 0

        for filename, content in tex_files.items():
            print(f"  [P4.5] 检查 {filename}...")
            report = self.check_file(filename, content)
            file_reports.append(report)
            total_fixed += len(report["auto_fixes"]) + (1 if report["llm_fixed"] else 0)
            total_critical += report["critical_remaining"]

        overall_status = "pass" if total_critical == 0 else "fail"

        full_report = {
            "overall_status": overall_status,
            "files_checked": len(tex_files),
            "total_fixes_applied": total_fixed,
            "total_critical_remaining": total_critical,
            "file_reports": file_reports,
        }

        # Save report
        PAPER_DIR.mkdir(parents=True, exist_ok=True)
        report_path = PAPER_DIR / "latex_check_report.json"
        report_path.write_text(
            json.dumps(full_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Update context
        ctx["phase"] = "P4.5_complete"
        ctx.setdefault("latex_check", {}).update({
            "status": overall_status,
            "report_path": str(report_path),
            "critical_remaining": total_critical,
            "fixes_applied": total_fixed,
        })
        save_context(ctx)

        # Print summary
        print(f"\n[P4.5-DONE] 状态: {overall_status.upper()}")
        print(f"  检查文件数: {len(tex_files)}")
        print(f"  自动修复数: {total_fixed}")
        print(f"  剩余 critical: {total_critical}")
        print(f"  报告: {report_path}")

        return ctx
