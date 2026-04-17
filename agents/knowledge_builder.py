"""
knowledge_builder.py — 一次性将 gitclone/MathModel/ 所有资料
提炼成分类知识库，分别喂给建模手/编程手/写作手。

运行方式：
    python -m agents.knowledge_builder           # 全量构建
    python -m agents.knowledge_builder --cat writing  # 只构建写作库
    python -m agents.knowledge_builder --force   # 强制重建（忽略缓存）

输出目录：knowledge_base/
    ├── modeling_patterns.json   → 建模手（modeler）
    ├── writing_patterns.json    → 写作手（writer）
    ├── algorithm_patterns.json  → 编程手（coder）
    ├── latex_templates.json     → 写作手（writer，LaTeX专项）
    ├── competition_problems.json→ 建模手（历年题目规律）
    └── build_manifest.json      → 构建清单（用于增量更新）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model
from agents.knowledge_base import KNOWLEDGE_BASE_DIR

_BASE = Path(__file__).resolve().parent.parent
GITCLONE_DIR = Path(os.getenv("GITCLONE_DIR", str(_BASE / "gitclone" / "MathModel")))
MANIFEST_PATH = KNOWLEDGE_BASE_DIR / "build_manifest.json"

# ── 分类规则 ──────────────────────────────────────────────────────────────────
# 每个分类: (输出文件名, 源目录列表/文件列表, 读取模式)

CATEGORIES: dict[str, dict] = {
    "writing": {
        "output": "writing_patterns.json",
        "description": "写作手专用：论文写作技巧、结构规范、竞赛心得",
        "consumer": "writer subagent + writing_agent",
        "sources": [
            GITCLONE_DIR / "数学建模技巧篇/如何写数学建模竞赛论文.md",
            GITCLONE_DIR / "数学建模应掌握的十类算法.md",
            GITCLONE_DIR / "比赛心得.md",
            GITCLONE_DIR / "选题、命题介绍分析.md",
            GITCLONE_DIR / "数学建模竞赛网上资源.md",
        ],
        "pdf_dirs": [],    # 不从 PDF 提取
        "extract_type": "writing_tips",
    },
    "algorithm": {
        "output": "algorithm_patterns.json",
        "description": "编程手专用：MATLAB算法实现、算法模板、代码骨架",
        "consumer": "coder subagent + code_agent",
        "sources": [],
        "pdf_dirs": [],
        "code_dirs": [
            GITCLONE_DIR / "现代算法",
        ],
        "extract_type": "algorithm_code",
    },
    "modeling": {
        "output": "modeling_patterns.json",
        "description": "建模手专用：历届获奖论文的建模思路、模型选择、创新点",
        "consumer": "modeler subagent + modeling_agent",
        "sources": [
            GITCLONE_DIR / "选题、命题介绍分析.md",
        ],
        "pdf_dirs": [
            # 国赛论文（近5年，每年每题抽样3篇）
            GITCLONE_DIR / "国赛论文/2023年优秀论文",
            GITCLONE_DIR / "国赛论文/2022年优秀论文",
            GITCLONE_DIR / "国赛论文/2021年优秀论文",
            GITCLONE_DIR / "国赛论文/2020年优秀论文",
            GITCLONE_DIR / "国赛论文/2019年优秀论文",
            # 美赛论文（近5年）
            GITCLONE_DIR / "美赛论文/2017美赛特等奖原版论文集",
        ],
        "sample_per_dir": 2,   # 每个题型目录最多抽2篇（避免处理太多PDF）
        "extract_type": "modeling_patterns",
    },
    "latex": {
        "output": "latex_templates.json",
        "description": "写作手专用：LaTeX论文格式、宏包配置、竞赛模板",
        "consumer": "writer subagent（LaTeX专项）",
        "sources": [],
        "pdf_dirs": [],
        "tex_dirs": [
            GITCLONE_DIR / "2018年研究生数学建模/2018年Latex模版",
            GITCLONE_DIR / "2019年论文模版/2019年Latex模版",
        ],
        "extract_type": "latex_template",
    },
    "problems": {
        "output": "competition_problems.json",
        "description": "建模手专用：历年竞赛题目规律、常见题型、出题风格",
        "consumer": "question_extractor + modeler subagent",
        "sources": [
            GITCLONE_DIR / "选题、命题介绍分析.md",
        ],
        "pdf_dirs": [
            GITCLONE_DIR / "国赛试题/2022年研究生数学建模竞赛试题",
            GITCLONE_DIR / "国赛试题/2021年研究生数学建模竞赛试题",
        ],
        "sample_per_dir": 2,
        "extract_type": "problem_patterns",
    },
}

# ── LLM 提炼 Prompts ──────────────────────────────────────────────────────────

EXTRACT_PROMPTS: dict[str, str] = {
    "writing_tips": """你是一位数学建模竞赛论文专家。
请阅读以下竞赛资料，提炼出对论文写作手最有价值的经验。
输出严格JSON（不含markdown代码块），结构：
{
  "source": "文件名",
  "category": "writing",
  "key_rules": ["规则1", "规则2"],
  "abstract_tips": "摘要写法要点（1-2句）",
  "structure_tips": "论文结构建议",
  "common_mistakes": ["常见错误1", "常见错误2"],
  "competition_strategy": "竞赛策略摘要（1-2句）",
  "direct_quotes": ["值得直接引用的金句1", "金句2"]
}""",

    "algorithm_code": """你是一位数学建模竞赛代码专家。
请阅读以下算法代码/说明，提炼对编程手最有用的信息。
输出严格JSON（不含markdown代码块），结构：
{
  "source": "文件名",
  "category": "algorithm",
  "algorithm_name": "算法名称",
  "algorithm_type": "优化|预测|分类|统计|仿真",
  "use_cases": ["适用场景1", "场景2"],
  "core_logic": "核心逻辑（1-2句）",
  "python_equivalent": "Python等价实现提示（scipy/sklearn函数名）",
  "key_parameters": ["关键参数1", "参数2"],
  "pitfalls": ["注意事项1", "注意事项2"]
}""",

    "modeling_patterns": """你是一位数学建模竞赛评审专家。
请阅读以下获奖论文摘录，提炼建模手需要的关键信息。
输出严格JSON（不含markdown代码块），结构：
{
  "source": "文件名",
  "category": "modeling",
  "problem_type": "优化|预测|评价|分类|统计|仿真",
  "year": "年份（如2023）",
  "award_level": "一等奖|二等奖|优秀奖",
  "core_models": ["主模型", "验证模型"],
  "model_selection_reason": "为什么选这个模型（1句）",
  "innovation_points": ["创新点1", "创新点2"],
  "formula_structure": "核心公式结构描述",
  "sensitivity_approach": "敏感性分析做法",
  "visualization_types": ["图表类型1", "图表类型2"],
  "key_conclusions": "主要结论（1-2句）"
}""",

    "latex_template": """你是一位LaTeX排版专家。
请阅读以下LaTeX模板文件，提炼排版规范。
输出严格JSON（不含markdown代码块），结构：
{
  "source": "文件名",
  "category": "latex",
  "document_class": "文档类名",
  "key_packages": ["包名1", "包名2"],
  "page_layout": "页面布局描述",
  "font_settings": "字体设置",
  "equation_style": "公式排版规范",
  "figure_style": "图表插入规范",
  "section_structure": ["章节结构1", "章节2"],
  "special_commands": ["自定义命令1", "命令2"]
}""",

    "problem_patterns": """你是一位数学建模竞赛命题专家。
请阅读以下竞赛题目资料，分析题型规律。
输出严格JSON（不含markdown代码块），结构：
{
  "source": "文件名",
  "category": "problem",
  "year": "年份",
  "problem_id": "题号(A/B/C/D/E/F)",
  "domain": "领域（如交通/环境/工程/生物）",
  "problem_type": "优化|预测|评价|分类|统计|仿真",
  "keywords": ["关键词1", "关键词2"],
  "data_provided": "提供了什么数据",
  "sub_questions": ["子问题1", "子问题2"],
  "difficulty": "简单|中等|困难",
  "recommended_models": ["推荐模型1", "模型2"]
}""",
}

# ── 文件读取工具 ──────────────────────────────────────────────────────────────

def _read_text_file(path: Path, max_chars: int = 8000) -> str:
    """读取文本文件（md/txt/tex/m），截断超长内容。"""
    for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            content = path.read_text(encoding=enc, errors="replace")
            return content[:max_chars]
        except Exception:
            continue
    return ""


def _extract_pdf_text(path: Path, max_chars: int = 6000) -> str:
    """用 pdfplumber 提取 PDF 文本（容器内运行时可用）。"""
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(path)) as pdf:
            pages = []
            total = 0
            for page in pdf.pages[:8]:  # 最多读8页
                t = page.extract_text() or ""
                pages.append(t)
                total += len(t)
                if total >= max_chars:
                    break
            return "\n".join(pages)[:max_chars]
    except Exception as e:
        return f"[PDF提取失败: {e}]"


def _file_hash(path: Path) -> str:
    """计算文件内容hash，用于增量更新检测。"""
    try:
        h = hashlib.md5(path.read_bytes()).hexdigest()
        return h[:12]
    except Exception:
        return "unknown"


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 核心提炼函数 ──────────────────────────────────────────────────────────────

def _distill_with_llm(content: str, extract_type: str, source_name: str) -> dict | None:
    """调用 LLM 对单个文档进行知识提炼，返回结构化 dict。"""
    system = EXTRACT_PROMPTS.get(extract_type, EXTRACT_PROMPTS["writing_tips"])
    user = f"来源文件：{source_name}\n\n内容：\n{content}"

    try:
        raw = call_model(system, user, task="extraction")
        # 清理 markdown 代码块
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])
        return json.loads(raw)
    except json.JSONDecodeError:
        # 尝试找到第一个 { 到最后一个 }
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except Exception:
                pass
        print(f"  [KB] JSON 解析失败: {source_name}")
        return None
    except Exception as e:
        print(f"  [KB] LLM 调用失败 ({source_name}): {e}")
        return None


# ── 各类别构建函数 ────────────────────────────────────────────────────────────

def _build_from_text_files(
    sources: list[Path],
    extract_type: str,
    manifest: dict,
    force: bool,
    cat_name: str = "",
) -> list[dict]:
    entries: list[dict] = []
    for path in sources:
        if not path.exists():
            print(f"  [KB] 跳过不存在的文件: {path}")
            continue
        key = f"{cat_name}:{path}" if cat_name else str(path)
        fhash = _file_hash(path)
        if not force and manifest.get(key) == fhash:
            print(f"  [KB] 缓存命中，跳过: {path.name}")
            continue
        print(f"  [KB] 提炼中: {path.name} ...", end=" ", flush=True)
        content = _read_text_file(path)
        result = _distill_with_llm(content, extract_type, path.name)
        if result:
            result["_source_path"] = str(path)
            entries.append(result)
            manifest[key] = fhash
            print("OK")
            time.sleep(0.5)  # 避免速率限制
        else:
            print("FAILED")
    return entries


def _build_from_code_dirs(
    code_dirs: list[Path],
    manifest: dict,
    force: bool,
) -> list[dict]:
    """从 .m / .py 代码文件提炼算法模式。"""
    entries: list[dict] = []
    code_exts = {".m", ".py"}
    for code_dir in code_dirs:
        if not code_dir.exists():
            continue
        for path in sorted(code_dir.rglob("*")):
            if path.suffix.lower() not in code_exts:
                continue
            key = str(path)
            fhash = _file_hash(path)
            if not force and manifest.get(key) == fhash:
                continue
            print(f"  [KB] 算法提炼: {path.name} ...", end=" ", flush=True)
            content = _read_text_file(path)
            result = _distill_with_llm(content, "algorithm_code", path.name)
            if result:
                result["_source_path"] = str(path)
                entries.append(result)
                manifest[key] = fhash
                print("OK")
                time.sleep(0.5)
            else:
                print("FAILED")
    return entries


def _build_from_pdf_dirs(
    pdf_dirs: list[Path],
    extract_type: str,
    sample_per_dir: int,
    manifest: dict,
    force: bool,
) -> list[dict]:
    """从 PDF 目录提炼（每子目录抽样 N 篇，避免处理太多文件）。"""
    entries: list[dict] = []
    for pdf_dir in pdf_dirs:
        if not pdf_dir.exists():
            print(f"  [KB] 目录不存在: {pdf_dir}")
            continue
        # 遍历子目录（按题型 A/B/C/D/E/F）
        subdirs = sorted([d for d in pdf_dir.iterdir() if d.is_dir()])
        if not subdirs:
            subdirs = [pdf_dir]
        for subdir in subdirs:
            pdfs = sorted(list(subdir.glob("*.pdf")))[:sample_per_dir]
            for pdf_path in pdfs:
                key = str(pdf_path)
                fhash = _file_hash(pdf_path)
                if not force and manifest.get(key) == fhash:
                    print(f"  [KB] 缓存命中: {pdf_path.name}")
                    continue
                print(f"  [KB] PDF提炼: {pdf_path.name} ...", end=" ", flush=True)
                content = _extract_pdf_text(pdf_path)
                if "[PDF提取失败" in content or len(content) < 100:
                    print("SKIP(无文本)")
                    continue
                # 附加目录元数据到提炼请求
                year_hint = ""
                for part in pdf_path.parts:
                    if "年" in part and ("优秀" in part or "美赛" in part):
                        year_hint = part
                        break
                context = f"[来源: {year_hint}/{subdir.name}] {content}"
                result = _distill_with_llm(context, extract_type, pdf_path.name)
                if result:
                    result["_source_path"] = str(pdf_path)
                    result["_year_dir"] = year_hint
                    result["_problem_type"] = subdir.name
                    entries.append(result)
                    manifest[key] = fhash
                    print("OK")
                    time.sleep(1.0)  # PDF处理更慢，多等一点
                else:
                    print("FAILED")
    return entries


def _build_from_tex_dirs(
    tex_dirs: list[Path],
    manifest: dict,
    force: bool,
) -> list[dict]:
    """从 LaTeX 模板目录提炼格式规范。"""
    entries: list[dict] = []
    tex_exts = {".tex", ".cls"}
    for tex_dir in tex_dirs:
        if not tex_dir.exists():
            continue
        for path in sorted(tex_dir.rglob("*")):
            if path.suffix.lower() not in tex_exts:
                continue
            key = str(path)
            fhash = _file_hash(path)
            if not force and manifest.get(key) == fhash:
                continue
            print(f"  [KB] LaTeX提炼: {path.name} ...", end=" ", flush=True)
            content = _read_text_file(path)
            result = _distill_with_llm(content, "latex_template", path.name)
            if result:
                result["_source_path"] = str(path)
                entries.append(result)
                manifest[key] = fhash
                print("OK")
                time.sleep(0.5)
            else:
                print("FAILED")
    return entries


# ── 知识库加载接口（供各 agent 调用）────────────────────────────────────────

def load_knowledge(category: str) -> list[dict]:
    """
    供各 Agent 调用的统一接口。
    category 可选: "modeling" | "writing" | "algorithm" | "latex" | "problems"

    返回对应 JSON 文件的 entries 列表。
    若文件不存在，返回空列表（不报错，不阻塞流程）。
    """
    filename_map = {
        "modeling": "modeling_patterns.json",
        "writing": "writing_patterns.json",
        "algorithm": "algorithm_patterns.json",
        "latex": "latex_templates.json",
        "problems": "competition_problems.json",
    }
    fname = filename_map.get(category)
    if not fname:
        return []
    path = KNOWLEDGE_BASE_DIR / fname
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("entries", [])
    except Exception:
        return []


def format_knowledge_for_prompt(category: str, max_chars: int = 3000) -> str:
    """
    将知识库格式化为可直接插入 prompt 的文字块。
    max_chars 控制总长度，避免撑爆上下文窗口。
    """
    entries = load_knowledge(category)
    if not entries:
        return ""

    lines = [f"# 历届竞赛知识库：{category}"]
    total = 0
    for e in entries:
        # 按类型选择最有价值的字段
        snippet = ""
        if category == "modeling":
            snippet = (
                f"[{e.get('year', '')} {e.get('problem_type', '')}] "
                f"模型: {', '.join(e.get('core_models', []))} | "
                f"创新: {'; '.join(e.get('innovation_points', [])[:2])} | "
                f"可视化: {', '.join(e.get('visualization_types', []))}"
            )
        elif category == "writing":
            snippet = (
                f"[{e.get('source', '')}] "
                f"规则: {'; '.join(e.get('key_rules', [])[:3])} | "
                f"常见错误: {'; '.join(e.get('common_mistakes', [])[:2])}"
            )
        elif category == "algorithm":
            snippet = (
                f"[{e.get('algorithm_name', '')}] "
                f"类型: {e.get('algorithm_type', '')} | "
                f"场景: {'; '.join(e.get('use_cases', [])[:2])} | "
                f"Python: {e.get('python_equivalent', '')}"
            )
        elif category == "latex":
            snippet = (
                f"[{e.get('source', '')}] "
                f"宏包: {', '.join(e.get('key_packages', [])[:5])} | "
                f"章节: {', '.join(e.get('section_structure', []))}"
            )
        elif category == "problems":
            snippet = (
                f"[{e.get('year', '')} {e.get('problem_id', '')}题 {e.get('domain', '')}] "
                f"类型: {e.get('problem_type', '')} | "
                f"推荐模型: {', '.join(e.get('recommended_models', []))}"
            )
        if snippet:
            lines.append(f"- {snippet}")
            total += len(snippet)
            if total >= max_chars:
                lines.append("... [截断，完整数据见 knowledge_base/*.json]")
                break
    return "\n".join(lines)


# ── 主构建流程 ────────────────────────────────────────────────────────────────

def build_category(cat_name: str, force: bool = False) -> None:
    cat = CATEGORIES[cat_name]
    output_path = KNOWLEDGE_BASE_DIR / cat["output"]
    manifest = _load_manifest()

    print(f"\n{'='*60}")
    print(f"构建知识库: [{cat_name}] -> {cat['output']}")
    print(f"描述: {cat['description']}")
    print(f"消费者: {cat['consumer']}")
    print(f"{'='*60}")

    entries: list[dict] = []

    # 1. 文本文件
    if cat.get("sources"):
        entries += _build_from_text_files(
            cat["sources"], cat["extract_type"], manifest, force, cat_name
        )

    # 2. 代码目录
    if cat.get("code_dirs"):
        entries += _build_from_code_dirs(cat["code_dirs"], manifest, force)

    # 3. PDF 目录
    if cat.get("pdf_dirs"):
        entries += _build_from_pdf_dirs(
            cat["pdf_dirs"],
            cat["extract_type"],
            cat.get("sample_per_dir", 2),
            manifest,
            force,
        )

    # 4. LaTeX 目录
    if cat.get("tex_dirs"):
        entries += _build_from_tex_dirs(cat["tex_dirs"], manifest, force)

    # 保存
    output = {
        "category": cat_name,
        "description": cat["description"],
        "consumer": cat["consumer"],
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_count": len(entries),
        "entries": entries,
    }
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _save_manifest(manifest)

    print(f"\n[OK] 完成: {len(entries)} 条知识条目 -> {output_path}")


def build_all(force: bool = False) -> None:
    print("开始构建全量知识库...")
    print(f"来源目录: {GITCLONE_DIR}")
    print(f"输出目录: {KNOWLEDGE_BASE_DIR}")

    for cat_name in CATEGORIES:
        build_category(cat_name, force=force)

    print("\n" + "="*60)
    print("全量知识库构建完成！")
    for cat_name, cat in CATEGORIES.items():
        path = KNOWLEDGE_BASE_DIR / cat["output"]
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            print(f"  {cat['output']:<35} {data['entry_count']:>3} 条  -> {cat['consumer']}")


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建竞赛资料知识库")
    parser.add_argument(
        "--cat",
        choices=list(CATEGORIES.keys()),
        help="只构建指定类别（默认全量构建）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重建，忽略缓存",
    )
    args = parser.parse_args()

    if args.cat:
        build_category(args.cat, force=args.force)
    else:
        build_all(force=args.force)
