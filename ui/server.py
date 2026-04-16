"""MCM Pipeline Dashboard — FastAPI backend.

Endpoints:
  GET  /                          → index.html
  GET  /api/status                → pipeline status
  GET  /api/context               → raw context.json
  GET  /api/files                 → file listing
  GET  /api/figures/{filename}    → serve figure
  GET  /api/logs                  → run.log tail
  GET  /api/reports/{type}        → review / latex report JSON
  GET  /api/knowledge             → knowledge base entry counts
  POST /api/run/{phase}           → start pipeline from phase
  POST /api/stop                  → terminate pipeline
  GET  /api/pipeline-output       → SSE: pipeline stdout
  GET  /api/sse                   → SSE: phase change events
  GET  /api/config                → get model_routes + pipeline settings
  POST /api/config                → save model_routes + pipeline settings
  POST /api/chat                  → AI assistant (returns {reply, actions})
  GET  /api/experience            → experience log entries (filterable by phase)

Usage:
    python -m ui.server           # port 8501
    python -m ui.server --port 9000
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError:
    print("需要安装 fastapi 和 uvicorn:  pip install fastapi uvicorn")
    sys.exit(1)

# ── Paths ──
BASE = Path(__file__).parent.parent
CONTEXT_PATH = BASE / "context_store" / "context.json"
PAPER_DIR = BASE / "paper"
VOL_DIR = BASE / "vol"
DATA_DIR = VOL_DIR / "data"
FIGURES_DIR = VOL_DIR / "outputs" / "figures"
LOGS_DIR = VOL_DIR / "logs"
KB_DIR = BASE / "knowledge_base"
STATIC_DIR = Path(__file__).parent / "static"
MODEL_ROUTES_PATH = BASE / "config" / "model_routes.toml"
PIPELINE_CFG_PATH = BASE / "config" / "pipeline.json"

# ── Phase metadata ──
PHASE_META = {
    "P0b":  {"name": "PDF 转译",   "agent": "pdf_agent.py",          "icon": "doc"},
    "P1":   {"name": "题目解析",   "agent": "question_extractor.py", "icon": "search"},
    "P1.5": {"name": "数据清洗",   "agent": "data_cleaning_agent.py","icon": "data"},
    "P2":   {"name": "数学建模",   "agent": "modeling_agent.py",     "icon": "model"},
    "P3":   {"name": "代码求解",   "agent": "code_agent.py",         "icon": "code"},
    "P3.5": {"name": "数据验证",   "agent": "data_validator.py",     "icon": "check"},
    "P4":   {"name": "论文撰写",   "agent": "writing_agent.py",      "icon": "write"},
    "P4.5": {"name": "LaTeX 检查", "agent": "latex_check_agent.py",  "icon": "latex"},
    "P5":   {"name": "审校评分",   "agent": "review_agent.py",       "icon": "review"},
    "P5.5": {"name": "数据审计",   "agent": "data_validator.py",     "icon": "audit"},
}

PHASE_ORDER = ["P0b", "P1", "P1.5", "P2", "P3", "P3.5", "P4", "P4.5", "P5", "P5.5"]

PHASE_COMPLETE_MAP = {
    "init": set(),
    "P0b_complete": {"P0b"},
    "P1_extraction_complete": {"P0b", "P1"},
    "P1.5_complete": {"P0b", "P1", "P1.5"},
    "P1.5_skipped": {"P0b", "P1", "P1.5"},
    "P2_complete": {"P0b", "P1", "P1.5", "P2"},
    "P3_complete": {"P0b", "P1", "P1.5", "P2", "P3"},
    "P3_logic_err": {"P0b", "P1", "P1.5", "P2"},
    "P3.5_complete": {"P0b", "P1", "P1.5", "P2", "P3", "P3.5"},
    "P4_complete": {"P0b", "P1", "P1.5", "P2", "P3", "P3.5", "P4"},
    "P4.5_complete": {"P0b", "P1", "P1.5", "P2", "P3", "P3.5", "P4", "P4.5"},
    "P5_complete": {"P0b", "P1", "P1.5", "P2", "P3", "P3.5", "P4", "P4.5", "P5"},
    "P5.5_complete": set(PHASE_ORDER),
}

app = FastAPI(title="MCM Pipeline Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_pipeline_proc: subprocess.Popen | None = None


# ─────────────────────────────────────────────────────── helpers ──

def _load_context() -> dict:
    if not CONTEXT_PATH.exists():
        return {"phase": "init"}
    return json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))


def _phase_status(ctx: dict) -> dict[str, str]:
    current_phase = ctx.get("phase", "init")
    completed: set[str] = set()
    if current_phase in PHASE_COMPLETE_MAP:
        completed = PHASE_COMPLETE_MAP[current_phase]
    else:
        for key in sorted(PHASE_COMPLETE_MAP, key=len, reverse=True):
            if current_phase.startswith(key.split("_")[0]):
                completed = PHASE_COMPLETE_MAP[key]
                break

    statuses: dict[str, str] = {}
    found_running = False
    for p in PHASE_ORDER:
        if p in completed:
            statuses[p] = "completed"
        elif not found_running:
            if "logic_err" in current_phase and p == "P3":
                statuses[p] = "error"
            elif _pipeline_proc and _pipeline_proc.poll() is None:
                statuses[p] = "running"
            else:
                statuses[p] = "pending"
            found_running = True
        else:
            statuses[p] = "pending"
    return statuses


# ─────────────────────────────────────────── config read / write ──

def _parse_toml_config() -> dict[str, dict]:
    """Parse model_routes.toml into a plain dict (handles multi-line arrays)."""
    if not MODEL_ROUTES_PATH.exists():
        return {}
    text = MODEL_ROUTES_PATH.read_text(encoding="utf-8")
    result: dict[str, dict] = {}
    current: str | None = None
    # Join multi-line arrays: collapse lines between [ and ]
    # Flatten multi-line arrays onto one line first
    joined_lines: list[str] = []
    in_array = False
    array_buf = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if not in_array:
                joined_lines.append(stripped)
            continue
        if in_array:
            array_buf += " " + stripped
            if "]" in stripped:
                joined_lines.append(array_buf)
                array_buf = ""
                in_array = False
        else:
            if "= [" in stripped and "]" not in stripped.split("= [", 1)[1]:
                in_array = True
                array_buf = stripped
            else:
                joined_lines.append(stripped)

    for line in joined_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(\w+(?:\.\w+)?)\]$", line)
        if m:
            current = m.group(1)
            result[current] = {}
            continue
        if current is None:
            continue
        kv = re.match(r'^(\w+)\s*=\s*(.+)$', line)
        if not kv:
            continue
        key, val = kv.group(1), kv.group(2).strip()
        if "[" in val and "]" in val:
            items = re.findall(r'"([^"]+)"', val)
            result[current][key] = items
        elif val.lstrip("-").isdigit():
            result[current][key] = int(val)
        else:
            result[current][key] = val.strip('"')
    return result


def _write_toml_config(config: dict[str, dict]) -> None:
    """Write model_routes.toml from a plain dict."""
    lines = [
        "# ─────────────────────────────────────────────────────────────────────────────",
        "# 模型路由配置  (由 Dashboard 自动生成)",
        "# 前缀: or:→OpenRouter  ds:→DeepSeek  qwen:→Dashscope",
        "# ─────────────────────────────────────────────────────────────────────────────",
    ]
    for section, vals in config.items():
        lines.append(f"\n[{section}]")
        for key, val in vals.items():
            if isinstance(val, list):
                quoted = ", ".join(f'"{v}"' for v in val)
                lines.append(f"{key} = [{quoted}]")
            else:
                lines.append(f"{key} = {val}")
    MODEL_ROUTES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_pipeline_cfg() -> dict:
    defaults: dict[str, Any] = {
        "max_rollbacks": 2,
        "max_heal_iterations": 3,
        "max_tokens": {
            "modeling": 4096,
            "codegen": 8192,
            "writing": 8192,
            "extraction": 2048,
            "default": 2048,
        },
    }
    if not PIPELINE_CFG_PATH.exists():
        return defaults
    try:
        saved = json.loads(PIPELINE_CFG_PATH.read_text(encoding="utf-8"))
        defaults.update(saved)
    except Exception:
        pass
    return defaults


def _save_pipeline_cfg(cfg: dict) -> None:
    PIPELINE_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PIPELINE_CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────── AI chat helper ──

_CHAT_SYSTEM = """\
你是 MCM 数学建模流水线的智能控制助手。你可以帮助用户：
1. 了解当前流水线状态
2. 暂停 / 重启流水线
3. 修改模型配置（切换LLM、调整超时）
4. 修改流水线配置（循环次数、Token限制）

当你需要执行操作时，在回复中嵌入 JSON 行动块（每个单独一行）：
<action>{"type":"pause"}</action>
<action>{"type":"restart","phase":"P3"}</action>
<action>{"type":"set_model","task":"modeling","model":"ds:deepseek-chat"}</action>
<action>{"type":"set_timeout","task":"modeling","value":180}</action>
<action>{"type":"set_budget","task":"modeling","value":360}</action>
<action>{"type":"set_pipeline","key":"max_rollbacks","value":3}</action>
<action>{"type":"set_tokens","task":"codegen","value":8192}</action>

规则：
- 默认用中文回复
- 每次操作前先简短说明要做什么，再给出行动块
- 行动块执行完毕后系统会反馈执行结果
- 如果用户没有明确说暂停，在修改配置后问是否需要重启
"""


def _execute_chat_actions(actions: list[dict]) -> list[str]:
    """Execute parsed actions and return human-readable results."""
    global _pipeline_proc
    results: list[str] = []

    for act in actions:
        t = act.get("type")
        try:
            if t == "pause":
                if _pipeline_proc and _pipeline_proc.poll() is None:
                    _pipeline_proc.terminate()
                    _pipeline_proc = None
                    results.append("✓ 流水线已暂停")
                else:
                    results.append("ℹ 流水线当前未在运行")

            elif t == "restart":
                phase = act.get("phase", "P0b")
                if phase not in PHASE_ORDER:
                    results.append(f"✗ 无效阶段: {phase}")
                    continue
                if _pipeline_proc and _pipeline_proc.poll() is None:
                    _pipeline_proc.terminate()
                _pipeline_proc = subprocess.Popen(
                    [sys.executable, "main.py", "--start", phase],
                    cwd=str(BASE),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                results.append(f"✓ 已从 {phase} 重新启动 (pid={_pipeline_proc.pid})")

            elif t == "set_model":
                task = act.get("task")
                model = act.get("model")
                if not task or not model:
                    results.append("✗ set_model 需要 task 和 model")
                    continue
                cfg = _parse_toml_config()
                if task not in cfg:
                    cfg[task] = {"models": [], "timeout_seconds": 60, "budget_seconds": 180}
                models = cfg[task].get("models", [])
                if model not in models:
                    models.insert(0, model)
                else:
                    models.remove(model)
                    models.insert(0, model)
                cfg[task]["models"] = models
                _write_toml_config(cfg)
                results.append(f"✓ [{task}] 首选模型已设为 {model}")

            elif t == "set_timeout":
                task = act.get("task")
                val = act.get("value")
                if task and val is not None:
                    cfg = _parse_toml_config()
                    if task in cfg:
                        cfg[task]["timeout_seconds"] = int(val)
                        _write_toml_config(cfg)
                        results.append(f"✓ [{task}] timeout 已设为 {val}s")

            elif t == "set_budget":
                task = act.get("task")
                val = act.get("value")
                if task and val is not None:
                    cfg = _parse_toml_config()
                    if task in cfg:
                        cfg[task]["budget_seconds"] = int(val)
                        _write_toml_config(cfg)
                        results.append(f"✓ [{task}] budget 已设为 {val}s")

            elif t == "set_pipeline":
                key = act.get("key")
                val = act.get("value")
                if key and val is not None:
                    pc = _load_pipeline_cfg()
                    pc[key] = val
                    _save_pipeline_cfg(pc)
                    results.append(f"✓ pipeline.{key} 已设为 {val}")

            elif t == "set_tokens":
                task = act.get("task")
                val = act.get("value")
                if task and val is not None:
                    pc = _load_pipeline_cfg()
                    pc.setdefault("max_tokens", {})[task] = int(val)
                    _save_pipeline_cfg(pc)
                    results.append(f"✓ max_tokens[{task}] 已设为 {val}")

        except Exception as e:
            results.append(f"✗ 执行 {t} 失败: {e}")

    return results


# ──────────────────────────────────────────────────── API routes ──

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/status")
async def get_status():
    ctx = _load_context()
    phase_statuses = _phase_status(ctx)
    scores = ctx.get("review", {}).get("scores", {})
    total_score = 0
    if isinstance(scores, dict):
        for dim in scores.values():
            if isinstance(dim, dict) and "score" in dim:
                total_score += dim.get("score", 0)
    return {
        "current_phase": ctx.get("phase", "init"),
        "phases": [
            {
                "id": p,
                "name": PHASE_META[p]["name"],
                "icon": PHASE_META[p]["icon"],
                "agent": PHASE_META[p]["agent"],
                "status": phase_statuses[p],
            }
            for p in PHASE_ORDER
        ],
        "selected_problem": ctx.get("competition", {}).get("selected_problem", ""),
        "model_type": ctx.get("modeling", {}).get("model_type", ""),
        "model_name": ctx.get("modeling", {}).get("primary_model", {}).get("model_name", ""),
        "code_status": ctx.get("code_execution", {}).get("status", ""),
        "artifacts_count": len(ctx.get("code_execution", {}).get("artifacts", [])),
        "review_score": total_score,
        "review_tier": ctx.get("review", {}).get("scores", {}).get("tier", ""),
        "latex_check": ctx.get("latex_check", {}).get("status", ""),
        "data_cleaning": {
            "files": ctx.get("data_cleaning", {}).get("files_processed", 0),
            "figures": len(ctx.get("data_cleaning", {}).get("all_figures", [])),
        },
        "data_validation": ctx.get("data_validation", {}),
        "pipeline_running": _pipeline_proc is not None and _pipeline_proc.poll() is None,
    }


@app.get("/api/context")
async def get_context():
    return _load_context()


@app.get("/api/files")
async def list_files():
    files = []
    if DATA_DIR.exists():
        for f in sorted(DATA_DIR.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "path": str(f), "size": f.stat().st_size,
                               "category": "data", "is_cleaned": f.name.startswith("cleaned_")})
    if PAPER_DIR.exists():
        for f in sorted(PAPER_DIR.glob("*.*")):
            if f.is_file() and f.suffix in (".tex", ".bib", ".json"):
                files.append({"name": f.name, "path": str(f), "size": f.stat().st_size, "category": "paper"})
    if FIGURES_DIR.exists():
        for f in sorted(FIGURES_DIR.iterdir()):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".pdf", ".eps"):
                files.append({"name": f.name, "path": str(f), "size": f.stat().st_size, "category": "figure"})
    return {"files": files}


@app.get("/api/figures/{filename}")
async def get_figure(filename: str):
    for p in [FIGURES_DIR / filename, PAPER_DIR / "figures" / filename]:
        if p.exists() and p.is_file():
            return FileResponse(str(p))
    raise HTTPException(404, f"Figure not found: {filename}")


@app.get("/api/logs")
async def get_logs():
    log_path = LOGS_DIR / "run.log"
    if not log_path.exists():
        return {"log": "(暂无日志)", "size": 0}
    content = log_path.read_text(encoding="utf-8", errors="replace")
    return {"log": content[-10000:], "size": log_path.stat().st_size}


@app.get("/api/reports/{report_type}")
async def get_report(report_type: str):
    report_map = {
        "review": PAPER_DIR / "review_report.json",
        "latex": PAPER_DIR / "latex_check_report.json",
    }
    path = report_map.get(report_type)
    if not path or not path.exists():
        raise HTTPException(404, f"Report not found: {report_type}")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/knowledge")
async def get_knowledge():
    items = []
    if KB_DIR.exists():
        for f in sorted(KB_DIR.glob("*.json")):
            if f.name == "build_manifest.json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    count = len(data.get("entries", data.get("patterns", [])))
                else:
                    count = 0
            except Exception:
                count = 0
            items.append({"name": f.stem, "entries": count})
    return {"knowledge_base": items}


@app.post("/api/run/{phase}")
async def run_phase(phase: str):
    global _pipeline_proc
    if phase not in PHASE_ORDER:
        raise HTTPException(400, f"Invalid phase: {phase}")
    if _pipeline_proc and _pipeline_proc.poll() is None:
        raise HTTPException(409, "Pipeline is already running")
    _pipeline_proc = subprocess.Popen(
        [sys.executable, "main.py", "--start", phase],
        cwd=str(BASE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {"status": "started", "phase": phase, "pid": _pipeline_proc.pid}


@app.post("/api/stop")
async def stop_pipeline():
    global _pipeline_proc
    if _pipeline_proc and _pipeline_proc.poll() is None:
        _pipeline_proc.terminate()
        _pipeline_proc = None
        return {"status": "stopped"}
    return {"status": "not_running"}


@app.get("/api/pipeline-output")
async def pipeline_output():
    """SSE: stream pipeline stdout."""
    async def generate():
        if not _pipeline_proc or _pipeline_proc.poll() is not None:
            yield 'data: {"done": true}\n\n'
            return
        while True:
            line = _pipeline_proc.stdout.readline()
            if not line:
                if _pipeline_proc.poll() is not None:
                    yield 'data: {"done": true}\n\n'
                    break
                await asyncio.sleep(0.1)
                continue
            escaped = json.dumps(line.rstrip())
            yield f'data: {{"line": {escaped}}}\n\n'
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/sse")
async def sse_status():
    """SSE: emit phase change events every 2s."""
    async def generate():
        last_phase = None
        while True:
            ctx = _load_context()
            phase = ctx.get("phase", "init")
            if phase != last_phase:
                last_phase = phase
                data = json.dumps({
                    "phase": phase,
                    "running": _pipeline_proc is not None and _pipeline_proc.poll() is None,
                })
                yield f"data: {data}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(generate(), media_type="text/event-stream")


# ─────────────────────────────────────────── config endpoints ──

@app.get("/api/config")
async def get_config():
    return {
        "model_routes": _parse_toml_config(),
        "pipeline": _load_pipeline_cfg(),
    }


@app.post("/api/config")
async def save_config(request: Request):
    body = await request.json()
    if "model_routes" in body:
        _write_toml_config(body["model_routes"])
    if "pipeline" in body:
        _save_pipeline_cfg(body["pipeline"])
    return {"status": "saved"}


# ─────────────────────────────────────────────── chat endpoint ──

@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """
    AI assistant chat.
    Request: {"message": str, "history": [{"role":"user"|"assistant","content":str}]}
    Response: {"reply": str, "actions": [...], "action_results": [...]}
    """
    body = await request.json()
    message: str = body.get("message", "").strip()
    history: list[dict] = body.get("history", [])

    if not message:
        raise HTTPException(400, "message is required")

    # Build context snapshot
    ctx = _load_context()
    status_data = {
        "current_phase": ctx.get("phase", "init"),
        "pipeline_running": _pipeline_proc is not None and _pipeline_proc.poll() is None,
        "selected_problem": ctx.get("competition", {}).get("selected_problem", ""),
        "model_type": ctx.get("modeling", {}).get("model_type", ""),
    }
    model_cfg = _parse_toml_config()
    pipeline_cfg = _load_pipeline_cfg()

    # Build messages for LLM
    system_content = _CHAT_SYSTEM + f"""

当前状态：
{json.dumps(status_data, ensure_ascii=False, indent=2)}

当前模型配置（各任务首选模型）：
{json.dumps({k: v.get("models", [])[:1] for k, v in model_cfg.items()}, ensure_ascii=False)}

当前流水线配置：
{json.dumps(pipeline_cfg, ensure_ascii=False)}
"""

    messages = [{"role": "system", "content": system_content}]
    for h in history[-10:]:  # limit history window
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    # Call LLM (run in thread to avoid blocking event loop)
    import asyncio
    loop = asyncio.get_event_loop()

    def _call_llm() -> str:
        try:
            # Import here to avoid circular import at module load
            sys.path.insert(0, str(BASE))
            from agents.orchestrator import call_model
            return call_model(system_content, message, task="default")
        except Exception as e:
            return f"[LLM 调用失败: {e}]"

    reply_text: str = await loop.run_in_executor(None, _call_llm)

    # Parse <action>...</action> blocks
    action_pattern = re.compile(r"<action>(.*?)</action>", re.DOTALL)
    raw_actions = action_pattern.findall(reply_text)
    parsed_actions: list[dict] = []
    for raw in raw_actions:
        try:
            parsed_actions.append(json.loads(raw.strip()))
        except Exception:
            pass

    # Execute actions
    action_results = _execute_chat_actions(parsed_actions)

    # Strip action tags from reply shown to user
    clean_reply = action_pattern.sub("", reply_text).strip()

    return {
        "reply": clean_reply,
        "actions": parsed_actions,
        "action_results": action_results,
    }


# ─────────────────────────────────────────────────────── clear ──

@app.post("/api/clear")
async def clear_workspace(request: Request):
    """
    Clear all generated artifacts so a new problem can start clean.

    Scope controlled by request body:
      keep_translations: bool  (default False) — keep translation/*.md
      keep_originals:    bool  (always True)   — never touch vol/data/*.xlsx/csv originals

    Always cleared:
      context_store/context.json → reset to {"phase":"init"}
      vol/data/cleaned_*.csv
      vol/data/cleaning_report_*.json
      vol/outputs/**
      vol/scripts/*.py  (generated; keeps README.md)
      vol/logs/run.log
      paper/*.tex  paper/*.bib  paper/*.json  paper/figures/

    Optionally cleared:
      translation/*.md
    """
    global _pipeline_proc

    # Stop any running pipeline first
    if _pipeline_proc and _pipeline_proc.poll() is None:
        _pipeline_proc.terminate()
        _pipeline_proc = None

    body = await request.json() if request.headers.get("content-type","").startswith("application/json") else {}
    keep_translations: bool = body.get("keep_translations", False)

    removed: list[str] = []
    errors:  list[str] = []

    def _rm(path: Path) -> None:
        try:
            if path.is_file():
                path.unlink()
                removed.append(path.name)
            elif path.is_dir():
                import shutil
                shutil.rmtree(path)
                removed.append(str(path.relative_to(BASE)))
        except Exception as e:
            errors.append(f"{path.name}: {e}")

    # 1. Reset context.json
    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_PATH.write_text(json.dumps({"phase": "init"}, ensure_ascii=False, indent=2), encoding="utf-8")
    removed.append("context_store/context.json (reset)")

    # 2. Cleaned data + reports (keep originals)
    if DATA_DIR.exists():
        for f in DATA_DIR.iterdir():
            if f.is_file() and (
                f.name.startswith("cleaned_") or f.name.startswith("cleaning_report_")
            ):
                _rm(f)

    # 3. All outputs (figures, CSVs, etc.)
    if (VOL_DIR / "outputs").exists():
        for item in (VOL_DIR / "outputs").iterdir():
            _rm(item)

    # 4. Generated scripts (keep README.md and PDF helpers)
    scripts_dir = VOL_DIR / "scripts"
    if scripts_dir.exists():
        for f in scripts_dir.iterdir():
            if f.is_file() and f.suffix == ".py" and not f.name.startswith("_pdf"):
                _rm(f)

    # 5. Run log
    log_file = LOGS_DIR / "run.log"
    _rm(log_file)

    # 6. Paper directory — generated files only
    if PAPER_DIR.exists():
        keep_paper = {"references_draft.bib"}   # might want to keep this
        paper_exts = {".tex", ".bib", ".json", ".bbl", ".aux", ".log", ".out"}
        for f in PAPER_DIR.iterdir():
            if f.is_file() and f.suffix in paper_exts and f.name not in keep_paper:
                _rm(f)
        # paper/figures/
        paper_figs = PAPER_DIR / "figures"
        if paper_figs.exists():
            _rm(paper_figs)

    # 7. Translations (optional)
    translation_dir = BASE / "translation"
    if not keep_translations and translation_dir.exists():
        for f in translation_dir.iterdir():
            if f.is_file() and f.suffix in (".md", ".txt"):
                _rm(f)

    return {
        "status": "cleared",
        "removed_count": len(removed),
        "removed": removed,
        "errors": errors,
    }


# ─────────────────────────────────────────────────────── experience ──

EXPERIENCE_LOG = KB_DIR / "experience_log.json"

@app.get("/api/experience")
async def get_experience(phase: str = "", limit: int = 20):
    """Return saved experience entries, optionally filtered by phase."""
    if not EXPERIENCE_LOG.exists():
        return {"entries": [], "total": 0}
    try:
        data = json.loads(EXPERIENCE_LOG.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": [], "total": 0}
    entries: list[dict] = data.get("entries", [])
    if phase:
        entries = [e for e in entries if e.get("phase") == phase]
    entries = list(reversed(entries))  # newest first
    return {
        "entries": entries[:limit],
        "total": len(entries),
        "phases": sorted({e.get("phase", "") for e in data.get("entries", [])}),
    }


# ─────────────────────────────────────────────────────── entry ──

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"\n  MCM Pipeline Dashboard  →  http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
