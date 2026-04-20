"""Run MATLAB-compatible .m scripts in the sandbox via GNU Octave.

Lets the writing/modeling pipeline emit real .m code alongside Python plots,
so the final paper can include authentic MATLAB figures without a MATLAB
license. Uses `octave --no-gui` inside the same container as Python solver.
"""

from __future__ import annotations

import os
from pathlib import Path

from agents.utils import container_name, docker_exec, host_to_container_path

BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", BASE_DIR / "vol"))
SCRIPTS_DIR = VOL_HOST / "scripts"
FIGURES_DIR = VOL_HOST / "outputs" / "figures"

_OCTAVE_HEADER = """\
% Auto-injected by octave_runner — headless plotting
graphics_toolkit("gnuplot");
set(0, "defaultfigurevisible", "off");
set(0, "defaultaxesfontsize", 11);
set(0, "defaultlinelinewidth", 1.4);
pkg load statistics 2>/dev/null;
pkg load optim 2>/dev/null;
warning("off", "all");
"""


def write_m_script(name: str, body: str) -> Path:
    """Write a .m script to vol/scripts/, prepending a headless header."""
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS_DIR / f"{name}.m"
    path.write_text(_OCTAVE_HEADER + "\n" + body, encoding="utf-8")
    return path


def run_m_script(script_path: Path | str, timeout: int = 300) -> tuple[int, str, str]:
    """Execute a .m file via `octave --no-gui` inside the sandbox container."""
    host_path = str(script_path)
    container_path = host_to_container_path(host_path)
    cmd = f"octave --no-gui --quiet --eval \"run('{container_path}')\""
    return docker_exec(container_name(), cmd, timeout=timeout)


def run_m_inline(body: str, name: str = "inline", timeout: int = 300) -> tuple[int, str, str]:
    """Write + run a .m script in one call. Returns (exit_code, stdout, stderr)."""
    path = write_m_script(name, body)
    return run_m_script(path, timeout=timeout)
