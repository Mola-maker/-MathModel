# MCM Agent — 数学建模竞赛全流程 AI 系统

> 72小时内完成：PDF题目摄取 → 数据清洗 → 数学建模 → 代码求解 → LaTeX论文撰写 → 审校

---

## 快速开始

### 1. 放入题目

将竞赛题目 PDF 复制到：
```
questiontest/
```

### 2. 放入数据（如有附件）

将 `.xlsx` / `.csv` 数据文件复制到：
```
vol/data/
```
EDA 阶段会自动扫描此目录。容器内路径对应 `/workspace/vol/data/`。

### 3. 运行

```bash
python main.py                          # 全流程 P0b → P5
python main.py --start P1               # 跳过PDF转换，从题目解析开始
python main.py --start P3 --problem A  # 从代码生成开始，只做A题
```

### 4. 查看结果

| 产物 | 路径 |
|------|------|
| LaTeX 论文 | `paper/main.tex` |
| 参考文献 | `paper/references_draft.bib` |
| 代码骨架 | `vol/scripts/skeleton.py` |
| 求解代码 | `vol/scripts/solver.py` |
| 图表产物 | `vol/outputs/` |
| 运行日志 | `vol/logs/run.log` |
| 审校报告 | `paper/review_report.json` |

---

## 系统架构

```
                     ┌─────────────────────────────────┐
                     │         main.py 主控制器          │
                     └──────────────┬──────────────────┘
                                    │
        ┌───────────┬───────────────┼───────────────┬───────────┐
        ▼           ▼               ▼               ▼           ▼
      P0b          P1              P2              P3          P4/P5
   PdfAgent  QuestionExtractor ModelingAgent  CodeAgent  WritingAgent
   PDF→MD    题目解析+三手        数学建模      代码+自愈   LaTeX+审校
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
     建模手       编程手       写作手
   Opus 4.6   Sonnet 4.6  Sonnet 4.6
   模型框架    代码骨架     论文大纲
        │           │           │
        ▼           ▼           ▼
  建模知识库   算法知识库   写作知识库
 (75条论文)  (11条算法)  (LaTeX模板)
```

---

## 各阶段说明

### P0b — PDF 转 Markdown

- 工具：`agents/pdf_agent.py`
- 输入：`questiontest/*.pdf`
- 输出：`translation/*.md`
- 使用 pdfplumber 提取文本，保留数学符号结构

### P1 — 题目解析 + 三手并行

- 工具：`agents/question_extractor.py`
- 解析出 Q1-Q5 子问题 + 关键词
- 并行启动三个子代理（ThreadPoolExecutor）：

| 子代理 | 模型 | 主要输出 |
|--------|------|---------|
| 建模手 | Claude Opus 4.6 | 3-5个数学模型框架（含LaTeX方程、假设列表） |
| 编程手 | Claude Sonnet 4.6 | pip依赖、代码骨架 `vol/scripts/skeleton.py` |
| 写作手 | Claude Sonnet 4.6 | 论文大纲、参考文献 `paper/references_draft.bib` |

每个子代理自动注入对应知识库（见下方知识库说明）。

### P1.5 — 数据清洗（规划中）

- 读取 `vol/data/*.xlsx` / `.csv`
- 处理缺失值（均值/插值/标记），输出说明
- 输出可视化图表到 `vol/outputs/`

### P2 — 数学建模

- 工具：`agents/modeling_agent.py`，使用 Claude Opus 4.6
- 基于 P1 建模手结果，建立精确数学模型
- 输出：方程组、假设体系、变量定义

### P2.5 — 模型对比（规划中）

- 多模型并行求解，横向对比
- 评分维度：精度、计算效率、可解释性
- 输出对比报告，辅助选模决策

### P3 — 代码生成 + 自愈

- 工具：`agents/code_agent.py` + `sandbox/loop.py`
- 使用 Claude Sonnet 4.6 生成 Python 代码
- 在 Docker 沙箱中执行，失败自动修复（最多5轮）
- 逻辑错误（NaN/infeasible）直接 ABORT

### P4 — LaTeX 论文撰写

- 工具：`agents/writing_agent.py`，使用 Claude Sonnet 4.6
- 生成完整九章节 LaTeX 论文
- 参照 `rule_latex/` 中的格式规范

### P5 — 审校评分

- 工具：`agents/review_agent.py`，使用 DeepSeek
- 四维评分：模型完整性、论证严谨性、LaTeX规范、参考文献
- 输出：`paper/review_report.json`

---

## 知识库

系统从竞赛资料（`gitclone/MathModel/`）提炼出结构化知识，在推理时注入各子代理。

| 知识库文件 | 条目数 | 来源 | 注入到 |
|-----------|--------|------|--------|
| `knowledge_base/modeling_patterns.json` | 75条 | 国赛2019-2023优秀论文 + 2017美赛特等奖 | 建模手 |
| `knowledge_base/algorithm_patterns.json` | 11条 | gitclone/现代算法/*.m | 编程手 |
| `knowledge_base/writing_patterns.json` | 4条 | 技巧篇、比赛心得等 | 写作手 |
| `knowledge_base/latex_templates.json` | 4条 | 2018-2019年LaTeX模板 | 写作手 |
| `knowledge_base/competition_problems.json` | 3条 | 历年题目分析 | 建模手 |

### 重建知识库

```bash
# 全量构建（约97条，首次需5-10分钟）
python -m agents.knowledge_builder

# 只重建某一类别
python -m agents.knowledge_builder --cat modeling
python -m agents.knowledge_builder --cat writing
python -m agents.knowledge_builder --cat algorithm
python -m agents.knowledge_builder --cat latex
python -m agents.knowledge_builder --cat problems

# 强制重建（忽略缓存）
python -m agents.knowledge_builder --force
```

---

## 文献检索

系统自动检索三个学术数据库，并支持手动导入 WoS / CNKI。

### 自动检索（无需配置）

- **OpenAlex** — 免费，覆盖广
- **Semantic Scholar** — 免费，含引用数
- **CrossRef** — DOI 权威来源

### 手动导入 WoS / CNKI

WoS 和 CNKI 无公开 API，需手动操作：

1. 在 Web of Science / 知网 检索文献
2. 导出格式选 **BibTeX**
3. 将 `.bib` 文件放入：
   ```
   reference/manual/wos_你的主题.bib
   reference/manual/cnki_你的主题.bib
   ```
4. 运行时自动合并到 `paper/references_draft.bib`

详见 `reference/manual/README.md`。

---

## 模型配置

模型路由配置文件：`config/model_routes.toml`

| 阶段 | 模型 | 供应商 |
|------|------|--------|
| 建模手（modeling） | Claude Opus 4.6 | OpenRouter |
| 编程手（codegen） | Claude Sonnet 4.6 | OpenRouter |
| 写作手（writing） | Claude Sonnet 4.6 | OpenRouter |
| 知识库提炼（extraction） | DeepSeek Chat | DeepSeek 直连 |
| 审校/自愈/默认 | DeepSeek Chat | DeepSeek 直连 |

修改路由：编辑 `config/model_routes.toml`，格式：
```toml
[modeling]
models = [
  "or:anthropic/claude-opus-4-6",   # OpenRouter
  "ds:deepseek-chat",               # DeepSeek 备用
]
timeout_seconds = 120
budget_seconds = 360
```

---

## 环境配置（.env）

```env
# OpenRouter（Claude Opus/Sonnet）
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# DeepSeek（审校/自愈/提炼）
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 阿里云通义（可选备用）
QWEN_API_KEY=sk-...
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# Docker 沙箱
SANDBOX_CONTAINER=mathmodel-sandbox

# 可选
GITHUB_TOKEN=ghp_...          # GitHub代码库检索（提高频率限制）
S2_API_KEY=...                # Semantic Scholar（可选）
CROSSREF_MAILTO=your@email.com # CrossRef礼貌标头
```

---

## 目录说明

| 目录/文件 | 用途 | 操作方式 |
|-----------|------|---------|
| `questiontest/` | 竞赛题目PDF | **用户放入** |
| `vol/data/` | 竞赛数据文件(.xlsx/.csv) | **用户放入** |
| `reference/manual/` | WoS/CNKI手动导出BibTeX | **用户放入** |
| `rule_latex/` | MCM论文格式规范 | **用户放入**（可选） |
| `gitclone/MathModel/` | 竞赛资料原始库（知识库来源） | 只读 |
| `knowledge_base/` | 提炼后的结构化知识库 | 自动生成 |
| `translation/` | PDF转换后的Markdown | 自动生成 |
| `paper/` | 生成的LaTeX论文 | 自动生成 |
| `vol/scripts/` | 生成的求解代码 | 自动生成 |
| `vol/outputs/` | 图表、CSV等产物 | 自动生成 |
| `context_store/` | Agent间共享状态 | 自动管理 |
| `config/model_routes.toml` | LLM路由配置 | **用户可改** |

---

## 常见问题

**Q: 运行出现 SSL 连接错误？**
Dashscope（阿里云）直连偶发 SSL 问题。系统会自动切换到 OpenRouter 备用。如持续失败，检查网络代理设置。

**Q: 知识库是空的？**
首次使用需构建知识库：
```bash
python -m agents.knowledge_builder
```

**Q: 如何只跑某个阶段？**
```bash
python main.py --start P3   # 从代码生成开始
```

**Q: 论文引用了不存在的文献？**
手动将 WoS/CNKI BibTeX 放入 `reference/manual/`，系统会自动合并，减少幻觉。

**Q: 代码在容器里报 ModuleNotFoundError？**
在容器内安装缺失包：
```bash
docker exec mathmodel-sandbox pip install 包名
```

---

## 开发路线

- [x] P0b — PDF转Markdown
- [x] P1 — 题目解析 + 三手并行（Opus/Sonnet/DeepSeek）
- [x] P2 — 数学建模
- [x] P3 — 代码生成 + Docker沙箱自愈
- [x] P4 — LaTeX论文撰写
- [x] P5 — 审校评分
- [x] 知识库系统（97条，国赛/美赛/算法/写作）
- [x] 三数据库文献检索（OpenAlex + S2 + CrossRef）
- [x] WoS/CNKI手动导入支持
- [ ] P1.5 — 数据清洗Agent
- [ ] P2.5 — 模型对比实验
- [ ] 各阶段检查员Agent
- [ ] 全流程监控UI（见 DESIGN.md）
- [ ] 图表自动生成模板化
