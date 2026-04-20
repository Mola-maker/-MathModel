"""P1.7 — Data Simulation Agent.

Augments undersized datasets (after P1.5 cleaning) with synthetic rows so
downstream modeling / solver stages have enough data to work with.

Design notes
------------
- **Gaussian-perturbation bootstrap.** Re-samples cleaned rows and adds
  column-scaled Gaussian noise to numeric columns; non-numeric columns are
  bootstrap-copied verbatim. This keeps the joint distribution close to the
  original (small KS-statistic) without pretending to discover new structure.
- **Never overwrites cleaned files.** Output goes to `augmented_{stem}.csv`
  alongside the original; P3 solver can opt in.
- **`_sim_origin` column** (values: `"real"` / `"simulated"`) tags every row
  so downstream code can filter if needed.
- **Runs as `on_error="skip"`**: if no eligible files, the agent records a
  note in context and returns without raising.
- **No LLM dependency.** Pure statistical augmentation — reliable even when
  the model router is unreachable.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from agents.orchestrator import load_context, save_context

try:
    from scipy import stats as _scipy_stats
except ImportError:
    _scipy_stats = None

BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", str(BASE_DIR / "vol")))
DATA_DIR = VOL_HOST / "data"

# ── Tunables ────────────────────────────────────────────────────────────────
MIN_ROWS_FOR_MODELING = 30     # fewer rows → trigger augmentation
TARGET_ROWS = 100              # expansion cap (including real rows)
PERTURBATION_SIGMA = 0.05      # relative noise std for numeric cols
KS_WARNING_THRESHOLD = 0.30    # per-column KS stat above this gets flagged
SIM_ORIGIN_COL = "_sim_origin"


@dataclass
class SimulatedFile:
    source: str
    output: str
    original_rows: int
    simulated_rows: int
    method: str
    preserved_cols: list[str] = field(default_factory=list)
    numeric_cols: list[str] = field(default_factory=list)
    ks_stats: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    trigger_threshold: int = MIN_ROWS_FOR_MODELING
    target_rows: int = TARGET_ROWS
    files: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    simulated_files: list[str] = field(default_factory=list)
    total_rows_added: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _ks_2samp(a: np.ndarray, b: np.ndarray) -> float:
    """Two-sample KS statistic. Uses scipy if available, else numpy-based fallback."""
    if _scipy_stats is not None:
        stat, _ = _scipy_stats.ks_2samp(a, b)
        return float(stat)
    # Fallback: compute empirical CDF difference manually.
    combined = np.sort(np.concatenate([a, b]))
    cdf_a = np.searchsorted(np.sort(a), combined, side="right") / a.size
    cdf_b = np.searchsorted(np.sort(b), combined, side="right") / b.size
    return float(np.max(np.abs(cdf_a - cdf_b)))


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c != SIM_ORIGIN_COL]


def _iter_cleaned_entries(ctx: dict):
    """Yield (filename, cleaned_file_path) for every successful P1.5 entry."""
    results = ctx.get("data_cleaning", {}).get("results", {}) or {}
    for fname, entry in results.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "success":
            continue
        cleaned = entry.get("cleaned_file")
        if not cleaned:
            continue
        path = Path(cleaned)
        if not path.is_absolute():
            path = (BASE_DIR / cleaned).resolve()
        if path.exists():
            yield fname, path


def _gaussian_bootstrap(
    df: pd.DataFrame,
    target_rows: int,
    sigma: float,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, dict[str, float], list[str]]:
    """Generate synthetic rows; return (augmented_df, ks_stats, warnings)."""
    n_real = len(df)
    n_needed = max(0, target_rows - n_real)
    numeric_cols = _numeric_columns(df)
    warnings: list[str] = []

    if n_needed == 0:
        out = df.copy()
        out[SIM_ORIGIN_COL] = "real"
        return out, {}, warnings

    # Bootstrap-sample row indices; copy every column verbatim first.
    sampled_idx = rng.integers(0, n_real, size=n_needed)
    synth = df.iloc[sampled_idx].reset_index(drop=True).copy()

    # Perturb numeric columns: add N(0, sigma * col_std) scaled noise.
    ks_stats: dict[str, float] = {}
    for col in numeric_cols:
        col_values = df[col].to_numpy(dtype=float)
        finite = col_values[np.isfinite(col_values)]
        if finite.size < 2:
            warnings.append(f"{col}: <2 finite values, no noise added")
            continue
        std = float(np.std(finite, ddof=1))
        if std == 0.0:
            # Constant column: keep verbatim.
            continue
        noise = rng.normal(0.0, sigma * std, size=n_needed)
        synth[col] = synth[col].astype(float) + noise

        try:
            stat = _ks_2samp(finite, synth[col].to_numpy(dtype=float))
            ks_stats[col] = round(stat, 4)
            if stat > KS_WARNING_THRESHOLD:
                warnings.append(f"{col}: KS stat {stat:.3f} > {KS_WARNING_THRESHOLD}")
        except Exception:
            pass

    real_part = df.copy()
    real_part[SIM_ORIGIN_COL] = "real"
    synth[SIM_ORIGIN_COL] = "simulated"
    out = pd.concat([real_part, synth], ignore_index=True)
    return out, ks_stats, warnings


def _augment_one(fname: str, cleaned_path: Path, rng: np.random.Generator) -> tuple[SimulatedFile | None, dict | None]:
    """Process a single cleaned file. Returns (simulated_file, None) or (None, skip_entry)."""
    try:
        df = pd.read_csv(cleaned_path)
    except Exception as exc:
        return None, {"source": fname, "reason": f"read_csv failed: {exc}"}

    n_real = len(df)
    if n_real == 0:
        return None, {"source": fname, "reason": "empty dataframe"}

    if n_real >= MIN_ROWS_FOR_MODELING:
        return None, {"source": fname, "reason": f"sufficient rows ({n_real} >= {MIN_ROWS_FOR_MODELING})"}

    numeric_cols = _numeric_columns(df)
    if not numeric_cols:
        return None, {"source": fname, "reason": "no numeric columns to perturb"}

    out_df, ks_stats, warns = _gaussian_bootstrap(df, TARGET_ROWS, PERTURBATION_SIGMA, rng)
    out_path = cleaned_path.with_name(f"augmented_{cleaned_path.stem.replace('cleaned_', '', 1)}.csv")
    out_df.to_csv(out_path, index=False, encoding="utf-8")

    preserved = [c for c in df.columns if c not in numeric_cols]
    simulated_added = len(out_df) - n_real
    return (
        SimulatedFile(
            source=str(cleaned_path),
            output=str(out_path),
            original_rows=n_real,
            simulated_rows=simulated_added,
            method="gaussian_bootstrap",
            preserved_cols=preserved,
            numeric_cols=numeric_cols,
            ks_stats=ks_stats,
            warnings=warns,
        ),
        None,
    )


class DataSimulationAgent:
    """P1.7 — augment undersized cleaned CSVs with Gaussian-perturbation bootstrap."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)

    def run(self) -> dict:
        ctx = load_context()
        result = SimulationResult()

        entries = list(_iter_cleaned_entries(ctx))
        if not entries:
            print("  [P1.7] 未发现 P1.5 清洗产物，跳过")
            return self._write(ctx, result, note="no cleaned files")

        for fname, path in entries:
            sim, skip = _augment_one(fname, path, self._rng)
            if sim is not None:
                result.files.append(asdict(sim))
                result.simulated_files.append(sim.output)
                result.total_rows_added += sim.simulated_rows
            elif skip is not None:
                result.skipped.append(skip)

        print(
            f"  [P1.7] 扫描 {len(entries)} 个清洗文件 → "
            f"增强 {len(result.files)} 个 (+{result.total_rows_added} 行)，"
            f"跳过 {len(result.skipped)}"
        )
        return self._write(ctx, result)

    @staticmethod
    def _write(ctx: dict, result: SimulationResult, note: str = "") -> dict:
        payload = result.to_dict()
        if note:
            payload["note"] = note
        ctx["data_simulation"] = payload
        ctx["phase"] = "P1.7_complete"
        save_context(ctx)
        return ctx
