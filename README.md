# MCM Agent

数学建模竞赛全流程 AI 系统：PDF 题目 → 清洗 → 建模 → 代码求解 → LaTeX 论文 → 审校。

## 快速开始

```bash
docker compose up --build         # 首次（构建镜像，3-5 分钟）
docker compose up -d              # 后续启动
# 打开 http://localhost:8501
```

不用 Docker 也可以：

```bash
pip install -r requirements.txt
python -m ui.server                # 监控面板
python main.py                     # 无头全流程
```

## 使用流程

1. 把题目 PDF 放进 `questiontest/`
2. 数据附件放进 `vol/data/`（可选）
3. 运行 `python main.py [--start P0b|P1|P2|P3|P4|P5] [--problem A|B|C]`
4. 在面板看进度，产物在 `paper/main.tex` + `vol/outputs/`

## 流水线

| 阶段 | Agent | 产物 |
|------|-------|------|
| P0b  | `pdf_agent` | `translation/*.md` |
| P1   | `question_extractor` + 三手并行 | context + 骨架代码 + 参考文献 |
| P1.5 | `data_cleaning_agent` | `cleaned_*.csv` + EDA 图 |
| P2   | `modeling_agent` | 方程组 + 假设 |
| P3   | `code_agent` + sandbox 自愈 | `solver.py` + 结果 |
| P3.5 | `data_validator`（pre-write）| 通过或回滚到 P2/P3 |
| P4   | `writing_agent` | `paper/main.tex` |
| P4.5 | `latex_check_agent` | 修复后的 LaTeX |
| P5   | `review_agent` | `paper/review_report.json` |
| P5.5 | `data_validator`（post-review）| 通过或回滚到 P3/P4 |

> 三手并行（P1）：建模手（Opus）/ 编程手（Sonnet）/ 写作手（Sonnet），各自注入对应知识库。

## 监控面板（ui/）

三页：**Dashboard**（流水线可视化、日志、产物）/ **Assistant**（AI 对话控制，三种 persona：控制/建模/代码）/ **Experience**（历次运行经验）。

Assistant 通过 OpenAI function-calling 调用 13 个工具（暂停/重启/改配置/沙箱 exec/读 context 等）。会话持久化到 `context_store/conversations.json`。

## 环境变量（.env）

```env
OPENROUTER_API_KEY=sk-or-...      # Claude Opus/Sonnet 主路径
DEEPSEEK_API_KEY=sk-...           # 审校/自愈/提炼
QWEN_API_KEY=sk-...               # 可选备用
GITHUB_TOKEN=ghp_...              # 可选，GitHub 代码库检索
S2_API_KEY=...                    # 可选，Semantic Scholar
CROSSREF_MAILTO=your@email.com    # CrossRef 礼貌标头
```

## 模型路由

`config/model_routes.toml`：

| task        | 默认模型                          |
|-------------|----------------------------------|
| modeling    | `or:anthropic/claude-opus-4-6`   |
| codegen     | `or:anthropic/claude-sonnet-4-6` |
| writing     | `or:anthropic/claude-sonnet-4-6` |
| extraction  | `ds:deepseek-chat`               |
| default     | `ds:deepseek-chat`               |

前缀：`or:` OpenRouter、`ds:` DeepSeek、`qwen:` Dashscope。

临时覆盖：`python main.py /override_model`（写入 `config/model_override.json`）。

## 知识库

从 `gitclone/MathModel/` 提炼（97 条静态 + `experience_log.json` 自动积累）：

```bash
python -m agents.knowledge_builder              # 全量
python -m agents.knowledge_builder --cat modeling
python -m agents.knowledge_builder --force
```

文献检索自动走 OpenAlex / Semantic Scholar / CrossRef。WoS / CNKI 需手动导出 BibTeX 放入 `reference/manual/`。

## 目录

```
questiontest/          用户：题目 PDF
vol/data/              用户：数据附件
reference/manual/      用户：手动 bib
config/                LLM 路由 / 覆盖
knowledge_base/        自动生成
context_store/         运行状态
translation/           PDF→MD
paper/                 生成的论文
vol/{scripts,outputs,logs}  沙箱产物
agents/                所有 Agent
sandbox/               docker exec 封装 + 自愈
ui/                    FastAPI 面板
```

## 常见问题

- **SSL 断连** — Dashscope 偶发，系统会回退到 OpenRouter。
- **容器找不到包** — `docker exec mathmodel-sandbox pip install 包名`。
- **前端连不上大模型** — 检查 `.env` 是否被 docker-compose 挂载、Key 是否在容器环境里（`docker exec mathmodel-app env | grep API_KEY`）。
- **Windows Docker socket** — 若 `/var/run/docker.sock` 挂载失败，把 `docker-compose.yml` 里的 socket 换成 `//./pipe/docker_engine://./pipe/docker_engine`。

详细设计与路线图见 `CLAUDE.md`。
