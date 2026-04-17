from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from agents.knowledge_base import save_knowledge_snapshot, search_github_repos
from agents.orchestrator import call_model
from agents.prompt_sanitizer import build_brief_problem, clamp_keywords
from agents.utils import parse_json
from agents.knowledge_builder import format_knowledge_for_prompt

SYSTEM_CODER = """You are a Python engineering expert for math modeling tasks.
Return JSON only with this schema:
{
  "github_repos": [{"name":"...","url":"...","relevance":"..."}],
  "dependencies": ["numpy","pandas"],
  "pip_install": "pip install ...",
  "code_skeleton": "python code as escaped string",
  "data_processing_tips": ["..."],
  "implementation_notes": "..."
}
"""

_BASE = Path(__file__).resolve().parent.parent.parent
VOL_SCRIPTS = Path(os.getenv("VOL_SCRIPTS", str(_BASE / "vol" / "scripts")))


class CodingHandAgent:
    def run(self, problem: dict, keywords: list[str]) -> dict:
        keywords = clamp_keywords(keywords)
        query = " ".join(keywords[:6]) if keywords else str(problem.get("title", "math modeling"))
        repos = search_github_repos(query, top_k=8)
        brief_problem = build_brief_problem(problem, background_max_len=500)

        snapshot_path = save_knowledge_snapshot(
            "github_sources",
            {
                "query": query,
                "github_results": repos,
            },
        )

        # 注入往届竞赛算法代码经验
        algo_kb = format_knowledge_for_prompt("algorithm", max_chars=2000)
        algo_section = f"\n\nAlgorithm patterns from past competitions:\n{algo_kb}" if algo_kb else ""

        user_prompt = (
            f"Problem:\n{json.dumps(brief_problem, ensure_ascii=False, indent=2)}\n\n"
            f"Keywords: {', '.join(keywords)}\n\n"
            f"Live GitHub results:\n{json.dumps(repos, ensure_ascii=False, indent=2)}\n\n"
            f"{algo_section}\n\n"
            "Generate a runnable skeleton for Python 3.13 with numpy/scipy/pandas/matplotlib/sklearn/SALib/pulp."
        )
        if len(user_prompt) > 14000:
            user_prompt = user_prompt[:14000] + "\n\n[TRUNCATED]"
        raw = call_model(SYSTEM_CODER, user_prompt, task="codegen")
        result = parse_json(raw)
        result["knowledge_snapshot"] = str(snapshot_path)

        skeleton = result.get("code_skeleton", "")
        if isinstance(skeleton, str) and len(skeleton) > 30:
            VOL_SCRIPTS.mkdir(parents=True, exist_ok=True)
            skeleton_path = VOL_SCRIPTS / "skeleton.py"
            skeleton_path.write_text(skeleton, encoding="utf-8")
            result["skeleton_saved"] = str(skeleton_path)
            print(f"  [Coder] skeleton saved: {skeleton_path}")

        deps = result.get("dependencies", [])
        if isinstance(deps, list):
            print(f"  [Coder] deps: {', '.join([str(x) for x in deps[:6]])}")
        return result
