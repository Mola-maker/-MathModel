from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


def parse_json(raw: str) -> dict:
    """Parse JSON content from raw LLM output."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {"error": "JSON parse failed", "raw": raw[:500]}


def docker_cp(host_path: str, container: str, container_path: str) -> None:
    """Copy host file to a target container path."""
    result = subprocess.run(
        ["docker", "cp", host_path, f"{container}:{container_path}"],
        capture_output=True,
    )
    if result.returncode != 0:
        stderr_bytes = result.stderr or b""
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"docker cp failed: {host_path} -> {container}:{container_path}. {stderr}"
        )


def docker_exec(container: str, cmd: str, timeout: int = 300) -> tuple[int, str, str]:
    """Execute shell command in container and return (code, stdout, stderr)."""
    result = subprocess.run(
        ["docker", "exec", container, "sh", "-c", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def vol_host() -> Path:
    _base = Path(__file__).resolve().parent.parent
    return Path(os.getenv("VOL_HOST", str(_base / "vol")))


def _running_container_names() -> list[str]:
    """Get running docker container names; return empty on failure."""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def container_name() -> str:
    """Return a valid sandbox container name with fallback discovery."""
    configured = os.getenv("SANDBOX_CONTAINER", "").strip()
    names = _running_container_names()

    if names:
        if configured and configured in names:
            return configured

        session_names = [n for n in names if n.startswith("bay-session-sess-")]
        if session_names:
            return session_names[0]

        if "bay" in names:
            return "bay"

    if configured:
        return configured

    return "bay-session-sess-a8eeaaadc79b"


def host_to_container_path(host_path: str) -> str:
    """Map host vol path to container path."""
    vol_container = os.getenv("VOL_CONTAINER", "/workspace/vol")
    path_obj = Path(host_path)
    base = vol_host()

    try:
        rel = path_obj.relative_to(base)
        return f"{vol_container}/{rel.as_posix()}"
    except ValueError:
        return f"{vol_container}/scripts/{path_obj.name}"
