"""Archiver — 扫描 vol/outputs，将新产物同步写入 Context Store。"""

import json
import os
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", str(_BASE / "vol")))
CONTEXT_PATH = Path(os.getenv("CONTEXT_STORE", "context_store/context.json"))
OUTPUT_DIR = VOL_HOST / "outputs"


def archive_artifacts(metrics: dict | None = None) -> list[str]:
    """
    扫描 vol/outputs 目录，更新 context_store artifacts 列表。
    可选传入 metrics dict（RMSE、R2 等）一并写入。
    返回新发现的文件列表。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    found = [str(p) for p in OUTPUT_DIR.iterdir() if p.is_file()]

    ctx = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    ctx.setdefault("code_execution", {})["artifacts"] = found
    ctx["code_execution"]["status"] = "success"

    results = ctx.setdefault("results", {"solver_stdout": "", "metrics": {}, "figures": []})
    if metrics:
        results.setdefault("metrics", {}).update(metrics)

    figures = [p for p in found if p.endswith((".png", ".pdf", ".svg"))]
    results["figures"] = figures

    CONTEXT_PATH.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    return found
