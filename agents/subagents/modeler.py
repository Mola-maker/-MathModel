from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from agents.knowledge_base import (
    data_source_status,
    save_knowledge_snapshot,
    search_openalex_works,
    search_semantic_scholar,
    search_crossref,
    load_manual_bibtex_entries,
)
from agents.orchestrator import call_model
from agents.prompt_sanitizer import build_brief_problem, clamp_keywords
from agents.utils import parse_json
from agents.knowledge_builder import format_knowledge_for_prompt

SYSTEM_MODELER = """You are a mathematical modeling expert.
Given problem information and keywords, recommend robust modeling frameworks.
Return JSON only with this schema:
{
  "search_strategy": "...",
  "classic_models": [{"name":"...","type":"...","core_equation":"...","why_fits":"...","difficulty":"..."}],
  "recommended_model": "...",
  "assumptions": [{"id":"A1","text":"...","justification":"..."}],
  "variables": {"x":{"meaning":"...","unit":"..."}},
  "model_comparison": "..."
}
"""


class ModelingHandAgent:
    def run(self, problem: dict, keywords: list[str]) -> dict:
        keywords = clamp_keywords(keywords)
        query = " ".join(keywords[:6]) if keywords else str(problem.get("title", "math modeling"))
        brief_problem = build_brief_problem(problem, background_max_len=600)

        # ── 并发检索三个数据库 ──────────────────────────────────────────────
        print("  [Modeler] 检索文献: OpenAlex / Semantic Scholar / CrossRef ...")
        openalex_papers = search_openalex_works(query, top_k=6)
        s2_papers = search_semantic_scholar(query, top_k=6)
        crossref_papers = search_crossref(query, top_k=4)
        manual_entries = load_manual_bibtex_entries()          # WoS/CNKI 手动导出

        all_papers = openalex_papers + s2_papers + crossref_papers
        print(f"  [Modeler] 检索到: OpenAlex={len(openalex_papers)}, "
              f"S2={len(s2_papers)}, CrossRef={len(crossref_papers)}, "
              f"手动导出={len(manual_entries)} 份")

        source_status = data_source_status()
        snapshot_path = save_knowledge_snapshot(
            "modeling_sources",
            {
                "query": query,
                "source_status": source_status,
                "openalex_results": openalex_papers,
                "semantic_scholar_results": s2_papers,
                "crossref_results": crossref_papers,
                "manual_exports": [e["source"] for e in manual_entries],
            },
        )

        # 手动导出文献提示（仅传文件名，避免 BibTeX 原文撑爆 prompt）
        manual_note = ""
        if manual_entries:
            names = [e["source"] for e in manual_entries]
            total = sum(e.get("entry_count", 0) for e in manual_entries)
            manual_note = (
                f"\n\nManual exports (WoS/CNKI) available in reference/manual/: "
                f"{names} — {total} entries total. "
                "These will be merged into references_draft.bib automatically."
            )

        # 注入历届获奖论文建模模式 + 历年题目规律
        modeling_kb = format_knowledge_for_prompt("modeling", max_chars=2500)
        problems_kb = format_knowledge_for_prompt("problems", max_chars=1000)
        kb_section = ""
        if modeling_kb:
            kb_section += f"\n\n{modeling_kb}"
        if problems_kb:
            kb_section += f"\n\n{problems_kb}"

        user_prompt = (
            f"Problem:\n{json.dumps(brief_problem, ensure_ascii=False, indent=2)}\n\n"
            f"Keywords: {', '.join(keywords)}\n\n"
            f"Literature from OpenAlex + Semantic Scholar + CrossRef "
            f"({len(all_papers)} papers total):\n"
            f"{json.dumps(all_papers, ensure_ascii=False, indent=2)}"
            f"{manual_note}"
            f"{kb_section}\n\n"
            "Please provide modeling frameworks grounded in the literature and past competition patterns above. "
            "Do not fabricate inaccessible CNKI/WoS details."
        )
        if len(user_prompt) > 14000:
            user_prompt = user_prompt[:14000] + "\n\n[TRUNCATED]"
        raw = call_model(SYSTEM_MODELER, user_prompt, task="modeling")
        result = parse_json(raw)
        result["knowledge_snapshot"] = str(snapshot_path)
        print(f"  [Modeler] 推荐模型: {result.get('recommended_model', 'unknown')}")
        return result
