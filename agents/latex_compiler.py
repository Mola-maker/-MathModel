"""LaTeX 编译工具 — 流式输出 + 自动修复循环。

compile_fix_loop(max_rounds, log_cb):
    xelatex × 3 → 若失败 → LLM 修复 → 再编译，循环至成功或达到上限。

cleanup_aux_files():
    删除 .aux .log .bbl .blg .out .toc 等辅助文件。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
PAPER_DIR = Path(os.getenv("PAPER_DIR", str(_BASE / "paper")))

_AUX_SUFFIXES = {
    ".aux", ".log", ".bbl", ".blg", ".out", ".toc", ".lof", ".lot",
    ".synctex.gz", ".fls", ".fdb_latexmk", ".idx", ".ind", ".ilg",
    ".nav", ".snm", ".vrb", ".bcf", ".run.xml", ".xdv",
}

# xelatex 行过滤：只保留错误/警告/定位行，跳过大量文件加载噪音
_SKIP_PREFIXES = ("(", ")", "\\", "[", "]", "{", "}", "<", ">", "ABD:", "**")
_KEEP_PATTERNS = ("!", "Warning", "Error", "error", "l.", "undefined", "missing",
                  "overfull", "underfull", "LaTeX", "Package", "File")


def _should_show(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if any(s.startswith(p) for p in _SKIP_PREFIXES):
        return False
    if any(k in s for k in _KEEP_PATTERNS):
        return True
    # 保留非纯路径行（路径行通常很长且带 /）
    return len(s) < 120 and "/" not in s


# ── 内部：带回调的单次 xelatex 运行 ──────────────────────────────────────────

def _xelatex_streaming(tex_name: str, cwd: Path, emit) -> tuple[int, list[str]]:
    """在 cwd 内运行 xelatex，逐行调用 emit，返回 (returncode, all_lines)。"""
    all_lines: list[str] = []
    try:
        proc = subprocess.Popen(
            ["xelatex", "-interaction=nonstopmode", "-file-line-error", tex_name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(cwd),
        )
        for raw in proc.stdout:
            line = raw.rstrip()
            all_lines.append(line)
            if _should_show(line):
                emit(line)
        proc.wait(timeout=180)
        return proc.returncode, all_lines
    except FileNotFoundError:
        raise RuntimeError("xelatex not found — 请确认容器已安装 TeX Live")
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("xelatex 超时（>180s）")


def _run_bibtex(stem: str, cwd: Path, emit) -> None:
    result = subprocess.run(
        ["bibtex", stem],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd), timeout=60,
    )
    for line in (result.stdout + result.stderr).splitlines():
        if line.strip():
            emit(line)


def _parse_log_file(log_path: Path) -> tuple[list[str], list[str]]:
    """从 .log 文件提取错误行和警告行。"""
    errors, warnings = [], []
    if not log_path.exists():
        return errors, warnings
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("!"):
            errors.append(s)
        elif "Warning" in s and len(s) < 200:
            warnings.append(s)
    return errors[:40], warnings[:20]


# ── 核心：一次完整编译（三遍 xelatex + bibtex）────────────────────────────────

def _compile_once(tex: Path, emit) -> dict:
    """运行一次完整编译流程，通过 emit 实时输出日志。"""
    paper_dir = tex.parent
    stem = tex.stem
    has_bib = bool(list(paper_dir.glob("*.bib")))
    all_lines: list[str] = []

    emit(f"  文件: {tex}")
    emit(f"  参考文献: {'有 .bib' if has_bib else '无'}")

    try:
        emit("\n[Pass 1] xelatex …")
        code, lines = _xelatex_streaming(tex.name, paper_dir, emit)
        all_lines += lines

        if has_bib:
            emit("\n[bibtex] …")
            _run_bibtex(stem, paper_dir, emit)

        emit("\n[Pass 2] xelatex …")
        code, lines = _xelatex_streaming(tex.name, paper_dir, emit)
        all_lines += lines

        emit("\n[Pass 3] xelatex …")
        code, lines = _xelatex_streaming(tex.name, paper_dir, emit)
        all_lines += lines

    except RuntimeError as e:
        return {"success": False, "log": "\n".join(all_lines),
                "errors": [str(e)], "warnings": [], "pdf_path": None}

    pdf = paper_dir / f"{stem}.pdf"
    errors, warnings = _parse_log_file(paper_dir / f"{stem}.log")
    return {
        "success": pdf.exists() and code == 0,
        "pdf_path": str(pdf) if pdf.exists() else None,
        "log": "\n".join(all_lines),
        "errors": errors,
        "warnings": warnings,
    }


# ── LLM 修复 ──────────────────────────────────────────────────────────────────

_SYSTEM_FIX = """\
你是 LaTeX 专家。根据编译错误修复 main.tex 文件。

规则：
1. 仔细分析每一条错误，找到根本原因
2. 只修改有问题的部分，不改动无关内容
3. 直接返回完整的修正后 .tex 文件内容
4. 不要包含 markdown 代码块（禁止 ``` 围栏）
5. 不要添加任何解释、注释或前言，只返回 LaTeX 代码
"""


def _llm_fix(tex: Path, errors: list[str], full_log: str, emit) -> str | None:
    """调用 LLM 修复 LaTeX 错误，返回修正后的 .tex 内容，失败返回 None。"""
    from agents.orchestrator import call_model

    tex_content = tex.read_text(encoding="utf-8")
    max_tex = 14000
    if len(tex_content) > max_tex:
        tex_content = tex_content[:max_tex] + "\n\n% ... [内容过长，已截断] ..."

    # 优先用精简的错误列表；不够则附上日志尾部
    error_text = "\n".join(errors) if errors else full_log[-3000:]

    user = f"""编译错误如下：
{error_text}

当前 main.tex 完整内容：
{tex_content}

请返回修正后的完整 .tex 内容（不含任何 markdown 围栏）："""

    emit("[LLM] 发送错误给 AI 分析中…")
    try:
        reply = call_model(_SYSTEM_FIX, user, task="writing")
    except Exception as e:
        emit(f"[LLM] 调用失败: {e}")
        return None

    # 去掉可能的代码块围栏
    reply = reply.strip()
    if reply.startswith("```"):
        lines = reply.splitlines()
        end = next((i for i in range(len(lines) - 1, 0, -1) if lines[i].strip() == "```"), len(lines))
        reply = "\n".join(lines[1:end]).strip()

    if len(reply) < 100:
        emit("[LLM] 返回内容过短，跳过")
        return None

    emit("[LLM] 已获取修复内容")
    return reply


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def compile_fix_loop(max_rounds: int = 5, log_cb=None) -> dict:
    """编译 + 自动修复循环。

    Parameters
    ----------
    max_rounds : int
        最大修复轮次（默认 5）。第 1 轮先编译，失败才进入 LLM 修复。
    log_cb : callable
        接收 dict 的回调：
          {"type": "log",     "line": str}   — 普通日志行
          {"type": "phase",   "line": str}   — 阶段标题（粗体显示）
          {"type": "success", "line": str}   — 成功消息（绿色）
          {"type": "error",   "line": str}   — 错误消息（红色）
          {"type": "done",    "success": bool, "rounds": int, "pdf_path": str|None}
    """
    if log_cb is None:
        log_cb = lambda m: print(m.get("line", ""))

    def emit(line: str, kind: str = "log"):
        log_cb({"type": kind, "line": line})

    tex = PAPER_DIR / "main.tex"
    if not tex.exists():
        emit(f"找不到 {tex}", "error")
        log_cb({"type": "done", "success": False, "rounds": 0, "pdf_path": None})
        return {"success": False}

    last_result: dict = {}

    for round_num in range(1, max_rounds + 1):
        bar = "─" * 48
        emit(f"\n{bar}", "phase")
        emit(f"  第 {round_num} / {max_rounds} 轮{'  [初始编译]' if round_num == 1 else '  [修复后重新编译]'}", "phase")
        emit(f"{bar}", "phase")

        last_result = _compile_once(tex, emit)

        if last_result["success"]:
            emit(f"\n✓ 编译成功！PDF → {last_result['pdf_path']}", "success")
            log_cb({"type": "done", "success": True,
                    "rounds": round_num, "pdf_path": last_result["pdf_path"]})
            return last_result

        errors = last_result.get("errors", [])
        emit(f"\n✗ 编译失败，发现 {len(errors)} 个错误", "error")
        for e in errors[:10]:
            emit(f"   {e}", "error")

        if round_num == max_rounds:
            emit(f"\n已达最大修复轮次 ({max_rounds})，停止。", "error")
            break

        # LLM 修复
        emit(f"\n{'─'*48}", "phase")
        emit(f"  调用 LLM 修复（第 {round_num} 次）", "phase")
        emit(f"{'─'*48}", "phase")

        fixed = _llm_fix(tex, errors, last_result["log"], emit)
        if not fixed:
            emit("LLM 修复失败，停止循环。", "error")
            break

        backup = PAPER_DIR / f"main.bak{round_num}.tex"
        backup.write_text(tex.read_text(encoding="utf-8"), encoding="utf-8")
        tex.write_text(fixed, encoding="utf-8")
        emit(f"[已写入修复] 原文件备份为 {backup.name}")

    log_cb({"type": "done", "success": False,
            "rounds": max_rounds, "pdf_path": None})
    return last_result


def compile_latex(tex_file: Path | None = None) -> dict:
    """单次编译（不循环修复），供后向兼容调用。"""
    collected: list[str] = []
    def log_cb(msg):
        if "line" in msg:
            collected.append(msg["line"])
    result = compile_fix_loop(max_rounds=1, log_cb=log_cb)
    result["log"] = "\n".join(collected)
    return result


def cleanup_aux_files(paper_dir: Path | None = None) -> dict:
    """删除 LaTeX 辅助文件，保留 .tex .bib .pdf 及子目录。"""
    d = paper_dir or PAPER_DIR
    removed, errors = [], []
    for f in d.iterdir():
        if f.is_file() and f.suffix in _AUX_SUFFIXES:
            try:
                f.unlink()
                removed.append(f.name)
            except Exception as e:
                errors.append(f"{f.name}: {e}")
    return {"removed_count": len(removed), "removed": sorted(removed), "errors": errors}
