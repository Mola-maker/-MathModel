# 手动文献导出目录

将从 Web of Science 或 CNKI 导出的 BibTeX 文件放在这里，AI 会自动读取并合并到论文参考文献。

## Web of Science 导出步骤

1. 进入 Web of Science，搜索关键词
2. 勾选相关文献 → 点击「导出」→ 选择「BibTeX」
3. 将下载的 `.bib` 文件放入本目录
4. 文件命名建议：`wos_关键词_日期.bib`（如 `wos_topsis_20260415.bib`）

## CNKI 导出步骤

1. 进入知网高级检索，搜索关键词
2. 勾选文献 → 点击「导出/参考文献」→ 选择「BibTeX」格式
3. 将下载的 `.bib` 文件放入本目录
4. 文件命名建议：`cnki_关键词_日期.bib`（如 `cnki_模糊评价_20260415.bib`）

## 注意

- 只支持 `.bib` 格式（BibTeX）
- 每次运行 `main.py` 时，系统会自动将本目录所有文件合并到 `paper/references_draft.bib`
- 也可手动触发：在 Python 中调用 `from agents.knowledge_base import merge_manual_bibtex_to_paper; merge_manual_bibtex_to_paper()`
