"""Writer prompt — paragraph-style writing, figure insertion rules, section structure.

Borrowed and adapted from MathModelAgent reference project.
"""

from agents.prompts.shared import (
    FORMULA_CALCULATION_RULE,
    REFERENCE_RETRIEVAL_RULE,
    AWARD_PAPER_REFERENCE,
    LANGUAGE_SELECTION_RULE,
)


def get_writer_section_prompts() -> dict[str, str]:
    """Return per-section system prompts for the WritingAgent.

    Each prompt is prefixed with _GLOBAL_RULES so every section
    inherits language selection, formula calculation, and anti-hallucination rules.
    """
    sections = {
        "abstract": ABSTRACT_PROMPT,
        "introduction": INTRODUCTION_PROMPT,
        "assumptions": ASSUMPTIONS_PROMPT,
        "model_formulation": MODEL_FORMULATION_PROMPT,
        "solution": SOLUTION_PROMPT,
        "results_analysis": RESULTS_ANALYSIS_PROMPT,
        "sensitivity": SENSITIVITY_PROMPT,
        "conclusion": CONCLUSION_PROMPT,
        "references": REFERENCES_PROMPT,
    }
    return {k: _GLOBAL_RULES + v for k, v in sections.items()}


# ── Global quality rules (assembled separately, injected into each prompt) ──
_GLOBAL_RULES = (
    "# 全局规范（最高优先级）\n\n"
    + LANGUAGE_SELECTION_RULE
    + "\n---\n"
    + FORMULA_CALCULATION_RULE
    + "\n---\n"
    + REFERENCE_RETRIEVAL_RULE
    + "\n---\n"
    + AWARD_PAPER_REFERENCE
    + "\n---\n"
)

# ── Shared writing rules ──

WRITING_STYLE_RULES = """
# 写作风格规范

## 段落式写作（关键！）
**严格禁止分点式论述（bullet points / numbered lists）出现在正文中。**
必须将分点式内容转换为流畅的论文级段落式语言。

错误示例：
```
关键发现：
1. 右偏分布：大多数国家奖牌数较少
2. 均值 > 中位数：数据被高奖牌数国家拉高
```

正确示例：
```
分布分析揭示了奖牌数据的若干显著特征。大多数国家获得的奖牌数较少，
中位数仅为5枚，而少数强国累积了显著更高的总量，形成了右偏分布。
均值超过中位数进一步证实了这一点，表明平均值被高表现国家抬高。
```

## 过渡连接词
| 类型 | 过渡词 |
|------|--------|
| 递进 | Furthermore, Moreover, Additionally, In addition |
| 因果 | Therefore, Thus, Consequently, As a result |
| 转折 | However, Nevertheless, In contrast |
| 举例 | For example, Specifically, As illustrated by |
| 总结 | In summary, To conclude, Overall |

## 语态与时态
- 被动语态为主：强调客观性 (The model was trained using...)
- 主动语态：强调研究贡献 (We develop a novel framework...)
- 现在时：描述模型、公式、一般性结论
- 过去时：描述实验过程、数据处理步骤

## 避免事项
- 避免口语化表达（"a lot of" → "numerous"）
- 避免主观评价词（"very good" → 用数据支撑）
- 避免过长句子（每句不超过30词）
- 禁止分点列表出现在正文论述中

## 图表引用规范
- 引入：As illustrated in Figure X, [图表核心内容].
- 解读：Figure X shows/depicts/demonstrates that [趋势/关系].
- 对比：Compared with [对比对象], [研究对象] achieves [结果].
- **每幅图表至少配3行分析**

## 图片插入格式（LaTeX）
\\begin{figure}[H]
\\centering
\\includegraphics[width=0.8\\textwidth]{figures/FILENAME.png}
\\caption{Description}
\\label{fig:label}
\\end{figure}

## 参数定量依据
所有模型参数必须有明确来源：数据统计 / 文献引用 / 校准实验。

## 因果 vs 相关
预测准确率高 ≠ 因果关系！必须明确声明相关性而非因果。
"""

# ── Per-section prompts ──

ABSTRACT_PROMPT = f"""写 MCM 标准摘要（Summary Sheet），控制在 250 词以内。
结构：问题重述(1-2句) → 每个问题的方法+结果(各2-3句) → 总结(1-2句)。
关键词 4-5个。
输出纯英文文本（无 LaTeX 命令）。

{WRITING_STYLE_RULES}
"""

INTRODUCTION_PROMPT = f"""写竞赛论文引言（Introduction），约 300-400 词。
涵盖：背景介绍、问题的重要性、本文方法概述、论文结构说明。
输出 LaTeX 格式，可含简单公式。

{WRITING_STYLE_RULES}
"""

ASSUMPTIONS_PROMPT = f"""将假设列表整理为 MCM 论文 Assumptions 章节。
每条假设含编号、假设内容、合理性说明（Justification）。
格式：
\\begin{{enumerate}}
  \\item \\textbf{{假设内容}} — 合理性说明
\\end{{enumerate}}

{WRITING_STYLE_RULES}
"""

MODEL_FORMULATION_PROMPT = f"""将数学模型推导过程写成正式的 Model Formulation 章节，约 600-800 词。
含变量定义表（用 tabularx 环境）、核心方程推导、目标函数说明。

公式用 \\begin{{equation}}...\\end{{equation}}，
多个公式用 \\begin{{align}}...\\end{{align}}。
变量定义表用 \\begin{{table}}[H] + \\begin{{tabularx}} 环境。

## 公式展示要求（每个公式必须包含三层）
1. **符号定义层**：标准 LaTeX 公式 + 每个符号的说明
2. **数值计算层**：代入代码实际输出的真实数值，逐步展示计算过程
   - 数值来自 vol/outputs/ 目录下的代码运行结果
   - 必须具体到小数点后 4 位
   - 禁止使用"代入数据得到结果"这种省略写法
3. **结果解释层**：对计算结果的物理意义/实际意义说明

## 模型选择论证必须包含
1. 为什么选这个模型（1-2句）
2. 与备选方案的对比（参考 model_comparison 字段）

{WRITING_STYLE_RULES}
"""

SOLUTION_PROMPT = f"""将求解过程写成 Solution 章节，约 400-500 词。
包含算法描述、求解步骤、收敛性说明。
可用 \\begin{{algorithm}}[H] + algpseudocode 环境描述伪代码。

{WRITING_STYLE_RULES}
"""

RESULTS_ANALYSIS_PROMPT = f"""将数值结果写成 Results and Analysis 章节，约 500-600 词。
包含主要结果描述、图表引用、结果解读。

## 数值真实性要求（最高优先级）
所有出现在本章节的数值，必须来自代码输出（vol/outputs/ 目录）。
写作前必须读取以下文件获取真实数据：
- context_store/context.json 中的 code_results 字段
- vol/outputs/ 目录下的 CSV/JSON 文件
禁止在没有数据支撑的情况下给出任何数值结论。

## 图表引用格式
\\begin{{figure}}[H]
\\centering
\\includegraphics[width=0.8\\textwidth]{{figures/FILENAME.png}}
\\caption{{Description}}
\\label{{fig:label}}
\\end{{figure}}

每张图至少配 3 行解读分析，必须引用图中的具体数值（如"the R² value reaches 0.9231"）。

{WRITING_STYLE_RULES}
"""

SENSITIVITY_PROMPT = f"""将敏感性分析写成 Sensitivity Analysis 章节，约 300-400 词。
写作骨架：
1. 参数选择与范围：指出关键参数及取值范围
2. 性能与稳健性评估：评价指标与对比结论
3. 可视化与表格支撑

引用敏感性分析图：
\\begin{{figure}}[H]
\\centering
\\includegraphics[width=0.75\\textwidth]{{figures/fig_sensitivity.png}}
\\caption{{Sensitivity analysis of key parameters}}
\\label{{fig:sensitivity}}
\\end{{figure}}

{WRITING_STYLE_RULES}
"""

CONCLUSION_PROMPT = f"""写 Conclusion 章节，约 250-300 词。
总结主要发现、模型局限性、未来工作方向。
输出 LaTeX 格式。

{WRITING_STYLE_RULES}
"""

REFERENCES_PROMPT = f"""根据调研阶段的参考资料列表，生成 BibTeX 格式的参考文献。

## 生成规则
- 每条 BibTeX 必须包含：author, title, year, journal/booktitle, doi 或 url
- 输出纯 BibTeX（不含其他内容）
- 每篇文献在全文中只能被引用一次

## 严格禁止（AI 幻觉防范）
- 禁止虚构任何字段（作者名、期刊名、卷号、页码、DOI 均必须真实）
- 若某条文献无法确认真实性，改为引用以下可验证的替代源：
  - 公开数据集（kaggle, UCI, government open data）
  - 官方标准文件（WHO, ISO, NIST）
  - 知名教材（附 ISBN）

## 最低数量要求
- 全文总引用 ≥ 20 条
- 每道子问题至少 5 条相关引用
- 引用类型：1 综述 + 2 方法论文 + 2 应用论文（每道题）

{REFERENCE_RETRIEVAL_RULE}
"""

# ── LaTeX template ──

MCM_LATEX_TEMPLATE = r"""\documentclass[12pt,a4paper]{article}

% ── Geometry & Layout ──
\usepackage[margin=1in]{geometry}
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\lhead{\small Team \# XXXXXXX}
\rhead{\small Page \thepage\ of \pageref{LastPage}}
\renewcommand{\headrulewidth}{0.4pt}
\usepackage{lastpage}

% ── Mathematics ──
\usepackage{amsmath,amssymb,amsfonts,mathtools}
\usepackage{bm}
\numberwithin{equation}{section}

% ── Tables & Figures ──
\usepackage{graphicx}
\graphicspath{{figures/}}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{float}
\usepackage{subcaption}
\usepackage{caption}
\captionsetup{font=small,labelfont=bf,skip=6pt}

% ── Code & Algorithm ──
\usepackage{algorithm}
\usepackage{algpseudocode}
\usepackage{listings}
\lstset{
  basicstyle=\ttfamily\small,
  frame=single,
  breaklines=true,
  numbers=left,
  numberstyle=\tiny\color{gray},
  keywordstyle=\color{blue},
  commentstyle=\color{gray},
}

% ── Colors & Links ──
\usepackage[dvipsnames]{xcolor}
\usepackage[colorlinks=true,linkcolor=NavyBlue,citecolor=ForestGreen,urlcolor=RoyalPurple]{hyperref}

% ── Bibliography ──
\usepackage{natbib}
\bibliographystyle{plainnat}

% ── Misc ──
\usepackage{enumitem}
\setlist{nosep,leftmargin=1.5em}
\usepackage{parskip}
\setlength{\parskip}{0.4em}

% ── Title ──
\title{\Large\bfseries $title}
\author{Team \# XXXXXXX}
\date{\today}

\begin{document}

%% ====================== Summary Sheet ======================
\thispagestyle{empty}
\begin{center}
  \large\bfseries Summary Sheet
\end{center}
\vspace{0.5em}
\hrule\vspace{1em}

$abstract

\vspace{1em}\hrule
\newpage

%% ====================== Main Paper ======================
\setcounter{page}{1}
\tableofcontents
\newpage

\section{Introduction}
$introduction

\section{Assumptions and Justifications}
$assumptions

\section{Model Formulation}
$model_formulation

\section{Solution and Implementation}
$solution

\section{Results and Analysis}
$results_analysis

\section{Sensitivity Analysis}
$sensitivity

\section{Strengths and Weaknesses}
\subsection{Strengths}
$strengths

\subsection{Weaknesses}
$weaknesses

\section{Conclusion}
$conclusion

\newpage
\bibliography{references}

\newpage
\appendix

\section{Appendix A: Core Code Listings}

% ── Data Preprocessing ──
\subsection{Data Preprocessing and Cleaning}
\lstinputlisting[language=Python,caption={Data Cleaning and EDA Script (eda.py)}]{../../vol/scripts/eda.py}

% ── Main Solver ──
\subsection{Model Implementation}
\lstinputlisting[language=Python,caption={Main Solver Script (solver.py)}]{../../vol/scripts/solver.py}

% ── Sensitivity Analysis ──
\subsection{Sensitivity Analysis}
\lstinputlisting[language=Python,caption={Sensitivity Analysis Script}]{../../vol/scripts/sensitivity_analysis.py}

\section{Appendix B: Numerical Calculation Details}
% 此节由 WritingAgent 根据代码输出自动填充公式三层结构
% Each formula: (1) symbolic definition, (2) numerical substitution, (3) interpretation
$numerical_calculations

\end{document}
"""
