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
  GET  /api/pipeline-events       → SSE: structured phase events (JSONL bus)
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
    "P2.5": {"name": "数学可视化", "agent": "matlab_viz.py",         "icon": "model"},
    "P3":   {"name": "代码求解",   "agent": "code_agent.py",         "icon": "code"},
    "P3.5": {"name": "数据验证",   "agent": "data_validator.py",     "icon": "check"},
    "P4":   {"name": "论文撰写",   "agent": "writing_agent.py",      "icon": "write"},
    "P4.5": {"name": "LaTeX 检查", "agent": "latex_check_agent.py",  "icon": "latex"},
    "P5":   {"name": "审校评分",   "agent": "review_agent.py",       "icon": "review"},
    "P5.5": {"name": "数据审计",   "agent": "data_validator.py",     "icon": "audit"},
}

PHASE_ORDER = ["P0b", "P1", "P1.5", "P2", "P2.5", "P3", "P3.5", "P4", "P4.5", "P5", "P5.5"]

PHASE_COMPLETE_MAP = {
    "init": set(),
    "P0b_complete": {"P0b"},
    "P1_extraction_complete": {"P0b", "P1"},
    "P1.5_complete": {"P0b", "P1", "P1.5"},
    "P1.5_skipped": {"P0b", "P1", "P1.5"},
    "P2_complete": {"P0b", "P1", "P1.5", "P2"},
    "P2.5_complete": {"P0b", "P1", "P1.5", "P2", "P2.5"},
    "P3_complete": {"P0b", "P1", "P1.5", "P2", "P2.5", "P3"},
    "P3_logic_err": {"P0b", "P1", "P1.5", "P2", "P2.5"},
    "P3.5_complete": {"P0b", "P1", "P1.5", "P2", "P2.5", "P3", "P3.5"},
    "P4_complete": {"P0b", "P1", "P1.5", "P2", "P2.5", "P3", "P3.5", "P4"},
    "P4.5_complete": {"P0b", "P1", "P1.5", "P2", "P2.5", "P3", "P3.5", "P4", "P4.5"},
    "P5_complete": {"P0b", "P1", "P1.5", "P2", "P2.5", "P3", "P3.5", "P4", "P4.5", "P5"},
    "P5.5_complete": set(PHASE_ORDER),
}

app = FastAPI(title="MCM Pipeline Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _load_extensions_on_startup() -> None:
    """Bootstrap plugins / MCP / skills once the FastAPI worker is up."""
    try:
        from agents.extensions import load_all as _load
        _load()
    except Exception as exc:  # noqa: BLE001
        print(f"  [ext] 启动加载失败: {exc}")


@app.on_event("shutdown")
async def _shutdown_extensions() -> None:
    try:
        from agents.extensions.registry import shutdown as _shutdown
        _shutdown()
    except Exception:
        pass

_pipeline_proc: subprocess.Popen | None = None
_sandbox_proc: subprocess.Popen | None = None
_sandbox_output_buf: list[str] = []   # rolling buffer of last 200 lines

# SSE heartbeat interval — prevents idle connections from being closed by
# intermediate proxies/browsers. Format `: comment\n\n` is a no-op for the
# EventSource parser but still a real TCP frame.
SSE_HEARTBEAT_INTERVAL = 15.0
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


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
1. 了解当前流水线和沙箱状态
2. 暂停 / 重启流水线
3. 修改模型配置（切换LLM、调整超时）
4. 修改流水线配置（循环次数、Token限制）
5. 实时控制代码沙箱：运行脚本、执行任意代码、终止进程

当你需要执行操作时，在回复中嵌入 JSON 行动块（每个单独一行）：

【流水线控制】
<action>{"type":"pause"}</action>
<action>{"type":"restart","phase":"P3"}</action>
<action>{"type":"set_model","task":"modeling","model":"ds:deepseek-chat"}</action>
<action>{"type":"set_timeout","task":"modeling","value":180}</action>
<action>{"type":"set_budget","task":"modeling","value":360}</action>
<action>{"type":"set_pipeline","key":"max_rollbacks","value":3}</action>
<action>{"type":"set_tokens","task":"codegen","value":8192}</action>

【沙箱控制】
<action>{"type":"sandbox_run","script":"solver.py"}</action>
<action>{"type":"sandbox_exec","code":"import pandas as pd\nprint(pd.__version__)"}</action>
<action>{"type":"sandbox_kill"}</action>
<action>{"type":"sandbox_status"}</action>

沙箱说明：
- sandbox_run: 在 Docker 沙箱中运行 vol/scripts/ 目录下指定脚本，输出实时流式推送到 /api/sandbox-output
- sandbox_exec: 在沙箱中执行任意 Python 代码（写入 _assistant_exec.py 后运行）
- sandbox_kill: 终止当前沙箱进程
- sandbox_status: 查询沙箱当前状态和最近输出（已嵌入上下文，可直接询问）

规则：
- 默认用中文回复
- 每次操作前先简短说明要做什么，再给出行动块
- 行动块执行完毕后系统会反馈执行结果
- 如果用户没有明确说暂停，在修改配置后问是否需要重启
- 沙箱执行是异步的，用户可以通过 /api/sandbox-output SSE 或直接询问你来查看最新输出
"""


def _start_sandbox_proc(script_host_path: str) -> None:
    """docker cp + Popen docker exec; updates _sandbox_proc and _sandbox_output_buf."""
    global _sandbox_proc, _sandbox_output_buf
    from agents.utils import container_name, docker_cp

    script_name = Path(script_host_path).name
    container_path = f"/tmp/{script_name}"
    docker_cp(script_host_path, container_name(), container_path)

    _sandbox_output_buf = []
    _sandbox_proc = subprocess.Popen(
        ["docker", "exec", container_name(), "python3", container_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# ─────────────────────────────────────────── tool dispatcher ──

def _dispatch_tool(name: str, args: dict) -> str:
    """Dispatch a single tool call (OpenAI function-calling) to its handler.

    Reuses _execute_chat_actions by converting to the legacy action-dict shape
    for the overlapping cases, and implements the new tools inline.
    """
    # New tools that don't map to legacy actions
    if name == "list_scripts":
        scripts_dir = VOL_DIR / "scripts"
        if not scripts_dir.exists():
            return "vol/scripts/ 不存在"
        files = sorted(f.name for f in scripts_dir.iterdir()
                       if f.is_file() and f.suffix == ".py")
        if not files:
            return "vol/scripts/ 为空"
        return "可用脚本:\n" + "\n".join(f"  • {f}" for f in files)

    if name == "read_context":
        key = (args.get("key") or "").strip()
        if not key:
            return "✗ read_context 需要 key 参数"
        ctx = _load_context()
        if key not in ctx:
            top = sorted(ctx.keys())
            return f"✗ context 中无 '{key}'。可用 key: {top}"
        val = ctx[key]
        try:
            rendered = json.dumps(val, ensure_ascii=False, indent=2)
        except Exception:
            rendered = str(val)
        if len(rendered) > 4000:
            rendered = rendered[:4000] + "\n… [truncated]"
        return f"context.{key}:\n{rendered}"

    # Map tool names → legacy action-dict types
    legacy_map = {
        "pipeline_pause": ("pause", {}),
        "pipeline_restart": ("restart", {"phase": args.get("phase")}),
        "set_model": ("set_model", {"task": args.get("task"), "model": args.get("model")}),
        "set_timeout": ("set_timeout", {"task": args.get("task"), "value": args.get("value")}),
        "set_budget": ("set_budget", {"task": args.get("task"), "value": args.get("value")}),
        "set_tokens": ("set_tokens", {"task": args.get("task"), "value": args.get("value")}),
        "set_pipeline_config": ("set_pipeline", {"key": args.get("key"), "value": args.get("value")}),
        "sandbox_run": ("sandbox_run", {"script": args.get("script")}),
        "sandbox_exec": ("sandbox_exec", {"code": args.get("code")}),
        "sandbox_kill": ("sandbox_kill", {}),
        "sandbox_status": ("sandbox_status", {}),
    }
    if name not in legacy_map:
        # Try extension registry (plugin tools + MCP tools)
        try:
            from agents.tool_registry import extension_handler
            handler = extension_handler(name)
        except Exception:
            handler = None
        if handler is not None:
            try:
                result = handler(**(args or {}))
            except Exception as exc:  # noqa: BLE001
                return f"✗ 工具 {name} 执行异常: {type(exc).__name__}: {exc}"
            if isinstance(result, (dict, list)):
                try:
                    return json.dumps(result, ensure_ascii=False)[:4000]
                except Exception:
                    return str(result)[:4000]
            return str(result)[:4000]
        return f"✗ 未知工具: {name}"
    action_type, extra = legacy_map[name]
    action: dict = {"type": action_type, **extra}
    results = _execute_chat_actions([action])
    return results[0] if results else ""


def _execute_chat_actions(actions: list[dict]) -> list[str]:
    """Execute parsed actions and return human-readable results."""
    global _pipeline_proc, _sandbox_proc, _sandbox_output_buf
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

            # ── 沙箱控制 ──────────────────────────────────────────────
            elif t == "sandbox_run":
                script = act.get("script", "").strip()
                if not script:
                    results.append("✗ sandbox_run 需要 script 参数")
                    continue
                script_path = VOL_DIR / "scripts" / script
                if not script_path.exists():
                    results.append(f"✗ 脚本不存在: vol/scripts/{script}")
                    continue
                if _sandbox_proc and _sandbox_proc.poll() is None:
                    _sandbox_proc.terminate()
                _start_sandbox_proc(str(script_path))
                results.append(f"✓ 沙箱已启动: {script} (pid={_sandbox_proc.pid}), 输出见 /api/sandbox-output")

            elif t == "sandbox_exec":
                code = act.get("code", "").strip()
                if not code:
                    results.append("✗ sandbox_exec 需要 code 参数")
                    continue
                exec_path = VOL_DIR / "scripts" / "_assistant_exec.py"
                exec_path.parent.mkdir(parents=True, exist_ok=True)
                exec_path.write_text(code, encoding="utf-8")
                if _sandbox_proc and _sandbox_proc.poll() is None:
                    _sandbox_proc.terminate()
                _start_sandbox_proc(str(exec_path))
                results.append(f"✓ 沙箱已执行代码片段 (pid={_sandbox_proc.pid}), 输出见 /api/sandbox-output")

            elif t == "sandbox_kill":
                if _sandbox_proc and _sandbox_proc.poll() is None:
                    _sandbox_proc.terminate()
                    _sandbox_proc = None
                    results.append("✓ 沙箱进程已终止")
                else:
                    results.append("ℹ 沙箱当前没有运行中的进程")

            elif t == "sandbox_status":
                if _sandbox_proc and _sandbox_proc.poll() is None:
                    recent = _sandbox_output_buf[-20:] if _sandbox_output_buf else []
                    results.append(
                        f"ℹ 沙箱运行中 (pid={_sandbox_proc.pid}), "
                        f"最近输出 ({len(recent)} 行):\n" + "\n".join(recent)
                    )
                else:
                    code = _sandbox_proc.returncode if _sandbox_proc else None
                    recent = _sandbox_output_buf[-20:] if _sandbox_output_buf else []
                    results.append(
                        f"ℹ 沙箱空闲 (上次退出码={code}), "
                        f"最近输出 ({len(recent)} 行):\n" + "\n".join(recent)
                    )

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
    """SSE: stream pipeline stdout with periodic heartbeats."""
    async def generate():
        if not _pipeline_proc or _pipeline_proc.poll() is not None:
            yield 'data: {"done": true}\n\n'
            return
        last_beat = time.time()
        while True:
            line = _pipeline_proc.stdout.readline()
            if not line:
                if _pipeline_proc.poll() is not None:
                    yield 'data: {"done": true}\n\n'
                    break
                # heartbeat while waiting for more output
                if time.time() - last_beat > SSE_HEARTBEAT_INTERVAL:
                    yield ": keepalive\n\n"
                    last_beat = time.time()
                await asyncio.sleep(0.1)
                continue
            escaped = json.dumps(line.rstrip())
            yield f'data: {{"line": {escaped}}}\n\n'
            last_beat = time.time()
    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/sandbox-output")
async def sandbox_output():
    """SSE: stream sandbox (docker exec) stdout with heartbeats."""
    async def generate():
        global _sandbox_output_buf
        if not _sandbox_proc or _sandbox_proc.poll() is not None:
            yield 'data: {"done": true}\n\n'
            return
        last_beat = time.time()
        while True:
            line = _sandbox_proc.stdout.readline()
            if not line:
                if _sandbox_proc.poll() is not None:
                    rc = _sandbox_proc.returncode
                    yield f'data: {{"done": true, "exit_code": {rc}}}\n\n'
                    break
                if time.time() - last_beat > SSE_HEARTBEAT_INTERVAL:
                    yield ": keepalive\n\n"
                    last_beat = time.time()
                await asyncio.sleep(0.05)
                continue
            stripped = line.rstrip()
            _sandbox_output_buf.append(stripped)
            if len(_sandbox_output_buf) > 200:
                _sandbox_output_buf = _sandbox_output_buf[-200:]
            escaped = json.dumps(stripped)
            yield f'data: {{"line": {escaped}}}\n\n'
            last_beat = time.time()
    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/sandbox-status")
async def sandbox_status():
    """Return sandbox running state and recent output buffer."""
    running = _sandbox_proc is not None and _sandbox_proc.poll() is None
    return {
        "running": running,
        "pid": _sandbox_proc.pid if _sandbox_proc else None,
        "exit_code": _sandbox_proc.returncode if (_sandbox_proc and not running) else None,
        "recent_lines": _sandbox_output_buf[-50:],
    }


@app.get("/api/sse")
async def sse_status():
    """SSE: emit phase/running changes, plus a heartbeat comment every 15s."""
    async def generate():
        last_phase = None
        last_running = None
        last_beat = time.time()
        # prime with current state so clients render immediately
        while True:
            ctx = _load_context()
            phase = ctx.get("phase", "init")
            running = _pipeline_proc is not None and _pipeline_proc.poll() is None
            changed = phase != last_phase or running != last_running
            if changed:
                last_phase = phase
                last_running = running
                yield f"data: {json.dumps({'phase': phase, 'running': running})}\n\n"
                last_beat = time.time()
            elif time.time() - last_beat > SSE_HEARTBEAT_INTERVAL:
                yield ": keepalive\n\n"
                last_beat = time.time()
            await asyncio.sleep(2)
    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


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

_MAX_TOOL_ROUNDS = 5


def _build_chat_system(persona_prompt: str) -> str:
    """Combine persona prompt with a live status snapshot."""
    ctx = _load_context()
    sandbox_running = _sandbox_proc is not None and _sandbox_proc.poll() is None
    status_data = {
        "current_phase": ctx.get("phase", "init"),
        "pipeline_running": _pipeline_proc is not None and _pipeline_proc.poll() is None,
        "selected_problem": ctx.get("competition", {}).get("selected_problem", ""),
        "model_type": ctx.get("modeling", {}).get("model_type", ""),
        "sandbox_running": sandbox_running,
        "sandbox_pid": _sandbox_proc.pid if sandbox_running else None,
        "sandbox_recent_output": _sandbox_output_buf[-10:] if _sandbox_output_buf else [],
    }
    model_cfg = _parse_toml_config()
    pipeline_cfg = _load_pipeline_cfg()
    return persona_prompt + f"""

当前状态：
{json.dumps(status_data, ensure_ascii=False, indent=2)}

当前模型配置（各任务首选模型）：
{json.dumps({k: v.get("models", [])[:1] for k, v in model_cfg.items()}, ensure_ascii=False)}

当前流水线配置：
{json.dumps(pipeline_cfg, ensure_ascii=False)}
"""


@app.get("/api/personas")
async def get_personas():
    from agents.persona_mgr import list_personas, default_persona_id
    return {"personas": list_personas(), "default": default_persona_id()}


@app.get("/api/sessions")
async def list_chat_sessions():
    from agents.conversation_mgr import list_sessions
    return {"sessions": list_sessions()}


@app.post("/api/sessions")
async def create_chat_session(request: Request):
    from agents.conversation_mgr import create_session
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    persona = (body.get("persona") or "").strip() or None
    title = (body.get("title") or "新对话").strip()
    s = create_session(persona=persona, title=title)
    return {"id": s["id"], "persona": s["persona"], "title": s["title"]}


@app.get("/api/sessions/{session_id}")
async def get_chat_session(session_id: str):
    from agents.conversation_mgr import get_session, sanitize_for_display
    s = get_session(session_id)
    if not s:
        raise HTTPException(404, f"session not found: {session_id}")
    return {
        "id": s["id"],
        "title": s.get("title", "新对话"),
        "persona": s.get("persona", "controller"),
        "messages": sanitize_for_display(s.get("messages", [])),
        "created_at": s.get("created_at", 0),
        "updated_at": s.get("updated_at", 0),
    }


@app.delete("/api/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    from agents.conversation_mgr import delete_session
    return {"deleted": delete_session(session_id)}


@app.patch("/api/sessions/{session_id}")
async def patch_chat_session(session_id: str, request: Request):
    from agents.conversation_mgr import rename_session, set_persona, get_session
    body = await request.json()
    updated = False
    if "title" in body:
        updated = rename_session(session_id, body["title"]) or updated
    if "persona" in body:
        updated = set_persona(session_id, body["persona"]) or updated
    if not updated:
        raise HTTPException(400, "no valid fields to update")
    return {"ok": True, "session": get_session(session_id)}


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """
    Tool-calling chat. Request: {session_id, message, persona?}
    Returns {reply, tool_calls[], display_messages[]}.

    If session_id is missing or unknown, a new session is created with the
    requested persona (or the default).
    """
    from agents.conversation_mgr import (
        append_messages, create_session, get_session, sanitize_for_display,
    )
    from agents.persona_mgr import get_persona
    from agents.tool_registry import tools_for

    body = await request.json()
    message: str = (body.get("message") or "").strip()
    session_id: str = (body.get("session_id") or "").strip()
    persona_override: str | None = (body.get("persona") or "").strip() or None

    if not message:
        raise HTTPException(400, "message is required")

    # Resolve / create session
    session = get_session(session_id) if session_id else None
    if not session:
        session = create_session(persona=persona_override)
        session_id = session["id"]
    persona = get_persona(persona_override or session.get("persona", "controller"))

    system_content = _build_chat_system(persona.system_prompt)
    # Inject any skills whose triggers match the user message
    try:
        from agents.extensions import get_registry
        from agents.extensions.skills import render_skills_block
        matched = get_registry().match_skills(message, limit=3)
        skills_block = render_skills_block(matched)
        if skills_block:
            system_content = system_content + skills_block
    except Exception:
        pass
    tools = tools_for(list(persona.allowed_tools))

    # Build messages: system + prior history + new user message
    messages: list[dict] = [{"role": "system", "content": system_content}]
    messages.extend(session.get("messages", []))
    user_msg = {"role": "user", "content": message}
    messages.append(user_msg)

    new_messages_to_persist: list[dict] = [user_msg]
    tool_call_log: list[dict] = []

    loop = asyncio.get_event_loop()

    def _call(msgs: list[dict]) -> dict:
        sys.path.insert(0, str(BASE))
        from agents.orchestrator import call_with_tools
        return call_with_tools(msgs, tools=tools, task="default")

    final_reply: str = ""
    try:
        for _ in range(_MAX_TOOL_ROUNDS):
            assistant_msg = await loop.run_in_executor(None, _call, messages)
            messages.append(assistant_msg)
            new_messages_to_persist.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                final_reply = assistant_msg.get("content", "") or ""
                break

            # Execute each tool, append tool-result messages, loop
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    parsed_args = {}
                result = _dispatch_tool(name, parsed_args)
                tool_call_log.append({"name": name, "arguments": parsed_args, "result": result})
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id") or f"call_{name}",
                    "name": name,
                    "content": result,
                }
                messages.append(tool_msg)
                new_messages_to_persist.append(tool_msg)
        else:
            final_reply = (
                "（达到最大工具调用轮数限制，仍未收敛，请检查工具或重新表述问题）"
            )
    except Exception as e:
        final_reply = f"[LLM 调用失败: {e}]"
        new_messages_to_persist.append({"role": "assistant", "content": final_reply})

    # Persist all new messages to the session
    append_messages(session_id, new_messages_to_persist)

    # Refresh session to get sanitized view
    refreshed = get_session(session_id) or {}
    return {
        "session_id": session_id,
        "persona": persona.id,
        "reply": final_reply,
        "tool_calls": tool_call_log,
        "display_messages": sanitize_for_display(refreshed.get("messages", [])),
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


# ─────────────────────────────────────────────── latex compile / cleanup ──

_compile_lock = asyncio.Lock()   # 防止多次同时触发编译


@app.get("/api/compile-stream")
async def compile_stream(max_rounds: int = 5):
    """SSE: 流式输出 compile_fix_loop 的实时日志。

    事件格式：
      {"type": "log"|"phase"|"success"|"error", "line": str}
      {"type": "done", "success": bool, "rounds": int, "pdf_path": str|None}
    """
    import threading

    if _compile_lock.locked():
        async def _busy():
            yield 'data: {"type":"error","line":"已有编译任务在运行，请稍候"}\n\n'
            yield 'data: {"type":"done","success":false,"rounds":0,"pdf_path":null}\n\n'
        return StreamingResponse(_busy(), media_type="text/event-stream")

    queue: asyncio.Queue = asyncio.Queue()
    ev_loop = asyncio.get_event_loop()

    def log_cb(msg: dict):
        ev_loop.call_soon_threadsafe(queue.put_nowait, msg)

    def worker():
        try:
            sys.path.insert(0, str(BASE))
            from agents.latex_compiler import compile_fix_loop
            compile_fix_loop(max_rounds=max_rounds, log_cb=log_cb)
        except Exception as exc:
            log_cb({"type": "error", "line": f"[FATAL] {exc}"})
            log_cb({"type": "done", "success": False, "rounds": 0, "pdf_path": None})

    async def generate():
        async with _compile_lock:
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            while True:
                msg = await queue.get()
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("type") == "done":
                    break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cleanup-aux")
async def cleanup_aux_endpoint():
    """删除 paper/ 下所有 LaTeX 辅助文件（.aux .log .bbl 等），保留 .tex .bib .pdf。"""
    from agents.latex_compiler import cleanup_aux_files
    return cleanup_aux_files()


# ────────────────────────────────────── pipeline events (SSE) ──

@app.get("/api/pipeline-events")
async def pipeline_events(last_seq: int = 0):
    """SSE: tail structured pipeline events (phase_start/end, rollback, ...).

    Clients pass `?last_seq=N` to resume after a reconnect without re-reading
    events they already saw. Each SSE `data:` frame is a full event JSON with
    a monotonic `seq`.
    """
    events_path = LOGS_DIR / "pipeline_events.jsonl"

    def _read_from(seq: int) -> tuple[list[dict], int]:
        if not events_path.exists():
            return [], seq
        out: list[dict] = []
        max_seq = seq
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_seq = int(ev.get("seq", 0))
                if ev_seq > seq:
                    out.append(ev)
                    if ev_seq > max_seq:
                        max_seq = ev_seq
        return out, max_seq

    async def generate():
        seen = last_seq
        last_beat = time.time()
        # Prime with historical events past `last_seq`
        historical, seen = _read_from(seen)
        for ev in historical:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        if historical:
            last_beat = time.time()

        while True:
            await asyncio.sleep(0.5)
            new_events, seen = _read_from(seen)
            if new_events:
                for ev in new_events:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                last_beat = time.time()
            elif time.time() - last_beat > SSE_HEARTBEAT_INTERVAL:
                yield ": keepalive\n\n"
                last_beat = time.time()

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


# ─────────────────────────────────────────── logs stream (SSE) ──

@app.get("/api/logs/stream")
async def logs_stream():
    """SSE: tail run.log in real time, first emitting historical lines."""
    log_path = LOGS_DIR / "run.log"

    async def generate():
        # 1. Emit last 300 historical lines
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines()[-300:]:
                escaped = json.dumps(line)
                yield f'data: {{"line": {escaped}, "historical": true}}\n\n'

        # 2. Tail for new content
        last_size = log_path.stat().st_size if log_path.exists() else 0
        last_beat = time.time()
        buf = ""
        while True:
            await asyncio.sleep(0.5)
            if not log_path.exists():
                if time.time() - last_beat > SSE_HEARTBEAT_INTERVAL:
                    yield ": keepalive\n\n"
                    last_beat = time.time()
                continue
            size = log_path.stat().st_size
            if size <= last_size:
                if time.time() - last_beat > SSE_HEARTBEAT_INTERVAL:
                    yield ": keepalive\n\n"
                    last_beat = time.time()
                continue
            with log_path.open(encoding="utf-8", errors="replace") as f:
                f.seek(last_size)
                new_data = f.read(size - last_size)
            last_size = size
            buf += new_data
            lines = buf.split("\n")
            buf = lines[-1]          # keep incomplete line in buffer
            for line in lines[:-1]:
                if line:
                    escaped = json.dumps(line)
                    yield f'data: {{"line": {escaped}}}\n\n'
            last_beat = time.time()

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


# ─────────────────────────────────────── pip install in sandbox ──

@app.post("/api/pip-install")
async def pip_install_endpoint(request: Request):
    """Install a Python package inside the sandbox container (or locally)."""
    body = await request.json()
    package: str = body.get("package", "").strip()
    if not package or any(c in package for c in [";", "&", "|", "`", "$"]):
        raise HTTPException(400, "Invalid package name")

    loop = asyncio.get_event_loop()

    def _run_pip():
        try:
            from sandbox.runner import docker_exec, container_name
            exit_code, stdout, stderr = docker_exec(
                container_name(),
                f"pip install {package} --quiet",
                timeout=120,
            )
            return {"success": exit_code == 0, "output": (stdout + stderr).strip()}
        except Exception:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package, "--quiet"],
                capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            out = (result.stdout + result.stderr).strip()
            return {"success": result.returncode == 0, "output": out}

    result = await loop.run_in_executor(None, _run_pip)
    return result


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
