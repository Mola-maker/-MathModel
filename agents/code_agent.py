from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import container_name, docker_cp, docker_exec
from agents.prompts import CODER_PROMPT
from agents.flows import Flows
BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", BASE_DIR / "vol"))

SYSTEM_CODEGEN = CODER_PROMPT


def _extract_code(response: str) -> str:
    match = re.search(r"```python\s*(.*?)```", response, re.DOTALL)
    return match.group(1).strip() if match else response.strip()


def _fallback_solver_code() -> str:
    return """import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path("E:/mathmodel/vol/outputs")
OUT.mkdir(parents=True, exist_ok=True)

x = np.linspace(0, 1, 100)
y = x * (1 - x)

plt.figure(figsize=(6, 4))
plt.plot(x, y, label="baseline")
plt.title("Baseline Solver Output")
plt.xlabel("x")
plt.ylabel("y")
plt.legend()
plt.tight_layout()
plt.savefig(OUT / "baseline_solver.png", dpi=150)

result = {"status": "fallback", "peak_y": float(y.max())}
print(json.dumps(result, ensure_ascii=False))
"""


class CodeAgent:
    """P3: code generation + execution + archiving."""

    def _safe_problem_text(self, ctx: dict) -> str:
        text = str(ctx.get("competition", {}).get("problem_text", ""))
        return text[:1200]

    def generate_script(self, step_key: str, coder_prompt: str, ctx: dict) -> str:
        """Generate a Python script for any step using the unified CODER_PROMPT."""
        user_prompt = (
            f"{coder_prompt}\n\n"
            f"Problem snippet:\n{self._safe_problem_text(ctx)}"
        )
        try:
            code = _extract_code(call_model(SYSTEM_CODEGEN, user_prompt, task="codegen"))
        except Exception as e:
            print(f"[CodeAgent] {step_key} generation failed, using fallback: {e}")
            code = _fallback_solver_code()

        script_name = f"{step_key}.py"
        script_path = VOL_HOST / "scripts" / script_name
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(code, encoding="utf-8")
        return str(script_path)

    # Legacy methods kept for backward compatibility
    def generate_solver(self, ctx: dict) -> str:
        model = ctx.get("modeling", {}).get("primary_model", {})
        variables = ctx.get("modeling", {}).get("variables", {})
        method = ctx.get("modeling", {}).get("solution_method", "")
        prompt = (
            f"Model description:\n{json.dumps(model, ensure_ascii=False, indent=2)}\n\n"
            f"Variables:\n{json.dumps(variables, ensure_ascii=False)}\n\n"
            f"Solution method:\n{method}"
        )
        return self.generate_script("solver", prompt, ctx)

    def generate_visualization(self, ctx: dict, results_summary: str) -> str:
        prompt = (
            f"Results summary:\n{results_summary}\n\n"
            f"Model type: {ctx.get('modeling', {}).get('model_type', '')}\n\n"
            "Generate 2-3 academic figures with data feature output."
        )
        return self.generate_script("visualization", prompt, ctx)

    def generate_sensitivity(self, ctx: dict) -> str:
        variables = ctx.get("modeling", {}).get("variables", {})
        model = ctx.get("modeling", {}).get("primary_model", {})
        prompt = (
            f"Model:\n{json.dumps(model, ensure_ascii=False, indent=2)}\n\n"
            f"Key parameters:\n{json.dumps(variables, ensure_ascii=False)}\n\n"
            "Perform sensitivity analysis with ±10%, ±20%, ±50% ranges.\n"
            "Generate heatmap or tornado chart."
        )
        return self.generate_script("sensitivity", prompt, ctx)

    def _sync_to_container(self, script_path: str) -> None:
        fname = Path(script_path).name
        docker_cp(script_path, container_name(), f"/tmp/{fname}")

    def _run_in_container(self, script_name: str) -> tuple[int, str, str]:
        exit_code, stdout, stderr = docker_exec(container_name(), f"python3 /tmp/{script_name}", timeout=300)
        log = VOL_HOST / "logs" / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"[STDOUT]\n{stdout}\n[STDERR]\n{stderr}", encoding="utf-8")
        return exit_code, stdout, stderr

    def run(self) -> dict:
        from sandbox.archiver import archive_artifacts
        from sandbox.loop import execute_with_healing

        ctx = load_context()
        flows = Flows(ctx)
        steps = flows.get_all_steps()

        all_stdout: dict[str, str] = {}

        for step in steps:
            print(f"\n[P3] === {step.key} ===")
            script_path = self.generate_script(step.key, step.coder_prompt, ctx)
            self._sync_to_container(script_path)

            script_name = Path(script_path).name
            result = execute_with_healing(script_name)
            ctx = load_context()

            if result.get("status") == "logic_err":
                print(f"[P3-WARN] {step.key} 逻辑错误，跳过")
                all_stdout[step.key] = f"LOGIC_ERROR: {result.get('error', '')}"
                continue

            _, stdout, _ = self._run_in_container(script_name)
            summary = stdout[-2000:] if stdout else "No stdout"
            all_stdout[step.key] = summary
            print(f"[P3] {step.key} 完成")

        artifacts = archive_artifacts()

        # Merge all stdout
        combined_summary = "\n\n".join(
            f"=== {k} ===\n{v}" for k, v in all_stdout.items()
        )

        ctx = load_context()
        ctx["phase"] = "P3_complete"
        ctx.setdefault("results", {})["solver_stdout"] = combined_summary
        ctx["results"]["per_step_results"] = all_stdout
        ctx.setdefault("code_execution", {})["artifacts"] = artifacts
        save_context(ctx)
        return ctx
