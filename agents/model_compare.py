"""P2.8 — Model Compare Agent.

Given N candidate models (primary + alternatives collected during P2), score
each along cheap static metrics plus an LLM-judged ranking, then write a
consolidated comparison + selected winner back into context_store.

Design notes:
- This stage never overwrites P2's `primary_model`. It adds a sibling field
  `comparison_v2` (so the existing P2 comparison stays intact for diffing),
  plus a top-level `selected_model_id` the downstream stages can opt into.
- Runs as `on_error="skip"`: if no candidates exist, or the LLM call fails,
  the pipeline should continue with P2's original choice, not halt.
- Cheap metrics (equation count, variable count, LaTeX presence) are computed
  locally so even when the LLM is unreachable we still emit structured output.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import parse_json as _parse_json

SYSTEM_RANK = """你是一位数学建模竞赛评委。
给定 N 个候选模型（每个含 model_name / model_type / equations_latex / variables / assumptions 等），
从以下维度打分并排名：
  - rigor       数学严谨性 1-10
  - feasibility 可实现性   1-10
  - fit         与题目匹配度 1-10
  - score_pot   得分潜力     1-10

输出严格 JSON（不含 markdown 代码块）：
{
  "ranking": [
    {"model_id": "候选ID", "rigor": 1-10, "feasibility": 1-10,
     "fit": 1-10, "score_pot": 1-10, "total": int, "note": "一句话评语"}
  ],
  "winner_id": "得分最高的 model_id",
  "reason": "为什么它最优"
}
"""


@dataclass(frozen=True)
class Candidate:
    model_id: str
    model: dict
    metrics: dict

    def to_prompt_dict(self) -> dict:
        return {"model_id": self.model_id, **_trim(self.model), "metrics": self.metrics}


@dataclass
class CompareResult:
    candidates: list[dict] = field(default_factory=list)
    ranking: list[dict] = field(default_factory=list)
    winner_id: str = ""
    reason: str = ""
    method: str = "llm+metrics"   # or "metrics_only" / "single"

    def to_dict(self) -> dict:
        return asdict(self)


def _trim(model: dict, max_str: int = 800) -> dict:
    """Shrink large string fields so the prompt stays within budget."""
    out: dict[str, Any] = {}
    for k, v in model.items():
        if isinstance(v, str) and len(v) > max_str:
            out[k] = v[:max_str] + "…"
        else:
            out[k] = v
    return out


def _count_latex_equations(text: str) -> int:
    if not text:
        return 0
    count = len(re.findall(r"\\begin\{(equation|align|gather)", text))
    count += text.count("$$") // 2
    return count or (1 if text.strip() else 0)


def _compute_metrics(model: dict) -> dict:
    """Cheap static signals — no LLM, always available."""
    latex = model.get("equations_latex") or model.get("equations") or ""
    if isinstance(latex, list):
        latex = "\n".join(str(x) for x in latex)
    variables = model.get("variables", {})
    assumptions = model.get("assumptions", [])
    constraints = model.get("constraints", [])

    return {
        "equation_count": _count_latex_equations(str(latex)),
        "variable_count": len(variables) if hasattr(variables, "__len__") else 0,
        "assumption_count": len(assumptions) if isinstance(assumptions, list) else 0,
        "constraint_count": len(constraints) if isinstance(constraints, list) else 0,
        "has_latex": bool(latex),
        "has_solution_method": bool(model.get("solution_method")),
    }


def _collect_candidates(ctx: dict) -> list[Candidate]:
    """Gather candidate models from context. Returns empty list if none found."""
    modeling = ctx.get("modeling", {}) if isinstance(ctx, dict) else {}
    seen_names: set[str] = set()
    out: list[Candidate] = []

    def _push(model: dict, suffix: str) -> None:
        if not isinstance(model, dict) or not model:
            return
        name = str(model.get("model_name") or model.get("name") or suffix)
        if name in seen_names:
            return
        seen_names.add(name)
        out.append(Candidate(
            model_id=f"M{len(out) + 1}:{name}"[:80],
            model=model,
            metrics=_compute_metrics(model),
        ))

    primary = modeling.get("primary_model") or modeling.get("primary")
    if primary:
        _push(primary, "primary")

    for i, alt in enumerate(modeling.get("alternative_models", []) or []):
        _push(alt, f"alt_{i+1}")

    # Optional user-seeded list
    for i, c in enumerate(modeling.get("candidates", []) or []):
        _push(c, f"cand_{i+1}")

    return out


def _rank_llm(candidates: list[Candidate]) -> dict:
    """Ask the LLM for a ranked comparison. Returns dict or empty on failure."""
    payload = [c.to_prompt_dict() for c in candidates]
    user_prompt = "候选模型列表：\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        raw = call_model(SYSTEM_RANK, user_prompt, task="modeling")
        return _parse_json(raw) or {}
    except Exception as exc:
        print(f"  [P2.8] LLM 排名失败，降级为纯指标打分: {exc}")
        return {}


def _metric_fallback(candidates: list[Candidate]) -> dict:
    """Deterministic fallback ranking when LLM is unavailable.

    Score = equation_count + variable_count + constraint_count,
    plus bonuses for has_latex / has_solution_method. Ties broken by
    original collection order (primary first).
    """
    scored = []
    for idx, c in enumerate(candidates):
        m = c.metrics
        total = (
            m["equation_count"]
            + m["variable_count"]
            + m["constraint_count"]
            + (2 if m["has_latex"] else 0)
            + (2 if m["has_solution_method"] else 0)
        )
        scored.append({
            "model_id": c.model_id,
            "rigor": min(10, m["equation_count"] + 2),
            "feasibility": 5 + (2 if m["has_solution_method"] else 0),
            "fit": 5,
            "score_pot": 5 + (2 if m["has_latex"] else 0),
            "total": total,
            "note": "指标兜底打分（LLM 不可用）",
            "_order": idx,
        })
    scored.sort(key=lambda r: (-r["total"], r["_order"]))
    for r in scored:
        r.pop("_order", None)
    winner = scored[0] if scored else {}
    return {
        "ranking": scored,
        "winner_id": winner.get("model_id", ""),
        "reason": "纯指标打分：方程/变量/约束数量加权" if scored else "",
    }


class ModelCompareAgent:
    """P2.8 — multi-model comparison, adds `comparison_v2` to context."""

    def run(self) -> dict:
        ctx = load_context()
        candidates = _collect_candidates(ctx)

        result = CompareResult(candidates=[c.to_prompt_dict() for c in candidates])

        if not candidates:
            result.method = "skipped"
            result.reason = "无候选模型（modeling.primary_model / alternative_models 均为空）"
            print("  [P2.8] 未发现候选模型，跳过对比")
            return self._write(ctx, result)

        if len(candidates) == 1:
            result.method = "single"
            result.winner_id = candidates[0].model_id
            result.reason = "只有一个候选模型"
            result.ranking = [{
                "model_id": candidates[0].model_id, "total": 0,
                "note": "唯一候选，无需对比",
            }]
            print(f"  [P2.8] 仅 1 个候选（{candidates[0].model_id}），直接选定")
            return self._write(ctx, result)

        llm = _rank_llm(candidates)
        if llm.get("ranking"):
            result.ranking = llm["ranking"]
            result.winner_id = llm.get("winner_id") or (
                llm["ranking"][0].get("model_id", "") if llm["ranking"] else ""
            )
            result.reason = llm.get("reason", "")
            result.method = "llm+metrics"
        else:
            fb = _metric_fallback(candidates)
            result.ranking = fb["ranking"]
            result.winner_id = fb["winner_id"]
            result.reason = fb["reason"]
            result.method = "metrics_only"

        print(f"  [P2.8] 对比 {len(candidates)} 个候选，winner={result.winner_id} ({result.method})")
        return self._write(ctx, result)

    @staticmethod
    def _write(ctx: dict, result: CompareResult) -> dict:
        modeling = ctx.setdefault("modeling", {})
        modeling["comparison_v2"] = result.to_dict()
        if result.winner_id:
            modeling["selected_model_id"] = result.winner_id
        ctx["phase"] = "P2.8_complete"
        save_context(ctx)
        return ctx
