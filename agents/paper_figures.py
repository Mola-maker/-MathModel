"""P3.7 — Paper Figure Templates Agent.

Reads solver output artifacts (`vol/outputs/*.csv`) and produces
publication-ready figures into `vol/outputs/figures/` using a deterministic
matplotlib/seaborn template library.

Design notes
------------
- **No LLM dependency.** Template choice is pattern-based on column names and
  dtypes, so it works even when the model router is unreachable.
- **Idempotent / skip-friendly.** Runs as `on_error="skip"`: if the outputs
  directory is empty or unreadable, the agent records a note in context and
  returns without raising.
- **Small surface area.** Seven template renderers covering the common cases
  in MCM/ICM papers: line trend, scatter, bar, histogram, correlation heatmap,
  residual/QQ, and pivot heatmap. Each template writes one PNG and appends a
  manifest entry so P4 (writing_agent) can reference figures by filename.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from agents.orchestrator import load_context, save_context

BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", str(BASE_DIR / "vol")))
OUTPUTS_DIR = VOL_HOST / "outputs"
FIGURES_DIR = OUTPUTS_DIR / "figures"

# Matches matlab_viz for visual consistency across P2.5 / P3.7 outputs.
COLORS = {
    "primary": "#2E5B88",
    "secondary": "#E85D4C",
    "tertiary": "#4A9B7F",
    "neutral": "#7F7F7F",
    "light": "#B8D4E8",
}

# Column-name patterns used for template auto-selection.
_TIME_TOKENS = ("time", "t", "step", "iter", "iteration", "epoch", "year", "day")
_PRED_PAIRS = (("y_true", "y_pred"), ("actual", "predicted"), ("observed", "fitted"))


@dataclass
class FigureEntry:
    name: str
    template: str
    source: str
    path: str
    columns: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    figures: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    source_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _apply_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.labelsize": 11,
            "axes.linewidth": 1.3,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "legend.frameon": False,
            "figure.dpi": 150,
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
        }
    )
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _safe_stem(path: Path) -> str:
    stem = re.sub(r"[^0-9A-Za-z_]+", "_", path.stem).strip("_")
    return stem or "data"


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _find_time_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if col.lower() in _TIME_TOKENS and pd.api.types.is_numeric_dtype(df[col]):
            return col
    return None


def _find_pred_pair(df: pd.DataFrame) -> tuple[str, str] | None:
    cols_lower = {c.lower(): c for c in df.columns}
    for a, b in _PRED_PAIRS:
        if a in cols_lower and b in cols_lower:
            return cols_lower[a], cols_lower[b]
    return None


# ── Template renderers ──────────────────────────────────────────────────────


def _plot_line_trend(df: pd.DataFrame, time_col: str, y_cols: list[str], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.5))
    palette = [COLORS["primary"], COLORS["secondary"], COLORS["tertiary"], COLORS["neutral"]]
    for i, col in enumerate(y_cols[:4]):
        ax.plot(df[time_col], df[col], label=col, color=palette[i % len(palette)], linewidth=1.8)
    ax.set_xlabel(time_col)
    ax.set_ylabel("value")
    ax.set_title("Time-series Trend")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.savefig(out)
    plt.close(fig)


def _plot_scatter(df: pd.DataFrame, x: str, y: str, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.5, 5))
    ax.scatter(df[x], df[y], s=24, alpha=0.7, color=COLORS["primary"], edgecolor="white", linewidth=0.5)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(f"{y} vs {x}")
    ax.grid(True, alpha=0.3)
    fig.savefig(out)
    plt.close(fig)


def _plot_bar(df: pd.DataFrame, cat: str, val: str, out: Path) -> None:
    agg = df.groupby(cat)[val].mean().sort_values(ascending=False).head(15)
    fig, ax = plt.subplots(figsize=(7.2, 4.5))
    ax.bar(range(len(agg)), agg.values, color=COLORS["primary"], alpha=0.85)
    ax.set_xticks(range(len(agg)))
    ax.set_xticklabels([str(x)[:12] for x in agg.index], rotation=30, ha="right")
    ax.set_ylabel(f"mean({val})")
    ax.set_title(f"{val} by {cat}")
    ax.grid(True, axis="y", alpha=0.3)
    fig.savefig(out)
    plt.close(fig)


def _plot_histogram(df: pd.DataFrame, col: str, out: Path) -> None:
    data = df[col].dropna().values
    if data.size < 2:
        return
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ax.hist(data, bins=min(30, max(8, int(np.sqrt(data.size)))), color=COLORS["primary"], alpha=0.8, edgecolor="white")
    ax.axvline(float(np.mean(data)), color=COLORS["secondary"], linestyle="--", linewidth=1.5, label=f"mean={np.mean(data):.3g}")
    ax.set_xlabel(col)
    ax.set_ylabel("frequency")
    ax.set_title(f"Distribution of {col}")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.savefig(out)
    plt.close(fig)


def _plot_correlation(df: pd.DataFrame, numeric: list[str], out: Path) -> None:
    corr = df[numeric].corr().values
    fig, ax = plt.subplots(figsize=(6.8, 5.6))
    im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(numeric)))
    ax.set_yticks(range(len(numeric)))
    ax.set_xticklabels(numeric, rotation=45, ha="right")
    ax.set_yticklabels(numeric)
    for i in range(len(numeric)):
        for j in range(len(numeric)):
            ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center",
                    color="white" if abs(corr[i, j]) > 0.5 else "black", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.04)
    ax.set_title("Correlation Matrix")
    fig.savefig(out)
    plt.close(fig)


def _plot_residual(df: pd.DataFrame, y_true: str, y_pred: str, out: Path) -> None:
    res = df[y_true].values - df[y_pred].values
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))

    axes[0].scatter(df[y_pred], res, s=24, alpha=0.7, color=COLORS["primary"], edgecolor="white", linewidth=0.5)
    axes[0].axhline(0, color=COLORS["secondary"], linestyle="--", linewidth=1.2)
    axes[0].set_xlabel(y_pred)
    axes[0].set_ylabel("residual")
    axes[0].set_title("Residual vs Predicted")
    axes[0].grid(True, alpha=0.3)

    qq = np.sort(res)
    q_theo = np.sort(np.random.default_rng(0).standard_normal(len(res)))
    axes[1].scatter(q_theo, qq, s=20, alpha=0.7, color=COLORS["tertiary"])
    lo, hi = float(min(q_theo.min(), qq.min())), float(max(q_theo.max(), qq.max()))
    axes[1].plot([lo, hi], [lo, hi], color=COLORS["secondary"], linestyle="--", linewidth=1.2)
    axes[1].set_xlabel("theoretical quantiles")
    axes[1].set_ylabel("sample quantiles")
    axes[1].set_title("Q-Q Plot")
    axes[1].grid(True, alpha=0.3)

    fig.savefig(out)
    plt.close(fig)


# ── Auto-detection orchestrator ─────────────────────────────────────────────


def _pick_templates(df: pd.DataFrame) -> list[tuple[str, dict]]:
    """Return list of (template_name, kwargs) tuples for this dataframe."""
    picks: list[tuple[str, dict]] = []
    numeric = _numeric_columns(df)
    if len(numeric) == 0 or len(df) < 2:
        return picks

    pair = _find_pred_pair(df)
    if pair:
        picks.append(("residual", {"y_true": pair[0], "y_pred": pair[1]}))

    tcol = _find_time_column(df)
    if tcol:
        ys = [c for c in numeric if c != tcol][:4]
        if ys:
            picks.append(("line", {"time_col": tcol, "y_cols": ys}))

    if not tcol and len(numeric) >= 2:
        picks.append(("scatter", {"x": numeric[0], "y": numeric[1]}))

    cat_cols = [c for c in df.columns if c not in numeric and df[c].nunique() <= 20]
    if cat_cols and numeric:
        picks.append(("bar", {"cat": cat_cols[0], "val": numeric[0]}))

    if numeric:
        picks.append(("histogram", {"col": numeric[0]}))

    if len(numeric) >= 3:
        picks.append(("correlation", {"numeric": numeric[:8]}))

    return picks


def _render(template: str, df: pd.DataFrame, out: Path, kwargs: dict) -> None:
    if template == "line":
        _plot_line_trend(df, out=out, **kwargs)
    elif template == "scatter":
        _plot_scatter(df, out=out, **kwargs)
    elif template == "bar":
        _plot_bar(df, out=out, **kwargs)
    elif template == "histogram":
        _plot_histogram(df, out=out, **kwargs)
    elif template == "correlation":
        _plot_correlation(df, out=out, **kwargs)
    elif template == "residual":
        _plot_residual(df, out=out, **kwargs)
    else:
        raise ValueError(f"unknown template: {template}")


def _scan_csv_files() -> list[Path]:
    if not OUTPUTS_DIR.exists():
        return []
    return sorted(p for p in OUTPUTS_DIR.glob("*.csv") if p.is_file())


def _render_csv(csv_path: Path) -> tuple[list[FigureEntry], list[dict]]:
    entries: list[FigureEntry] = []
    skipped: list[dict] = []
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        return entries, [{"source": csv_path.name, "reason": f"read_csv failed: {exc}"}]

    picks = _pick_templates(df)
    if not picks:
        skipped.append({"source": csv_path.name, "reason": "no compatible columns"})
        return entries, skipped

    stem = _safe_stem(csv_path)
    for template, kwargs in picks:
        out = FIGURES_DIR / f"auto_{stem}_{template}.png"
        try:
            _render(template, df, out, kwargs)
            used_cols: list[str] = []
            for v in kwargs.values():
                if isinstance(v, list):
                    used_cols.extend(str(x) for x in v)
                else:
                    used_cols.append(str(v))
            entries.append(FigureEntry(
                name=out.name, template=template, source=csv_path.name,
                path=str(out), columns=used_cols,
            ))
        except Exception as exc:
            skipped.append({
                "source": csv_path.name, "template": template,
                "reason": f"render failed: {exc}",
            })
    return entries, skipped


class PaperFiguresAgent:
    """P3.7 — generate publication-ready figures from solver outputs."""

    def run(self) -> dict:
        ctx = load_context()
        result = RunResult()

        csv_files = _scan_csv_files()
        result.source_count = len(csv_files)

        if not csv_files:
            print("  [P3.7] vol/outputs 下无 CSV，跳过")
            return self._write(ctx, result, note="no csv sources")

        FIGURES_DIR.mkdir(parents=True, exist_ok=True)
        _apply_style()

        for csv_path in csv_files:
            entries, skipped = _render_csv(csv_path)
            result.figures.extend(asdict(e) for e in entries)
            result.skipped.extend(skipped)

        print(
            f"  [P3.7] 处理 {len(csv_files)} 个 CSV → 生成 {len(result.figures)} 张图"
            f"（跳过 {len(result.skipped)}）"
        )
        return self._write(ctx, result)

    @staticmethod
    def _write(ctx: dict, result: RunResult, note: str = "") -> dict:
        modeling = ctx.setdefault("modeling", {})
        modeling["paper_figures"] = result.to_dict()
        if note:
            modeling["paper_figures"]["note"] = note
        ctx["phase"] = "P3.7_complete"
        save_context(ctx)
        return ctx


def _render_summary(result: dict[str, Any]) -> str:
    n = len(result.get("figures", []))
    src = result.get("source_count", 0)
    return f"生成 {n} 张图（来自 {src} 个 CSV）"
