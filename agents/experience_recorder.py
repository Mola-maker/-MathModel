"""Experience Recorder — 成功经验提炼与保存。

每个阶段成功完成后自动调用，将本次运行的关键经验浓缩为结构化条目，
追加到 knowledge_base/experience_log.json。
格式与 modeling_patterns.json 完全一致，可被后续 P1/P2/P3 知识注入系统读取。

设计原则：
- 不记录"发生了什么"，而是提炼"下次遇到相似问题该怎么做"
- 每条经验包含：策略 / 技术选择 / 踩坑记录 / 可复用的 Prompt 技巧
- 相同题型的经验会聚合权重，使知识库随运行次数越来越准
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context

KB_DIR = Path(__file__).parent.parent / "knowledge_base"
EXPERIENCE_LOG = KB_DIR / "experience_log.json"

MAX_ENTRIES = 120   # rotate oldest once exceeded
MAX_CHARS_PER_FIELD = 3000   # prevent single-field blowup

# ─────────────────────────────────── per-phase extraction prompts ──

_PHASE_PROMPTS: dict[str, str] = {

"P1": """你是数学建模竞赛导师。根据题目解析结果，提炼供"下次遇到相似题目"时参考的经验。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "problem_type": "优化类|预测类|评价类|规划类|其他",
  "key_problem_features": ["题目特征1", "题目特征2"],
  "selection_rationale": "为什么选了这道题",
  "decomposition_strategy": "如何拆解子问题",
  "important_constraints": ["关键约束1", "关键约束2"],
  "data_needs": ["需要什么数据1", "数据来源建议"],
  "modeling_hints": ["建模方向提示1", "提示2"],
  "reuse_tips": ["复用建议1（如：若下次遇到XX类题，可先考虑YY方法）"]
}
""",

"P1.5": """你是数据分析专家。根据数据清洗结果，提炼供"下次处理相似数据"时参考的经验。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "data_characteristics": ["数据特征1", "特征2"],
  "quality_issues_found": ["发现的问题1", "问题2"],
  "effective_cleaning_steps": ["有效清洗步骤1", "步骤2"],
  "eda_key_findings": ["EDA关键发现1", "发现2"],
  "useful_features": ["对建模有价值的特征1", "特征2"],
  "pitfalls": ["踩坑记录1（避免重蹈覆辙）"],
  "reuse_tips": ["复用建议1"]
}
""",

"P2": """你是数学建模专家。根据建模过程，提炼供"下次遇到相似建模任务"时参考的经验。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "model_type": "所用模型类型",
  "model_name": "模型名称",
  "why_this_model": "选择理由（替代方案及放弃原因）",
  "core_equations": ["核心方程LaTeX1", "方程2"],
  "key_assumptions": ["关键假设1（及合理性说明）", "假设2"],
  "variable_design": "变量设计思路",
  "solution_approach": "求解思路",
  "validation_method": "验证方法",
  "model_strengths": ["优点1", "优点2"],
  "model_limitations": ["局限1（及处理方式）"],
  "reuse_tips": ["复用建议1"]
}
""",

"P3": """你是算法工程师。根据代码求解过程，提炼供"下次实现相似求解器"时参考的经验。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "algorithm_used": "使用的算法/求解器",
  "why_this_algorithm": "选择理由",
  "implementation_key_points": ["实现要点1", "要点2"],
  "performance_tuning": ["调优技巧1", "技巧2"],
  "convergence_tips": ["收敛技巧1"],
  "numerical_stability": ["数值稳定性处理1"],
  "output_format": "输出格式设计",
  "bugs_encountered": ["遇到的bug及修复方法1"],
  "reuse_tips": ["复用建议1（如：下次用XX数据规模可用YY方法）"]
}
""",

"P4": """你是论文写作专家。根据论文撰写结果，提炼供"下次写作相似论文"时参考的经验。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "paper_structure": "论文结构亮点",
  "intro_strategy": "引言写作策略",
  "model_presentation": "数学模型展示技巧",
  "result_visualization": "结果可视化建议",
  "figure_tips": ["图表技巧1", "技巧2"],
  "table_tips": ["表格技巧1"],
  "conclusion_strategy": "结论写作策略",
  "latex_tricks": ["LaTeX技巧1", "技巧2"],
  "common_mistakes": ["写作常见错误1（及避免方法）"],
  "reuse_tips": ["复用建议1"]
}
""",

"P5": """你是论文评审专家。根据审校报告，提炼供"下次提升论文质量"时参考的经验。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "total_score": 评审总分数字或null,
  "tier": "等级如Meritorious/Honorable或null",
  "score_breakdown": {"innovation": 分数, "completeness": 分数, "readability": 分数, "math_rigor": 分数},
  "strengths": ["得分亮点1", "亮点2"],
  "critical_weaknesses": ["严重问题1（及改进方向）", "问题2"],
  "suggestions_acted_on": ["已处理的建议1"],
  "lessons_for_next_time": ["下次提升策略1", "策略2"],
  "reuse_tips": ["复用建议1（如：若题型相似，下次应重点加强XX）"]
}
""",

"P5.5": """你是质量审计专家。根据数据审计结果，提炼供"下次保证数据真实性"时参考的经验。

输出严格 JSON（不含 markdown 代码块），结构：
{
  "audit_result": "pass|fail|warn",
  "data_integrity_issues": ["发现的数据问题1"],
  "fabrication_risks": ["潜在虚构风险1（及避免方法）"],
  "verification_checklist": ["验证清单项1", "项2"],
  "reuse_tips": ["复用建议1"]
}
""",
}

# ─────────────────────────────────── context extraction per phase ──

def _extract_phase_context(ctx: dict, phase: str) -> str:
    """Extract the most relevant parts of context.json for a given phase."""
    prob = ctx.get("competition", {}).get("selected_problem", "未知")
    keywords = ctx.get("competition", {}).get("keywords", [])

    snippets: dict = {
        "selected_problem": prob,
        "keywords": keywords[:10],
    }

    if phase == "P1":
        comp = ctx.get("competition", {})
        snippets["tasks"] = comp.get("tasks", [])[:5]
        snippets["problem_type"] = comp.get("problem_type", "")
        snippets["constraints"] = comp.get("constraints", [])[:5]
        research = ctx.get("research", {})
        snippets["modeler_summary"] = str(research.get("modeler_results", ""))[:MAX_CHARS_PER_FIELD]

    elif phase == "P1.5":
        dc = ctx.get("data_cleaning", {})
        snippets["files_processed"] = dc.get("files_processed", 0)
        snippets["previews"] = {
            k: {kk: vv for kk, vv in v.items() if kk in ("shape", "columns", "data_type")}
            for k, v in dc.get("previews", {}).items()
        }
        summaries = dc.get("stdout_summaries", {})
        snippets["stdout_summary_sample"] = list(summaries.values())[:2]

    elif phase == "P2":
        m = ctx.get("modeling", {})
        snippets["model_type"] = m.get("model_type", "")
        snippets["primary_model"] = m.get("primary_model", {})
        snippets["assumptions"] = m.get("assumptions", [])[:6]
        snippets["equations_latex"] = m.get("equations_latex", "")[:MAX_CHARS_PER_FIELD]
        snippets["solution_method"] = m.get("solution_method", "")
        snippets["validation"] = m.get("validation", {})

    elif phase == "P3":
        ce = ctx.get("code_execution", {})
        snippets["status"] = ce.get("status", "")
        snippets["iterations"] = ce.get("iterations", 0)
        snippets["artifacts"] = ce.get("artifacts", [])[:5]
        snippets["stdout_tail"] = ce.get("stdout", "")[-MAX_CHARS_PER_FIELD:]
        snippets["last_error"] = ce.get("last_error", "")

    elif phase == "P4":
        paper = ctx.get("paper", {})
        snippets["sections"] = list(paper.get("sections", {}).keys())
        snippets["word_count"] = paper.get("word_count", 0)
        snippets["figures_used"] = paper.get("figures", [])[:5]

    elif phase == "P5":
        rev = ctx.get("review", {})
        snippets["scores"] = rev.get("scores", {})
        snippets["suggestions"] = rev.get("suggestions", [])[:8]
        snippets["critical_fixes"] = rev.get("critical_fixes", [])[:5]
        snippets["consistency"] = rev.get("consistency", {})

    elif phase == "P5.5":
        dv = ctx.get("data_validation", {}).get("post_review", {})
        snippets["valid"] = dv.get("valid")
        snippets["issues"] = dv.get("issues", [])[:8]
        snippets["critical_count"] = dv.get("critical_count", 0)

    return json.dumps(snippets, ensure_ascii=False, indent=2)


# ─────────────────────────────────── knowledge file I/O ──

def _load_log() -> dict:
    if not EXPERIENCE_LOG.exists():
        return {
            "category": "experience",
            "description": "本系统历次运行成功经验（自动提炼，随运行次数积累）",
            "consumer": "P1 P2 P3 P4 P5",
            "built_at": datetime.now().isoformat(),
            "entry_count": 0,
            "entries": [],
        }
    return json.loads(EXPERIENCE_LOG.read_text(encoding="utf-8"))


def _save_log(log: dict) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    log["entry_count"] = len(log.get("entries", []))
    log["updated_at"] = datetime.now().isoformat()
    EXPERIENCE_LOG.write_text(
        json.dumps(log, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────── main recorder ──

def record_experience(phase: str) -> dict | None:
    """
    Distill and save experience for the given phase.
    Reads context.json, calls LLM, appends to experience_log.json.
    Returns the saved entry, or None on failure.
    """
    if phase not in _PHASE_PROMPTS:
        return None   # gate/validation phases don't generate experience entries

    ctx = load_context()
    problem = ctx.get("competition", {}).get("selected_problem", "未知题目")
    model_name = ctx.get("modeling", {}).get("primary_model", {}).get("model_name", "")

    print(f"  [EXP] 提炼 {phase} 经验...")

    context_text = _extract_phase_context(ctx, phase)
    system_prompt = _PHASE_PROMPTS[phase]
    user_prompt = (
        f"题目：{problem}\n"
        f"阶段：{phase}\n\n"
        f"本次运行的上下文数据：\n{context_text}\n\n"
        f"请根据以上信息，提炼出高质量、可复用的经验条目。"
        f"重点关注\"下次遇到相似情况该怎么做\"，而非仅描述\"这次做了什么\"。"
    )

    try:
        raw = call_model(system_prompt, user_prompt, task="extraction")
    except Exception as e:
        print(f"  [EXP] LLM 调用失败: {e}")
        return None

    # Parse JSON
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        extracted = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            print(f"  [EXP] 无法解析 LLM 输出为 JSON，跳过")
            return None
        try:
            extracted = json.loads(m.group())
        except json.JSONDecodeError:
            print(f"  [EXP] JSON 解析失败，跳过")
            return None

    # Build entry
    entry = {
        "id": f"exp_{phase}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "phase": phase,
        "phase_name": _PHASE_NAME.get(phase, phase),
        "timestamp": datetime.now().isoformat(),
        "problem": problem,
        "model_type": model_name,
        **extracted,
    }

    # Persist
    log = _load_log()
    log.setdefault("entries", []).append(entry)

    # Rotate if over limit (keep newest)
    if len(log["entries"]) > MAX_ENTRIES:
        log["entries"] = log["entries"][-MAX_ENTRIES:]

    _save_log(log)
    print(f"  [EXP] 经验已保存: {entry['id']} ({EXPERIENCE_LOG.name}, 共 {len(log['entries'])} 条)")
    return entry


_PHASE_NAME = {
    "P1": "题目解析",
    "P1.5": "数据清洗",
    "P2": "数学建模",
    "P3": "代码求解",
    "P4": "论文撰写",
    "P5": "审校评分",
    "P5.5": "数据审计",
}


# ─────────────────────────────────── knowledge injection helper ──

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Split on word chars + CJK; keep tokens of length >= 2 to skip stopword-ish noise."""
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]


def _score_entry(entry: dict, query_tokens: list[str]) -> int:
    """Count how many query tokens appear in the serialized entry (substring match).

    Each token contributes 1, regardless of repetition — avoids a single noisy
    field dominating the score. Problem/model_type matches are weighted 2x.
    """
    if not query_tokens:
        return 0
    body = json.dumps(
        {k: v for k, v in entry.items() if k not in ("id", "timestamp")},
        ensure_ascii=False,
    ).lower()
    headline = " ".join(
        str(entry.get(k, "")) for k in ("problem", "model_type", "problem_type")
    ).lower()
    score = 0
    for tok in set(query_tokens):
        if tok in headline:
            score += 2
        elif tok in body:
            score += 1
    return score


def _derive_query_from_context() -> str:
    """Pull keywords from the current competition context if the caller didn't pass one."""
    try:
        ctx = load_context()
    except Exception:
        return ""
    comp = ctx.get("competition", {}) if isinstance(ctx, dict) else {}
    parts: list[str] = []
    kws = comp.get("keywords") or []
    if isinstance(kws, list):
        parts.extend(str(k) for k in kws)
    for key in ("selected_problem", "problem_type"):
        v = comp.get(key)
        if v:
            parts.append(str(v))
    # Modeling hints also useful for P3/P4 scoring
    model = ctx.get("modeling", {}) if isinstance(ctx, dict) else {}
    if isinstance(model, dict):
        mt = model.get("model_type") or model.get("primary_model", {}).get("model_name")
        if mt:
            parts.append(str(mt))
    return " ".join(parts)


def get_relevant_experience(
    phase: str,
    max_entries: int = 3,
    max_chars: int = 4000,
    query: str | None = None,
) -> str:
    """Return formatted experience entries relevant to the given phase.

    Ranking:
      1. Filter by phase-relevance map.
      2. If `query` (or derivable from context) yields tokens, rank by
         keyword-match score (desc) then by recency (desc). Ties broken by
         recency so fresher entries win when scores are equal.
      3. Otherwise fall back to the previous behavior: take the most recent N.

    Backward-compatible: existing callers that don't pass `query` still get
    meaningful results because keywords are auto-derived from context.json.
    Empty log → empty string (unchanged).
    """
    if not EXPERIENCE_LOG.exists():
        return ""

    try:
        log = json.loads(EXPERIENCE_LOG.read_text(encoding="utf-8"))
    except Exception:
        return ""

    entries = log.get("entries", [])
    if not entries:
        return ""

    relevance = {
        "P2": ["P1", "P2"],
        "P3": ["P2", "P3"],
        "P4": ["P3", "P4", "P5"],
        "P5": ["P4", "P5"],
    }
    relevant_phases = relevance.get(phase, [phase])
    filtered = [e for e in entries if e.get("phase") in relevant_phases]
    if not filtered:
        return ""

    # Build query: explicit > context-derived > none
    q_text = query if query is not None else _derive_query_from_context()
    query_tokens = _tokenize(q_text)

    if query_tokens:
        # Attach recency index so we can break ties deterministically.
        scored = [
            (_score_entry(e, query_tokens), idx, e)
            for idx, e in enumerate(filtered)
        ]
        # Keep only positive scores when at least one entry matches; otherwise
        # fall back to recency so we don't suppress all history on a bad query.
        if any(s > 0 for s, _, _ in scored):
            scored.sort(key=lambda t: (-t[0], -t[1]))
            chosen = [e for _, _, e in scored[:max_entries]]
        else:
            chosen = filtered[-max_entries:][::-1]
    else:
        # No query → original "most recent N, newest first" behavior.
        chosen = filtered[-max_entries:][::-1]

    lines = [f"# 历次运行经验（{len(chosen)} 条，供参考）"]
    total = 0
    for e in chosen:
        entry_text = (
            f"\n## [{e['phase']}] {e.get('problem', '')}  "
            f"({e.get('timestamp', '')[:10]})\n"
            + json.dumps(
                {k: v for k, v in e.items()
                 if k not in ("id", "phase", "phase_name", "timestamp", "problem", "model_type")},
                ensure_ascii=False, indent=2
            )
        )
        if total + len(entry_text) > max_chars:
            break
        lines.append(entry_text)
        total += len(entry_text)

    return "\n".join(lines)
