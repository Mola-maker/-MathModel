from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.utils import container_name, docker_cp, docker_exec, vol_host
from sandbox.archiver import archive_artifacts
from sandbox.healer import heal

CONTEXT_PATH = Path(os.getenv("CONTEXT_STORE", "context_store/context.json"))


def _run_script(script_host_path: str) -> tuple[int, str, str]:
    """Sync script to container and execute it, writing run log."""
    script_name = Path(script_host_path).name
    container_script = f"/tmp/{script_name}"

    docker_cp(script_host_path, container_name(), container_script)
    exit_code, stdout, stderr = docker_exec(container_name(), f"python3 {container_script}")

    log = vol_host() / "logs" / "run.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"[EXIT_CODE] {exit_code}\n[STDOUT]\n{stdout}\n[STDERR]\n{stderr}",
        encoding="utf-8",
    )
    return exit_code, stdout, stderr


def _update_ctx(updates: dict) -> None:
    ctx = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    ctx.setdefault("code_execution", {}).update(updates)
    CONTEXT_PATH.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")


def execute_with_healing(script_name: str) -> dict:
    """Run script with auto-healing loop."""
    script_host_path = str(vol_host() / "scripts" / script_name)
    max_iter = int(os.getenv("MAX_HEAL_ITERATIONS", "5"))

    _update_ctx({"status": "running", "current_script": script_host_path, "iterations": 0})

    for iteration in range(max_iter):
        exit_code, _, stderr = _run_script(script_host_path)

        if exit_code == 0:
            artifacts = archive_artifacts()
            _update_ctx({"status": "success", "iterations": iteration, "last_error": None})
            return {"status": "success", "artifacts": artifacts, "iterations": iteration}

        fixed_code, is_logic = heal(script_host_path, stderr, iteration)

        if is_logic:
            _update_ctx(
                {
                    "status": "logic_err",
                    "iterations": iteration + 1,
                    "last_error": stderr[-1000:],
                }
            )
            return {"status": "logic_err", "error": stderr[-500:], "iterations": iteration + 1}

        Path(script_host_path).write_text(fixed_code, encoding="utf-8")
        _update_ctx({"iterations": iteration + 1, "last_error": stderr[-500:]})

    _update_ctx({"status": "syntax_err", "iterations": max_iter})
    return {"status": "syntax_err", "iterations": max_iter}
