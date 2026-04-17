from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.subagents.coder import CodingHandAgent
from agents.subagents.modeler import ModelingHandAgent
from agents.subagents.writer import WritingHandAgent
from agents.utils import parse_json

_BASE = Path(__file__).resolve().parent.parent
TRANSLATION_DIR = Path(os.getenv("TRANSLATION_DIR", str(_BASE / "translation")))

SYSTEM_EXTRACT = """You are an MCM/ICM problem parser.
Extract structured data from markdown contest text.
Return JSON only with keys:
competition_name, problems, selected_problem.
Each problem includes: title, background, questions, keywords, data_provided, constraints.
"""


class QuestionExtractor:
    def _fallback_problem(self, md_content: str) -> dict:
        # Minimal fallback so pipeline can continue even if LLM output is malformed.
        selected = "A"
        for label in ["A", "B", "C", "D", "E", "F"]:
            if f"{label}题" in md_content or f"Problem {label}" in md_content:
                selected = label
                break
        return {
            "competition_name": "Unknown Contest",
            "problems": {
                selected: {
                    "title": f"{selected}题",
                    "background": md_content[:300],
                    "questions": {},
                    "keywords": [],
                    "data_provided": [],
                    "constraints": [],
                }
            },
            "selected_problem": selected,
        }

    def _normalize_problem_info(self, data: object, md_content: str) -> dict:
        if isinstance(data, dict):
            problems = data.get("problems")
            if isinstance(problems, dict):
                return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("problems"), dict):
                    return item
        return self._fallback_problem(md_content)

    def extract_questions(self, md_content: str) -> dict:
        content = md_content[:12000]
        parsed = parse_json(
            call_model(SYSTEM_EXTRACT, f"Contest markdown:\n{content}", task="extraction")
        )
        return self._normalize_problem_info(parsed, md_content)

    def run_subagents_parallel(self, problem_info: dict, selected: str = "A") -> dict:
        problem = problem_info.get("problems", {}).get(selected, {})
        keywords = problem.get("keywords", [])
        results: dict = {}

        def run_modeler():
            return "modeler", ModelingHandAgent().run(problem, keywords)

        def run_coder():
            return "coder", CodingHandAgent().run(problem, keywords)

        def run_writer():
            return "writer", WritingHandAgent().run(problem, keywords)

        print("[QuestionExtractor] 并行启动三个 subagent...")
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(run_modeler),
                executor.submit(run_coder),
                executor.submit(run_writer),
            ]
            for future in as_completed(futures):
                try:
                    name, result = future.result()
                    results[name] = result
                    print(f"[QuestionExtractor] {name} 完成")
                except Exception as e:
                    print(f"[QuestionExtractor] subagent 失败: {e}")
        return results

    def run(self, selected_problem: str | None = None) -> dict:
        mds = sorted(TRANSLATION_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not mds:
            raise FileNotFoundError("translation/ 目录中没有 Markdown 文件，请先运行 pdf_agent")

        md_path = mds[0]
        print(f"[QuestionExtractor] 读取题目: {md_path.name}")
        md_content = md_path.read_text(encoding="utf-8")

        print("[QuestionExtractor] 提取题目结构...")
        problem_info = self.extract_questions(md_content)

        selected = selected_problem or problem_info.get("selected_problem", "A")
        problem_info["selected_problem"] = selected

        subagent_results = self.run_subagents_parallel(problem_info, selected)

        ctx = load_context()
        ctx["phase"] = "P1_extraction_complete"

        problem = problem_info.get("problems", {}).get(selected, {})
        ctx.setdefault("competition", {})["problem_text"] = json.dumps(problem, ensure_ascii=False)
        ctx["competition"]["selected_problem"] = selected
        ctx["competition"]["tasks"] = list(problem.get("questions", {}).values())
        ctx["competition"]["keywords"] = problem.get("keywords", [])

        ctx.setdefault("research", {})["modeler_results"] = subagent_results.get("modeler", {})
        ctx["research"]["coder_results"] = subagent_results.get("coder", {})
        ctx["research"]["writer_results"] = subagent_results.get("writer", {})

        modeler = subagent_results.get("modeler", {})
        ctx.setdefault("modeling", {})["model_type"] = modeler.get("recommended_model", "")
        ctx["modeling"]["assumptions"] = modeler.get("assumptions", [])
        ctx["modeling"]["variables"] = modeler.get("variables", {})

        save_context(ctx)
        print(f"[QuestionExtractor] 完成，选题: {selected}")
        return ctx

