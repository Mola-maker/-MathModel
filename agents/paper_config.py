"""Paper configuration — language toggle and other paper-wide settings.

Single source of truth for paper.language. Read order:
  1. PAPER_LANGUAGE env var (overrides everything, useful for CI)
  2. config/paper_config.json (persisted across runs, set via UI)
  3. default "zh"
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

Language = Literal["zh", "en"]

_BASE = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _BASE / "config" / "paper_config.json"
_VALID_LANGS: set[str] = {"zh", "en"}
_DEFAULT_LANG: Language = "zh"


def _load_raw() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_paper_language() -> Language:
    """Return 'zh' or 'en'. Env var wins, then config file, then default."""
    env_val = (os.getenv("PAPER_LANGUAGE") or "").strip().lower()
    if env_val in _VALID_LANGS:
        return env_val  # type: ignore[return-value]

    raw = _load_raw()
    cfg_val = str(raw.get("language", "")).strip().lower()
    if cfg_val in _VALID_LANGS:
        return cfg_val  # type: ignore[return-value]

    return _DEFAULT_LANG


def set_paper_language(lang: str) -> Language:
    """Persist paper language selection. Returns the normalized value.

    Raises ValueError on invalid input so the UI surfaces bad requests.
    """
    normalized = lang.strip().lower()
    if normalized not in _VALID_LANGS:
        raise ValueError(f"invalid language: {lang!r}; expected one of {sorted(_VALID_LANGS)}")

    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = _load_raw()
    raw["language"] = normalized
    _CONFIG_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized  # type: ignore[return-value]


# Section title labels per language. Used by the LaTeX template renderer so
# that switching languages flips headings without re-editing the template.
SECTION_TITLES: dict[Language, dict[str, str]] = {
    "en": {
        "introduction": "Introduction",
        "assumptions": "Assumptions and Justifications",
        "model_formulation": "Model Formulation",
        "solution": "Solution and Implementation",
        "results_analysis": "Results and Analysis",
        "sensitivity": "Sensitivity Analysis",
        "strengths_weaknesses": "Strengths and Weaknesses",
        "strengths": "Strengths",
        "weaknesses": "Weaknesses",
        "conclusion": "Conclusion",
        "appendix_code": "Appendix A: Core Code Listings",
        "appendix_numeric": "Appendix B: Numerical Calculation Details",
        "summary_sheet": "Summary Sheet",
        "preproc": "Data Preprocessing and Cleaning",
        "model_impl": "Model Implementation",
        "sens_code": "Sensitivity Analysis",
    },
    "zh": {
        "introduction": "引言",
        "assumptions": "模型假设与合理性说明",
        "model_formulation": "模型建立",
        "solution": "模型求解与实现",
        "results_analysis": "结果与分析",
        "sensitivity": "敏感性分析",
        "strengths_weaknesses": "模型优缺点",
        "strengths": "模型优点",
        "weaknesses": "模型局限",
        "conclusion": "结论",
        "appendix_code": "附录 A：核心代码",
        "appendix_numeric": "附录 B：数值计算细节",
        "summary_sheet": "摘要",
        "preproc": "数据预处理与清洗",
        "model_impl": "模型实现",
        "sens_code": "敏感性分析脚本",
    },
}


def section_titles(lang: Language | None = None) -> dict[str, str]:
    """Return section-title mapping for the given language (defaults to current)."""
    return SECTION_TITLES[lang or get_paper_language()]
