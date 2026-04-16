from __future__ import annotations

from pathlib import Path

from agents.utils import container_name, docker_cp, docker_exec, vol_host


def run_script(script_host_path: str) -> tuple[int, str, str]:
    """Sync script into container and execute it."""
    script_name = Path(script_host_path).name
    container_path = f"/tmp/{script_name}"

    docker_cp(script_host_path, container_name(), container_path)
    exit_code, stdout, stderr = docker_exec(container_name(), f"python3 {container_path}")

    log = vol_host() / "logs" / "run.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"[EXIT_CODE] {exit_code}\n[STDOUT]\n{stdout}\n[STDERR]\n{stderr}",
        encoding="utf-8",
    )
    return exit_code, stdout, stderr
