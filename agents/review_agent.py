"""Review Agent — 模拟 COMAP 评委视角，对论文四维打分并给出修改建议。"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import parse_json as _parse_json

_BASE = Path(__file__).resolve().parent.parent
PAPER_DIR = Path(os.getenv("PAPER_DIR", str(_BASE / "paper")))

SYSTEM_JUDGE = """你是一位经验丰富的 MCM/ICM 竞赛评审委员。
请从评委视角对论文进行四维打分，输出严格 JSON（不含 markdown 代码块），结构：
{
  "scores": {
    "innovation": {"score": 1-25, "comment": "说明"},
    "completeness": {"score": 1-25, "comment": "说明"},
    "readability": {"score": 1-25, "comment": "说明"},
    "math_rigor": {"score": 1-25, "comment": "说明"}
  },
  "total": 合计分(满分100),
  "tier": "Outstanding|Meritorious|Honorable|Successful",
  "strengths": ["亮点1", "亮点2", "亮点3"],
  "weaknesses": ["弱点1", "弱点2", "弱点3"],
  "critical_fixes": ["必须修改项1", "必须修改项2"],
  "suggestions": ["建议1", "建议2", "建议3"]
}"""

SYSTEM_CONSISTENCY = """你是一位严谨的数学建模论文审稿人。
检查论文假设→推导→结论的逻辑链路是否自洽，是否存在矛盾。
输出严格 JSON（不含 markdown 代码块），结构：
{
  "consistency_issues": [
    {"location": "章节/段落", "issue": "问题描述", "severity": "critical|warning|minor"}
  ],
  "logic_chain": "pass|fail|warning",
  "note": "总体评价"
}"""

SYSTEM_FORMAT = """你是一位熟悉 MCM 规范的助手。
检查以下论文格式是否符合 MCM 官方要求。
输出严格 JSON（不含 markdown 代码块），结构：
{
  "format_issues": [
    {"item": "检查项", "status": "pass|fail|warning", "note": "说明"}
  ],
  "overall_format": "pass|fail|warning"
}"""

SYSTEM_HIGHLIGHT = """你是一位擅长学术写作的竞赛教练。
找出论文中最有竞争力的创新点，并给出如何在摘要和结论中更好地展示的具体建议。
输出 JSON，结构：
{
  "highlights": ["创新点1", "创新点2"],
  "abstract_improvements": ["改进建议1", "改进建议2"],
  "conclusion_improvements": ["改进建议1", "改进建议2"]
}"""

MCM_FORMAT_CHECKLIST = [
    "摘要控制在1页以内",
    "论文总页数不超过25页",
    "图表均有编号和图注",
    "参考文献格式统一",
    "团队编号出现在每页页眉",
    "摘要页格式符合Summary Sheet要求",
]


class ReviewAgent:
    """P5 审校优化：评委打分、逻辑检查、格式核查、亮点强化。"""

    def _load_paper_text(self) -> str:
        """加载所有章节文本（截断防止超长）。"""
        sections = []
        for tex_file in sorted(PAPER_DIR.glob("*.tex")):
            if tex_file.name != "main.tex":
                content = tex_file.read_text(encoding="utf-8")
                sections.append(f"=== {tex_file.stem} ===\n{content[:800]}")
        return "\n\n".join(sections)

    def judge_score(self, paper_text: str, ctx: dict) -> dict:
        """四维打分。"""
        user_prompt = (
            f"论文内容（节选）：\n{paper_text[:3000]}\n\n"
            f"模型类型：{ctx['modeling'].get('model_type', '')}\n"
            f"求解方法：{ctx['modeling'].get('solution_method', '')}"
        )
        return _parse_json(call_model(SYSTEM_JUDGE, user_prompt, task="review"))

    def check_consistency(self, paper_text: str, ctx: dict) -> dict:
        """逻辑一致性检查。"""
        user_prompt = (
            f"论文内容（节选）：\n{paper_text[:2500]}\n\n"
            f"假设列表：\n{json.dumps(ctx['modeling'].get('assumptions', []), ensure_ascii=False)}"
        )
        return _parse_json(call_model(SYSTEM_CONSISTENCY, user_prompt, task="review"))

    def check_format(self) -> dict:
        """格式规范核查（基于 checklist + 文本扫描）。"""
        issues = []
        for item in MCM_FORMAT_CHECKLIST:
            issues.append({"item": item, "status": "warning", "note": "请人工确认"})

        # 简单自动检查：main.tex 行数
        main_tex = PAPER_DIR / "main.tex"
        if main_tex.exists():
            lines = main_tex.read_text(encoding="utf-8").count("\n")
            status = "pass" if lines < 1500 else "warning"
            issues.append({
                "item": "LaTeX 源码行数",
                "status": status,
                "note": f"{lines} 行"
            })

        return {"format_issues": issues, "overall_format": "warning"}

    def highlight_strengths(self, paper_text: str, ctx: dict) -> dict:
        """识别创新亮点，给出强化建议。"""
        user_prompt = (
            f"论文内容（节选）：\n{paper_text[:2500]}\n\n"
            f"模型亮点参考：{json.dumps(ctx['modeling'].get('primary_model', {}), ensure_ascii=False)[:500]}"
        )
        return _parse_json(call_model(SYSTEM_HIGHLIGHT, user_prompt, task="review"))

    def run(self) -> dict:
        """
        完整 P5 流程：
        1. 加载论文文本
        2. 四维打分
        3. 逻辑一致性检查
        4. 格式核查
        5. 亮点强化建议
        6. 生成 review_report.json
        7. 写入 context_store
        """
        ctx = load_context()
        paper_text = self._load_paper_text()

        print("[P5] 评委视角打分...")
        scores = self.judge_score(paper_text, ctx)

        print("[P5] 逻辑一致性检查...")
        consistency = self.check_consistency(paper_text, ctx)

        print("[P5] 格式核查...")
        fmt = self.check_format()

        print("[P5] 亮点强化分析...")
        highlights = self.highlight_strengths(paper_text, ctx)

        # 汇总报告
        report = {
            "scores": scores,
            "consistency": consistency,
            "format": fmt,
            "highlights": highlights,
        }

        report_path = PAPER_DIR / "review_report.json"
        PAPER_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        # 写入 context
        ctx["phase"] = "P5_complete"
        review = ctx.setdefault("review", {})
        review["scores"] = scores.get("scores", {})
        review["total_score"] = scores.get("total")
        review["tier"] = scores.get("tier")
        review["suggestions"] = (
            scores.get("critical_fixes", []) + scores.get("suggestions", [])
        )
        review["report_path"] = str(report_path)

        save_context(ctx)

        # 打印摘要
        total = scores.get("total", 0)
        tier = scores.get("tier", "Unknown")
        print(f"\n[P5-DONE] 预测档次: {tier} | 总分: {total}/100")
        print(f"报告已保存: {report_path}")

        return ctx
