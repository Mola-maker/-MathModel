<div align="center">

# 🧮 MathModel Agent

**数学建模竞赛全流程 AI 流水线**

PDF 题目 → 数据清洗 → 数学建模 → 代码求解 → LaTeX 论文 → 自动审校

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-Apache_2.0-D22128)](https://opensource.org/licenses/Apache-2.0)
[![Pylint](https://img.shields.io/badge/Pylint-8.96%2F10-brightgreen)](https://pylint.org/)

---

*Hi, I'm mola — a JLU freshman who built this almost entirely through prompting Claude.*
*If you're a seasoned engineer and spot something worth improving, every issue and PR is genuinely appreciated.*

</div>

---

## 概览

MathModel Agent 是一套面向 MCM/ICM 等数学建模竞赛的**端到端 AI 自动化系统**。输入竞赛题目 PDF 和数据附件，输出一篇可编译的 LaTeX 论文。

整个过程由 11 个专职 Agent 串联完成，每个 Agent 独立调用 LLM，通过共享的 `context.json` 传递中间状态。代码执行在 Docker 沙箱中隔离运行，配备自愈循环（最多 N 次自动修复）。

```
PDF ──► P0b OCR ──► P1 题目解析 ──► P1.5 数据清洗 ──► P1.7 数据仿真
                                                              │
P5.5 数据审计 ◄── P5 审校评分 ◄── P4.5 LaTeX检查 ◄── P4 论文撰写
                                                              │
                                   P3.5 数据验证 ◄── P3 代码求解 ◄── P2 数学建模
```

---

## 特性

- **全流程自动化** — 11 个阶段，从 PDF 到论文一键完成
- **自愈沙箱** — 代码执行失败自动修复，回滚策略可配置
- **实时监控面板** — FastAPI + SSE 推流，三页 Dashboard / AI 助手 / 经验库
- **AI 控制助手** — 通过自然语言暂停流水线、切换模型、执行沙箱代码
- **插件系统** — 支持自定义 Plugin / MCP 工具服务器 / Skill 片段 / Persona
- **多模型路由** — OpenRouter / DeepSeek / Dashscope，按任务类型分配
- **知识库增强** — 静态建模模式库 + 运行时经验自动积累
- **文献检索** — OpenAlex / Semantic Scholar / CrossRef 自动检索

---

## 快速开始

### 前置要求

- Docker & Docker Compose
- API Key：至少一个（OpenRouter 推荐，DeepSeek 备用）
- PaddleOCR 容器（P0b 阶段，可选）：参考 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)

### Docker 启动（推荐）

```bash
# 1. 复制环境变量模板
cp .env.example .env        # 填入你的 API Key

# 2. 启动
docker compose up --build   # 首次构建（3-5 分钟）
docker compose up -d        # 后续启动

# 3. 打开面板
open http://localhost:8501
```

### 裸机启动

```bash
pip install -r requirements.txt

python -m ui.server          # 监控面板（:8501）
# 或直接运行全流程
python main.py               # 从 P0b 开始
python main.py --start P2    # 从指定阶段续跑
```

---

## 使用流程

```
1. 将竞赛题目 PDF 放入  questiontest/
2. 将数据附件放入      vol/data/         （可选）
3. 将手动参考文献放入  reference/manual/  （可选，BibTeX 格式）
4. 打开面板 → 选择题目 → 点击运行
```

或命令行：

```bash
python main.py [--start PHASE] [--problem A|B|C|D]

# PHASE 可选：P0b P1 P1.5 P1.7 P2 P2.5 P3 P3.5 P4 P4.5 P5 P5.5
```

产物输出：`paper/main.tex`（论文）、`vol/outputs/`（图表/数据）

---

## 流水线阶段

| 阶段 | Agent | 说明 | 主要产物 |
|------|-------|------|----------|
| **P0b** | `pdf_agent` | PaddleOCR PDF → Markdown | `translation/*.md` |
| **P1** | `question_extractor` | 三手并行解析题目 | context 结构体、骨架代码、参考文献 |
| **P1.5** | `data_cleaning_agent` | 数据探查 + 清洗 | `cleaned_*.csv`、EDA 图 |
| **P1.7** | `data_simulation` | 无数据时合成仿真数据 | 仿真 CSV |
| **P2** | `modeling_agent` | 选模型 + 推导方程组 | 建模方案、假设列表 |
| **P2.5** | `matlab_viz` | Octave/MATLAB 辅助可视化 | 额外图表 |
| **P3** | `code_agent` + sandbox | 代码生成 → Docker 执行 → 自愈 | `solver.py`、结果数据 |
| **P3.5** | `data_validator` | 写作前数据质量门控 | 通过 / 回滚到 P2·P3 |
| **P4** | `writing_agent` | 全文 LaTeX 写作 | `paper/main.tex` |
| **P4.5** | `latex_check_agent` | 编译检查 + 自动修复 | 可编译 LaTeX |
| **P5** | `review_agent` | 多维度评分审校 | `review_report.json` |
| **P5.5** | `data_validator` | 审后数据一致性核验 | 通过 / 回滚 |

> **P1 三手并行**：建模手（Opus）/ 编程手（Sonnet）/ 写作手（Sonnet）各自注入专属知识库，独立生成方向草案后汇聚。

---

## 监控面板

面板地址：`http://localhost:8501`，三个页面：

| 页面 | 功能 |
|------|------|
| **Dashboard** | 流水线状态可视化、实时日志流、产物文件列表、分数看板 |
| **Assistant** | AI 对话控制助手，支持暂停/重启/改配置/沙箱执行/读取 context |
| **Experience** | 历次运行经验浏览，按阶段/关键词过滤 |

### AI 助手工具

助手通过 OpenAI function-calling 调用 13 个内置工具，自然语言示例：

```
"把建模阶段的模型换成 claude-opus-4-7"
"从 P3 重新运行"
"在沙箱里执行：import pandas as pd; print(pd.__version__)"
"设置最大自愈次数为 5"
```

---

## 插件与扩展

系统支持四类扩展，均在启动时自动加载且 **幂等**：

### 1. Plugin（Python 插件）

在 `plugins/<name>/` 下创建包，继承 `Plugin` 基类：

```python
from agents.extensions.plugin import Plugin, tool, on_event

class MyPlugin(Plugin):
    name = "my_plugin"

    @tool(description="查询天气")
    def get_weather(self, city: str) -> str:
        return f"{city} 晴"

    @on_event("P3_complete")
    def on_code_done(self, payload: dict) -> None:
        print("代码执行完成:", payload)
```

在 `config/plugins.toml` 中启用：

```toml
enabled = ["my_plugin"]
```

### 2. MCP 工具服务器

在 `config/mcp_servers.json` 中声明外部工具服务器（stdio 传输），其工具自动合并进 AI 助手的工具列表。

### 3. Skill 片段

在 `config/skills/*.md` 中放置 Markdown 片段，前置 YAML frontmatter 声明触发词，助手回复前自动注入匹配的 Skill：

```yaml
---
name: latex_table_template
description: LaTeX 三线表模板
triggers: [latex, 表格, table]
---
\begin{tabular}{...}
```

### 4. Persona

内置三种助手角色（`config/personas/*.yaml`）：
- **controller** — 流水线控制，全工具权限
- **modeler** — 专注建模推导，注入建模知识库
- **coder** — 专注代码调试，注入算法库

---

## 模型路由

在 `config/model_routes.toml` 中为每类任务指定模型列表（按优先级排列，失败自动降级）：

```toml
[modeling]
models = ["or:anthropic/claude-opus-4-7", "or:anthropic/claude-sonnet-4-6"]
timeout_seconds = 120
budget_seconds  = 360

[codegen]
models = ["or:anthropic/claude-sonnet-4-6", "ds:deepseek-chat"]
timeout_seconds = 60

[writing]
models = ["or:anthropic/claude-sonnet-4-6"]
timeout_seconds = 90

[extraction]
models = ["ds:deepseek-chat", "qwen:qwen-plus"]
timeout_seconds = 30
```

**模型前缀：**`or:` OpenRouter · `ds:` DeepSeek · `qwen:` Dashscope

面板 Assistant 也可实时修改：`"把 modeling 的超时改成 180 秒"`

---

## 环境变量

在项目根目录创建 `.env`：

```env
# ── 必填（至少一个）──────────────────────────────────────────
OPENROUTER_API_KEY=sk-or-...       # OpenRouter（Claude Opus/Sonnet）
DEEPSEEK_API_KEY=sk-...            # DeepSeek（低成本任务）

# ── 可选 ────────────────────────────────────────────────────
QWEN_API_KEY=sk-...                # 阿里云 Dashscope 备用
GITHUB_TOKEN=ghp_...               # GitHub 代码库检索（knowledge_builder）
S2_API_KEY=...                     # Semantic Scholar 文献检索
CROSSREF_MAILTO=your@email.com     # CrossRef 礼貌请求头

# ── 路径覆盖（Docker 内已预设，裸机可不填）──────────────────
SANDBOX_CONTAINER=mathmodel-sandbox
VOL_HOST=/app/vol
PAPER_DIR=/app/paper
QUESTIONTEST_DIR=/app/questiontest
```

---

## 知识库

```bash
# 从 gitclone/MathModel/ 提炼建模模式（首次运行或题目类型改变后执行）
python -m agents.knowledge_builder              # 全量构建
python -m agents.knowledge_builder --cat modeling   # 仅重建建模库
python -m agents.knowledge_builder --force          # 强制覆盖

# 经验库（自动积累，无需手动操作）
# 每次流水线完成后自动写入 knowledge_base/experience_log.json
```

文献来源优先级：手动 BibTeX (`reference/manual/`) > OpenAlex > Semantic Scholar > CrossRef

---

## 目录结构

```
MathModel/
├── agents/                  # 所有 Agent 实现
│   ├── extensions/          # 插件系统（Plugin / MCP / Skill / Persona）
│   ├── orchestrator.py      # LLM 调用封装（重试 / 路由 / 工具调用）
│   ├── pipeline.py          # 流水线调度器（PhaseRunner + 回滚逻辑）
│   ├── model_router.py      # 任务级模型路由
│   └── events.py            # 跨进程结构化事件总线（JSONL）
├── sandbox/
│   ├── loop.py              # 自愈执行循环
│   ├── healer.py            # LLM 驱动代码修复
│   └── runner.py            # Docker exec 封装
├── ui/
│   └── server.py            # FastAPI 面板（SSE / 工具调用 / 会话管理）
├── config/
│   ├── model_routes.toml    # 模型路由配置
│   ├── plugins.toml         # 插件启用列表
│   ├── mcp_servers.json     # MCP 工具服务器
│   └── skills/              # Skill 片段目录
├── knowledge_base/          # 自动生成的建模模式库 + 经验日志
├── questiontest/            # ← 放入竞赛题目 PDF
├── vol/data/                # ← 放入数据附件
├── reference/manual/        # ← 放入手动 BibTeX
├── paper/                   # 生成的 LaTeX 论文
├── translation/             # PDF → Markdown 中间产物
├── context_store/           # 流水线运行状态（context.json）
├── main.py                  # 命令行入口
└── docker-compose.yml
```

---

## 自愈机制

P3 代码执行失败时：

```
执行失败
  └─ healer.py 分析 stderr
       ├─ 逻辑错误？ → 回滚到 P2 重新建模
       └─ 语法/运行错误？ → LLM 生成修复方案 → 重试
            └─ 超过 max_heal_iterations？ → 回滚到 P2
```

`max_heal_iterations` 可在面板中实时修改，也可在 `config/pipeline.json` 中预设。

---

## 常见问题

**Q: PaddleOCR 容器怎么配置？**
P0b 阶段调用宿主机上已有的 PaddleOCR 容器（`stoic_shaw`）。若容器名不同，在 `docker-compose.yml` 中修改 `PADDLEOCR_CONTAINER` 环境变量，或参考 [PaddleOCR 部署文档](https://github.com/PaddlePaddle/PaddleOCR)。

**Q: Docker socket 在 Windows 上挂载失败？**
将 `docker-compose.yml` 中的 `/var/run/docker.sock` 替换为 `//./pipe/docker_engine://./pipe/docker_engine`。

**Q: 沙箱里缺少 Python 包？**
```bash
docker exec mathmodel-sandbox pip install 包名
# 或在面板 Assistant 中：
"在沙箱里安装 scikit-learn"
```

**Q: API 频繁断连 / 超时？**
Dashscope 偶发 SSL 断连，系统会自动降级到下一个模型。也可在 `config/model_routes.toml` 里将 OpenRouter 模型上移到列表首位。

**Q: LaTeX 编译报错？**
P4.5 阶段会自动检查并尝试修复。若失败，产物在 `paper/latex_check_report.json`，可用面板 Assistant："分析一下 LaTeX 错误并修复"。

**Q: 如何跳过某个阶段？**
```bash
python main.py --start P4    # 从 P4 论文写作开始，跳过前序阶段
```

---

## 路线图

- [ ] 更好的 LaTeX 渲染（当前最薄弱环节）
- [ ] MATLAB 深度集成（目前用 Octave 兼容层）
- [ ] 减少幻觉：更强的 context 压缩与事实核验
- [ ] Web UI 重构（当前为 Alpine.js 单文件，计划迁移 React）
- [ ] 多题目并行（当前串行）

---

## 贡献

欢迎任何形式的贡献——Issue 反馈、PR、讨论都非常欢迎。

如果你是经验丰富的工程师，尤其希望得到以下方向的指导：架构设计、幻觉抑制策略、LaTeX 生成质量、MATLAB 集成方案。

---

<div align="center">
Built by <a href="https://github.com/Mola-maker">mola</a> · JLU · 2025
</div>
