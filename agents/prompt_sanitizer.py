from __future__ import annotations

from typing import Any


def clamp_text(text: str, max_len: int = 1200) -> str:
    s = str(text or "")
    return s[:max_len]


def clamp_keywords(keywords: Any, max_items: int = 12, item_max_len: int = 60) -> list[str]:
    if not isinstance(keywords, list):
        return []
    out: list[str] = []
    for item in keywords[:max_items]:
        out.append(clamp_text(str(item), item_max_len))
    return out


def build_brief_problem(problem: dict, background_max_len: int = 600) -> dict:
    return {
        "title": clamp_text(problem.get("title", ""), 200),
        "background": clamp_text(problem.get("background", ""), background_max_len),
        "questions": problem.get("questions", {}) if isinstance(problem.get("questions", {}), dict) else {},
        "keywords": clamp_keywords(problem.get("keywords", [])),
        "constraints": problem.get("constraints", []) if isinstance(problem.get("constraints", []), list) else [],
    }
