from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from agents.orchestrator import call_model
from agents.prompt_sanitizer import build_brief_problem, clamp_keywords
from agents.utils import parse_json
from agents.knowledge_builder import format_knowledge_for_prompt

RULE_LATEX_DIR = Path("E:/mathmodel/rule_latex")
PAPER_DIR = Path("E:/mathmodel/paper")

SYSTEM_WRITER = """You are an MCM/ICM paper writing assistant.
Return JSON only with keys:
paper_outline, task_writing_tips, references, bibtex_content, writing_notes.
"""


class WritingHandAgent:
    def _load_format_rules(self) -> str:
        rules: list[str] = []
        if RULE_LATEX_DIR.exists():
            for f in RULE_LATEX_DIR.glob("*.md"):
                rules.append(f.read_text(encoding="utf-8"))
            for f in RULE_LATEX_DIR.glob("*.txt"):
                rules.append(f.read_text(encoding="utf-8"))
        return "\n\n".join(rules) if rules else "Use standard MCM/ICM structure."

    def run(self, problem: dict, keywords: list[str]) -> dict:
        format_rules = self._load_format_rules()
        keywords = clamp_keywords(keywords)
        brief_problem = build_brief_problem(problem, background_max_len=500)

        # 注入往届国奖写作经验 + LaTeX模板知识
        writing_kb = format_knowledge_for_prompt("writing", max_chars=2000)
        latex_kb = format_knowledge_for_prompt("latex", max_chars=1000)
        kb_section = ""
        if writing_kb:
            kb_section += f"\n\n{writing_kb}"
        if latex_kb:
            kb_section += f"\n\n{latex_kb}"

        user_prompt = (
            f"Problem:\n{json.dumps(brief_problem, ensure_ascii=False, indent=2)}\n\n"
            f"Keywords: {', '.join(keywords)}\n\n"
            f"Format rules:\n{format_rules[:2000]}"
            f"{kb_section}"
        )
        if len(user_prompt) > 14000:
            user_prompt = user_prompt[:14000] + "\n\n[TRUNCATED]"
        raw = call_model(SYSTEM_WRITER, user_prompt, task="writing")
        result = parse_json(raw)

        bibtex = result.get("bibtex_content", "")
        if isinstance(bibtex, str) and len(bibtex) > 20:
            PAPER_DIR.mkdir(parents=True, exist_ok=True)
            bib_path = PAPER_DIR / "references_draft.bib"
            bib_path.write_text(bibtex, encoding="utf-8")
            result["bib_path"] = str(bib_path)
            print(f"  [Writer] BibTeX saved: {bib_path}")

        return result
