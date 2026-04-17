"""Data Cleaning Agent — P1.5: 自动扫描数据文件，生成清洗脚本，执行并输出报告。

流程：
1. 扫描 vol/data/ 下所有 .xlsx/.csv 文件
2. 对每个文件生成「预览摘要」（前5行 + dtypes + shape + 缺失统计）
3. 调用 LLM 生成清洗脚本（缺失值处理 + 异常值检测 + 类型转换 + EDA可视化）
4. 在 sandbox 中执行脚本（含自愈）
5. 收集清洗后数据 + 清洗报告 + EDA 图片
6. 写入 context_store 供后续 P2/P3 使用
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import container_name, docker_cp, docker_exec, host_to_container_path, vol_host

BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", BASE_DIR / "vol"))
DATA_DIR = VOL_HOST / "data"
SCRIPTS_DIR = VOL_HOST / "scripts"
OUTPUTS_DIR = VOL_HOST / "outputs"
FIGURES_DIR = OUTPUTS_DIR / "figures"

VOL_CONTAINER = os.getenv("VOL_CONTAINER", "/workspace/vol")

# Maximum heal iterations for cleaning scripts
MAX_HEAL_ITER = 3

SYSTEM_PREVIEW = """你是数据分析助手。根据以下数据文件的元信息（列名、数据类型、形状、前几行、缺失统计），
输出严格 JSON（不含 markdown 代码块），结构：
{
  "file_summary": "一句话描述该数据集",
  "rows": 行数,
  "cols": 列数,
  "column_analysis": [
    {
      "name": "列名",
      "dtype": "数据类型",
      "missing_count": 缺失数,
      "missing_pct": 缺失百分比,
      "suggested_action": "建议的清洗操作",
      "notes": "备注"
    }
  ],
  "potential_issues": ["可能的问题1", "可能的问题2"],
  "data_type": "时序|截面|面板|地理|文本|混合"
}"""

SYSTEM_CLEANING_CODE = """你是一名 Python 数据清洗专家，为数学建模竞赛生成可运行的数据清洗和 EDA 脚本。

# 环境
- Python 3.10+, pandas, numpy, matplotlib, seaborn, scipy, openpyxl
- matplotlib 必须用 Agg backend
- 中文字体配置已包含

# 输入
你会收到数据文件的元信息和清洗建议。根据这些信息生成完整的 Python 脚本。

# 脚本必须包含以下模块（按顺序）：

## 1. 数据加载
- 用 pd.read_excel() 或 pd.read_csv()，智能编码（utf-8 → gbk → latin-1）
- 打印 .info() 和 .head()

## 2. 缺失值报告与处理
- 逐列统计缺失数和缺失率
- 缺失率 < 5%：均值/中位数填充（数值列）或众数填充（分类列）
- 缺失率 5-20%：线性插值（时序）或 KNN 填充
- 缺失率 > 50%：删除该列，并打印警告
- 打印完整缺失值处理报告

## 3. 数据类型转换
- 日期列自动解析为 datetime
- 数值列中的字符串（如 "N/A", "-", "无"）替换为 NaN 后转 float
- 分类变量标注

## 4. 异常值检测
- 对所有数值列用 IQR 法检测异常值
- 打印每列的异常值数量和占比
- 异常值默认保留（标记但不删除），极端值（>5倍 IQR）删除

## 5. 描述性统计
- 输出 describe() + 中位数 + 偏度
- 格式化为论文可引用的表格

## 6. EDA 可视化（必须生成）
- 数值列分布：直方图 + KDE 叠加（最多选 6 个关键列）
- 相关性热力图：Pearson 相关系数矩阵
- 缺失值可视化：缺失模式矩阵图
- 箱线图：数值列异常值分布
- 所有图片保存到指定目录，300 dpi，tight layout

## 7. 清洗后数据保存
- 保存为 cleaned_{原文件名}.csv（UTF-8）
- 保存清洗报告为 JSON

## 8. 输出汇总
- 打印清洗前后的行数/列数变化
- 打印删除/填充/转换的统计
- 打印生成的图片列表

# 可视化配置（必须在脚本开头）
```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

plt.rcParams.update({
    'font.family': 'Arial',
    'font.size': 11,
    'axes.titlesize': 12,
    'axes.titleweight': 'bold',
    'axes.spines.top': False,
    'axes.spines.right': False,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
})
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
sns.set_theme(style='ticks')
```

# 输出格式
- 输出完整可运行 Python 脚本
- 用 ```python ... ``` 包裹代码
- 禁止交互式输入
- 所有路径使用参数中给出的路径
- 禁止写入子脚本到 /tmp 或其他路径再 subprocess 调用（所有逻辑必须在一个脚本内完成）
- 禁止在字符串或多行字符串中嵌入 ``` 围栏
"""


# ── Structured spec models (Pydantic) ────────────────────────────────────────

class ColumnSpec(BaseModel):
    name: str
    dtype: Literal["numeric", "datetime", "categorical", "id", "drop"]
    missing_action: Literal[
        "fill_mean", "fill_median", "fill_mode",
        "fill_zero", "interpolate", "drop_rows", "drop_col", "keep"
    ]
    outlier_action: Literal["clip_iqr", "keep", "drop_rows"]


class CleaningSpec(BaseModel):
    read_func: Literal["pd.read_excel", "pd.read_csv"]
    columns: list[ColumnSpec]
    plot_cols: list[str]  # numeric column names to include in EDA plots (≤ 6)


SYSTEM_SPEC = """\
你是数据清洗专家。根据数据文件预览信息，输出一份清洗规格 JSON。
严格输出 JSON，不要包含任何解释或 markdown 代码块。

格式（字段必须完全匹配，不能添加额外字段）:
{
  "read_func": "pd.read_excel",
  "columns": [
    {
      "name": "列名",
      "dtype": "numeric | datetime | categorical | id | drop",
      "missing_action": "fill_mean | fill_median | fill_mode | fill_zero | interpolate | drop_rows | drop_col | keep",
      "outlier_action": "clip_iqr | keep | drop_rows"
    }
  ],
  "plot_cols": ["数值列名1", "数值列名2"]
}

规则:
- dtype=id 或 dtype=drop 时 missing_action="keep", outlier_action="keep"
- 缺失率 > 80% 时 dtype="drop"
- plot_cols 只填数值列名，最多 6 个
- read_func 根据文件扩展名选择
"""


def _request_cleaning_spec(preview: dict) -> "CleaningSpec | None":
    """Ask LLM for a structured spec; validate with Pydantic. Retry up to 3×."""
    user_prompt = (
        f"数据文件预览:\n{json.dumps(preview, ensure_ascii=False, indent=2)}\n\n"
        "请输出清洗规格 JSON："
    )
    for attempt in range(3):
        try:
            raw = call_model(SYSTEM_SPEC, user_prompt, task="extraction")
            raw = raw.strip()
            # Strip any markdown fence the LLM might add despite instructions
            if raw.startswith("```"):
                lines = raw.splitlines()
                end = next(
                    (i for i in range(len(lines) - 1, 0, -1) if lines[i].strip() == "```"),
                    len(lines),
                )
                raw = "\n".join(lines[1:end]).strip()
            data = json.loads(raw)
            return CleaningSpec(**data)
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            print(f"  [P1.5] spec 解析失败 (第 {attempt + 1} 次): {exc}")
    return None


def _build_script_from_spec(
    spec: "CleaningSpec",
    data_path: str,
    output_dir: str,
    fig_dir: str,
    stem: str,
) -> str:
    """Generate a deterministic, template-based cleaning script from a validated spec.

    The LLM provides *decisions* (what dtype each column is, how to fill missing
    values, etc.); this function writes the actual Python — no LLM-generated code,
    no syntax surprises.
    """
    c: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────
    c += [
        "import matplotlib",
        "matplotlib.use('Agg')",
        "import matplotlib.pyplot as plt",
        "import seaborn as sns",
        "import pandas as pd",
        "import numpy as np",
        "import json, os, sys",
        "",
        "plt.rcParams.update({'font.family': 'Arial', 'font.size': 10,",
        "                      'savefig.dpi': 150, 'savefig.bbox': 'tight'})",
        "plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']",
        "plt.rcParams['axes.unicode_minus'] = False",
        "sns.set_theme(style='ticks')",
        f"os.makedirs({repr(fig_dir)}, exist_ok=True)",
        "",
    ]

    # ── Load data ────────────────────────────────────────────────────────
    dp = repr(data_path)
    if spec.read_func == "pd.read_excel":
        c += [
            f"_path = {dp}",
            "try:",
            "    df = pd.read_excel(_path)",
            "except Exception:",
            "    try:",
            "        df = pd.read_csv(_path, encoding='utf-8')",
            "    except Exception:",
            "        df = pd.read_csv(_path, encoding='gbk')",
        ]
    else:
        c += [
            f"_path = {dp}",
            "try:",
            "    df = pd.read_csv(_path, encoding='utf-8')",
            "except UnicodeDecodeError:",
            "    try:",
            "        df = pd.read_csv(_path, encoding='gbk')",
            "    except Exception:",
            "        df = pd.read_csv(_path, encoding='latin-1')",
        ]
    c += [
        "print('Loaded:', df.shape)",
        "_orig_len = len(df)",
        "_report = {'original_shape': list(df.shape), 'ops': []}",
        "",
    ]

    # ── Drop columns ─────────────────────────────────────────────────────
    drop_names = [col.name for col in spec.columns if col.dtype == "drop"]
    if drop_names:
        c += [
            f"df = df.drop(columns={repr(drop_names)}, errors='ignore')",
            f"_report['ops'].append({{'action': 'drop_cols', 'cols': {repr(drop_names)}}})",
            "",
        ]

    # ── Per-column operations ─────────────────────────────────────────────
    for col in spec.columns:
        if col.dtype in ("drop", "id"):
            continue
        cn = repr(col.name)

        c.append(f"if {cn} in df.columns:")

        # Type coercion
        if col.dtype == "numeric":
            c.append(f"    df[{cn}] = pd.to_numeric(df[{cn}], errors='coerce')")
        elif col.dtype == "datetime":
            c.append(f"    df[{cn}] = pd.to_datetime(df[{cn}], errors='coerce')")

        # Missing value handling
        act = col.missing_action
        if act == "fill_mean":
            c += [
                f"    _fv = df[{cn}].mean() if pd.api.types.is_numeric_dtype(df[{cn}]) else None",
                f"    if _fv is not None and pd.notna(_fv): df[{cn}] = df[{cn}].fillna(_fv)",
                f"    _report['ops'].append({{'col': {cn}, 'action': 'fill_mean'}})",
            ]
        elif act == "fill_median":
            c += [
                f"    _fv = df[{cn}].median() if pd.api.types.is_numeric_dtype(df[{cn}]) else None",
                f"    if _fv is not None and pd.notna(_fv): df[{cn}] = df[{cn}].fillna(_fv)",
                f"    _report['ops'].append({{'col': {cn}, 'action': 'fill_median'}})",
            ]
        elif act == "fill_mode":
            c += [
                f"    _mv = df[{cn}].mode()",
                f"    if len(_mv) > 0: df[{cn}] = df[{cn}].fillna(_mv.iloc[0])",
                f"    _report['ops'].append({{'col': {cn}, 'action': 'fill_mode'}})",
            ]
        elif act == "fill_zero":
            c += [
                f"    df[{cn}] = df[{cn}].fillna(0)",
                f"    _report['ops'].append({{'col': {cn}, 'action': 'fill_zero'}})",
            ]
        elif act == "interpolate":
            c += [
                f"    df[{cn}] = df[{cn}].interpolate(method='linear', limit_direction='both')",
                f"    _report['ops'].append({{'col': {cn}, 'action': 'interpolate'}})",
            ]
        elif act == "drop_rows":
            c += [
                f"    _b = len(df); df = df.dropna(subset=[{cn}])",
                f"    _report['ops'].append({{'col': {cn}, 'action': 'drop_rows', 'dropped': _b - len(df)}})",
            ]
        elif act == "drop_col":
            c += [
                f"    df = df.drop(columns=[{cn}], errors='ignore')",
                f"    _report['ops'].append({{'col': {cn}, 'action': 'drop_col'}})",
            ]

        # Outlier handling (numeric only)
        if col.dtype == "numeric" and col.outlier_action in ("clip_iqr", "drop_rows"):
            c += [
                f"    if {cn} in df.columns and pd.api.types.is_numeric_dtype(df[{cn}]):",
                f"        _q1 = df[{cn}].quantile(0.25)",
                f"        _q3 = df[{cn}].quantile(0.75)",
                f"        _iqr = _q3 - _q1",
            ]
            if col.outlier_action == "clip_iqr":
                c.append(f"        df[{cn}] = df[{cn}].clip(_q1 - 1.5*_iqr, _q3 + 1.5*_iqr)")
            else:
                c.append(f"        df = df[df[{cn}].between(_q1 - 1.5*_iqr, _q3 + 1.5*_iqr)]")

    c += ["", f"print('After cleaning:', df.shape)", ""]

    # ── EDA plots ────────────────────────────────────────────────────────
    # Only plot columns that exist and are numeric in the spec
    valid_plot = [
        col.name for col in spec.columns
        if col.name in spec.plot_cols and col.dtype == "numeric"
    ]
    c.append(
        f"_plot_cols = [c for c in {repr(valid_plot)} "
        f"if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]"
    )
    c.append("_figs = []")
    c.append("")

    # Distribution histograms
    c += [
        "if _plot_cols:",
        "    _n = min(len(_plot_cols), 6)",
        "    _nc = min(_n, 3); _nr = (_n + _nc - 1) // _nc",
        "    _fig, _axes = plt.subplots(_nr, _nc, figsize=(_nc*4, _nr*3))",
        "    _ax_list = list(_axes.flat) if hasattr(_axes, 'flat') else [_axes]",
        "    for _i, _col in enumerate(_plot_cols[:_n]):",
        "        _ax = _ax_list[_i]",
        "        _vals = df[_col].dropna()",
        "        if len(_vals) > 0:",
        "            _ax.hist(_vals, bins=30, color='#c96442', alpha=0.75, edgecolor='white')",
        "        _ax.set_title(str(_col))",
        "    for _ax in _ax_list[_n:]: _ax.set_visible(False)",
        f"    _fp = os.path.join({repr(fig_dir)}, 'distribution_{stem}.png')",
        "    plt.tight_layout(); plt.savefig(_fp); plt.close(); _figs.append(_fp)",
        "",
    ]

    # Correlation heatmap
    c += [
        "if len(_plot_cols) >= 2:",
        "    _num = df[[c for c in _plot_cols if c in df.columns]].select_dtypes(include='number')",
        "    if len(_num.columns) >= 2:",
        "        _fig, _ax = plt.subplots(figsize=(max(6, len(_num.columns)), max(5, len(_num.columns))))",
        "        sns.heatmap(_num.corr(), ax=_ax, annot=True, fmt='.2f',",
        "                    cmap='RdBu_r', center=0, square=True, linewidths=.5)",
        "        _ax.set_title('Correlation')",
        f"        _fp = os.path.join({repr(fig_dir)}, 'correlation_{stem}.png')",
        "        plt.tight_layout(); plt.savefig(_fp); plt.close(); _figs.append(_fp)",
        "",
    ]

    # Missing value bar
    c += [
        "_miss = df.isnull().sum()",
        "_miss = _miss[_miss > 0]",
        "if len(_miss) > 0:",
        "    _fig, _ax = plt.subplots(figsize=(max(6, len(_miss)), 3))",
        "    _miss.plot.bar(ax=_ax, color='#c96442', alpha=0.8)",
        "    _ax.set_title('Missing Values'); _ax.set_ylabel('Count')",
        "    plt.xticks(rotation=45, ha='right')",
        f"    _fp = os.path.join({repr(fig_dir)}, 'missing_{stem}.png')",
        "    plt.tight_layout(); plt.savefig(_fp); plt.close(); _figs.append(_fp)",
        "",
    ]

    # Boxplots
    c += [
        "if _plot_cols:",
        "    _n = min(len(_plot_cols), 6)",
        "    _nc = min(_n, 3); _nr = (_n + _nc - 1) // _nc",
        "    _fig, _axes = plt.subplots(_nr, _nc, figsize=(_nc*3, _nr*3))",
        "    _ax_list = list(_axes.flat) if hasattr(_axes, 'flat') else [_axes]",
        "    for _i, _col in enumerate(_plot_cols[:_n]):",
        "        _ax = _ax_list[_i]",
        "        _vals = df[_col].dropna()",
        "        if len(_vals) > 0:",
        "            _ax.boxplot(_vals, patch_artist=True,",
        "                        boxprops=dict(facecolor='#c96442', alpha=0.6))",
        "        _ax.set_title(str(_col))",
        "    for _ax in _ax_list[_n:]: _ax.set_visible(False)",
        f"    _fp = os.path.join({repr(fig_dir)}, 'boxplot_{stem}.png')",
        "    plt.tight_layout(); plt.savefig(_fp); plt.close(); _figs.append(_fp)",
        "",
    ]

    # ── Save outputs ─────────────────────────────────────────────────────
    c += [
        f"_out = os.path.join({repr(output_dir)}, 'cleaned_{stem}.csv')",
        "df.to_csv(_out, index=False, encoding='utf-8')",
        "print('Saved:', _out)",
        "_report['cleaned_shape'] = list(df.shape)",
        "_report['rows_removed'] = _orig_len - len(df)",
        "_report['figures'] = _figs",
        f"_rpt = os.path.join({repr(output_dir)}, 'cleaning_report_{stem}.json')",
        "with open(_rpt, 'w', encoding='utf-8') as _f:",
        "    json.dump(_report, _f, ensure_ascii=False, indent=2)",
        "print('Report:', _rpt)",
        "print('Figures:', _figs)",
        "print('DONE')",
    ]

    return "\n".join(c)


def _extract_code(response: str) -> str:
    """Extract Python code from LLM response, stripping markdown fences robustly.

    Uses line-by-line scanning so that triple backticks *inside* string
    literals (e.g. LLM wrote a sub-script in a heredoc) don't confuse the
    regex and cause partial extraction.
    """
    lines = response.strip().splitlines()

    # Find the first opening fence (```python / ```py / ```)
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```python") or stripped.startswith("```py") or stripped == "```":
            start = i
            break

    if start is not None:
        # Find the matching closing fence after the opening
        end = None
        for i in range(start + 1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        code_lines = lines[start + 1 : end] if end is not None else lines[start + 1 :]
        return "\n".join(code_lines).strip()

    # Fallback: strip any leading/trailing fence lines and return
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _generate_preview_script(file_path: str) -> str:
    """Generate a quick preview script for a data file.

    file_path is a host (Windows) path. The script runs inside the container,
    so we convert it to the container path for all file I/O.
    """
    ext = Path(file_path).suffix.lower()
    container_path = host_to_container_path(file_path)
    read_func = "pd.read_excel" if ext in (".xlsx", ".xls") else "pd.read_csv"
    fname = Path(file_path).name

    return f'''import pandas as pd
import json
import sys

try:
    df = {read_func}(r"{container_path}")
except Exception:
    try:
        df = pd.read_csv(r"{container_path}", encoding="gbk")
    except Exception:
        df = pd.read_csv(r"{container_path}", encoding="latin-1")

info = {{
    "file": "{fname}",
    "shape": list(df.shape),
    "columns": list(df.columns),
    "dtypes": {{col: str(dt) for col, dt in df.dtypes.items()}},
    "missing": {{col: int(df[col].isnull().sum()) for col in df.columns}},
    "missing_pct": {{col: round(df[col].isnull().sum() / len(df) * 100, 2) for col in df.columns}},
    "head": df.head(5).to_dict(orient="records"),
    "numeric_cols": list(df.select_dtypes(include="number").columns),
    "non_numeric_cols": list(df.select_dtypes(exclude="number").columns),
}}

print(json.dumps(info, ensure_ascii=False, default=str))
'''


class DataCleaningAgent:
    """P1.5: 数据清洗 + EDA 可视化。"""
    def _discover_data_files(self) -> list[Path]:
        """Find all data files in vol/data/."""
        if not DATA_DIR.exists():
            return []
        exts = {".xlsx", ".xls", ".csv", ".tsv"}
        files = [
            f for f in sorted(DATA_DIR.iterdir())
            if f.suffix.lower() in exts and not f.name.startswith("cleaned_")
        ]
        return files

    def _ensure_data_in_container(self) -> None:
        """Ensure required directories exist in the sandbox container.

        Data files don't need to be copied — both containers share the ./vol
        bind mount, so /app/vol/data/* in mathmodel-app is already visible as
        /workspace/vol/data/* in mathmodel-sandbox.
        """
        cname = container_name()
        vol_container = os.getenv("VOL_CONTAINER", "/workspace/vol")
        docker_exec(cname, f"mkdir -p {vol_container}/data {vol_container}/outputs/figures {vol_container}/scripts")

    def _sync_outputs_from_container(self) -> None:
        """No-op: outputs written by sandbox scripts are instantly visible here.

        Both containers share ./vol via bind mount — sandbox writes to
        /workspace/vol/data/cleaned_*.csv and /workspace/vol/outputs/figures/
        which are the same physical directory as /app/vol/data/ and
        /app/vol/outputs/figures/ in this container.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    def _run_in_container(self, script_path: str) -> tuple[int, str, str]:
        """Run script in sandbox via shared vol bind mount (no docker cp needed)."""
        import shlex
        # Both containers share the vol/ bind mount:
        #   mathmodel-app:  /app/vol  →  host ./vol
        #   mathmodel-sandbox: /workspace/vol  →  host ./vol
        # So a script written to /app/vol/scripts/foo.py is immediately
        # accessible at /workspace/vol/scripts/foo.py in the sandbox.
        container_path = host_to_container_path(script_path)
        exit_code, stdout, stderr = docker_exec(
            container_name(), f"python3 {shlex.quote(container_path)}", timeout=300
        )
        return exit_code, stdout, stderr

    def _run_local(self, script_path: str) -> tuple[int, str, str]:
        """Run script locally as fallback (no Docker)."""
        import subprocess
        result = subprocess.run(
            ["python", script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        return result.returncode, result.stdout, result.stderr

    def _execute_script(self, script_path: str) -> tuple[int, str, str]:
        """Execute script, trying container first then local fallback."""
        try:
            return self._run_in_container(script_path)
        except Exception as e:
            print(f"  [P1.5] Docker 不可用 ({e})，使用本地执行")
            return self._run_local(script_path)

    def preview_file(self, file_path: Path) -> dict:
        """Generate a quick metadata preview of a data file."""
        preview_script = _generate_preview_script(str(file_path))
        script_path = SCRIPTS_DIR / f"preview_{file_path.stem}.py"
        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        script_path.write_text(preview_script, encoding="utf-8")

        exit_code, stdout, stderr = self._execute_script(str(script_path))

        if exit_code != 0:
            print(f"  [P1.5] 预览失败: {file_path.name}")
            print(f"  stderr: {stderr[:500]}")
            return {"file": file_path.name, "error": stderr[:500]}

        # Parse JSON from stdout
        try:
            # Find the JSON line in stdout
            for line in stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    return json.loads(line)
            return {"file": file_path.name, "error": "No JSON in stdout", "raw": stdout[:500]}
        except json.JSONDecodeError:
            return {"file": file_path.name, "error": "JSON parse failed", "raw": stdout[:500]}

    def generate_cleaning_script(self, file_path: Path, preview: dict, analysis: dict) -> str:
        """Generate a cleaning + EDA script for one data file.

        Strategy:
          1. Ask LLM for a *structured spec* (JSON decisions only, validated
             with Pydantic) — no code generation yet.
          2. Build the actual Python script from the spec using our own
             deterministic template (zero LLM code, zero syntax bugs).
          3. Fall back to the old LLM codegen approach only if spec parsing
             fails after 3 retries.
        """
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)
        fig_dir    = host_to_container_path(str(FIGURES_DIR))
        data_path  = host_to_container_path(str(file_path))
        output_dir = host_to_container_path(str(DATA_DIR))
        stem       = file_path.stem

        # ── Path 1: spec-based (preferred, no LLM code gen) ──────────────
        spec = _request_cleaning_spec(preview)
        if spec is not None:
            print("  [P1.5] 使用结构化规格生成清洗脚本（无 LLM 代码生成）")
            return _build_script_from_spec(spec, data_path, output_dir, fig_dir, stem)

        # ── Path 2: fallback — LLM generates full Python script ───────────
        print("  [P1.5] spec 解析失败，回退到 LLM 代码生成")
        user_prompt = (
            f"数据文件路径: {data_path}\n"
            f"清洗后保存目录: {output_dir}\n"
            f"图片保存目录: {fig_dir}\n\n"
            f"数据预览信息:\n{json.dumps(preview, ensure_ascii=False, indent=2)}\n\n"
            f"LLM 分析建议:\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
            "请生成完整可运行的数据清洗 + EDA 脚本。"
        )
        response = call_model(SYSTEM_CLEANING_CODE, user_prompt, task="codegen")
        code = _extract_code(response)
        if code.lstrip().startswith("```"):
            code = re.sub(r"^```[^\n]*\n?", "", code.lstrip())
            code = re.sub(r"\n?```\s*$", "", code.rstrip())
        return code

    def analyze_preview(self, preview: dict) -> dict:
        """Call LLM to analyze preview and suggest cleaning strategies."""
        user_prompt = (
            f"数据文件元信息:\n{json.dumps(preview, ensure_ascii=False, indent=2)}"
        )
        from agents.utils import parse_json
        return parse_json(call_model(SYSTEM_PREVIEW, user_prompt, task="extraction"))

    def execute_with_healing(self, script_path: str, file_name: str) -> dict:
        """Run cleaning script with auto-healing on failure."""
        for attempt in range(MAX_HEAL_ITER):
            exit_code, stdout, stderr = self._execute_script(script_path)

            if exit_code == 0:
                print(f"  [P1.5] {file_name} 清洗脚本执行成功 (第 {attempt + 1} 次)")
                return {
                    "status": "success",
                    "stdout": stdout[-3000:],
                    "attempts": attempt + 1,
                }

            print(f"  [P1.5] {file_name} 第 {attempt + 1} 次执行失败，尝试修复...")
            if stderr:
                print(f"  [P1.5] stderr:\n{stderr[:800]}")

            # Ask LLM to fix the error
            code = Path(script_path).read_text(encoding="utf-8")
            fix_prompt = (
                f"以下 Python 脚本执行出错，请修复。\n\n"
                f"错误信息:\n{stderr[-2000:]}\n\n"
                f"原始代码:\n```python\n{code[:8000]}\n```\n\n"
                f"请输出修复后的完整代码，用 ```python ... ``` 包裹。"
            )
            fixed = _extract_code(
                call_model(SYSTEM_CLEANING_CODE, fix_prompt, task="codegen")
            )
            # Last-resort guard: if extraction still left a fence, strip it hard
            if fixed.lstrip().startswith("```"):
                fixed = re.sub(r"^```[^\n]*\n?", "", fixed.lstrip())
                fixed = re.sub(r"\n?```\s*$", "", fixed.rstrip())
                print("  [P1.5] 警告: 二次剥离 markdown 围栏")
            Path(script_path).write_text(fixed, encoding="utf-8")

        return {
            "status": "failed",
            "stdout": stdout[-1000:] if stdout else "",
            "stderr": stderr[-1000:] if stderr else "",
            "attempts": MAX_HEAL_ITER,
        }

    def _collect_cleaning_results(self, file_path: Path) -> dict:
        """Check what outputs were generated for a cleaned file."""
        stem = file_path.stem
        cleaned_csv = DATA_DIR / f"cleaned_{stem}.csv"
        report_json = DATA_DIR / f"cleaning_report_{stem}.json"

        result = {
            "cleaned_file": str(cleaned_csv) if cleaned_csv.exists() else None,
            "report_file": str(report_json) if report_json.exists() else None,
        }

        # Find generated figures
        figures = []
        if FIGURES_DIR.exists():
            for fig in FIGURES_DIR.glob(f"*{stem}*.*"):
                if fig.suffix.lower() in (".png", ".jpg", ".pdf"):
                    figures.append(str(fig))
        # Also collect generic EDA figures
        for pattern in ["eda_*", "distribution_*", "correlation_*", "missing_*", "boxplot_*"]:
            for fig in FIGURES_DIR.glob(pattern):
                fig_str = str(fig)
                if fig_str not in figures:
                    figures.append(fig_str)
        result["figures"] = figures

        return result

    def run(self) -> dict:
        """
        Complete P1.5 flow:
        1. Discover data files
        2. For each file: preview → analyze → generate cleaning script → execute
        3. Collect results
        4. Update context_store
        """
        ctx = load_context()
        data_files = self._discover_data_files()

        if not data_files:
            print("[P1.5] vol/data/ 中未发现数据文件，跳过数据清洗")
            print("[P1.5] 请将 .xlsx/.csv 文件放入 vol/data/ 目录")
            ctx["phase"] = "P1.5_skipped"
            save_context(ctx)
            return ctx

        print(f"[P1.5] 发现 {len(data_files)} 个数据文件:")
        for f in data_files:
            print(f"  - {f.name} ({f.stat().st_size / 1024:.1f} KB)")

        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)

        # Sync data files to container before running any scripts
        try:
            print("[P1.5] 同步数据文件到 Docker 容器...")
            self._ensure_data_in_container()
        except Exception as e:
            print(f"[P1.5] 容器同步失败 ({e})，将使用本地执行")

        all_results: dict[str, dict] = {}
        all_previews: dict[str, dict] = {}
        all_figures: list[str] = []

        for file_path in data_files:
            fname = file_path.name
            print(f"\n[P1.5] ── {fname} ──")

            # Step 1: Preview
            print(f"  [P1.5] 预览数据...")
            preview = self.preview_file(file_path)
            all_previews[fname] = preview

            if "error" in preview:
                print(f"  [P1.5] 预览失败: {preview['error']}")
                all_results[fname] = {"status": "preview_failed", "error": preview["error"]}
                continue

            shape = preview.get("shape", [0, 0])
            print(f"  [P1.5] 形状: {shape[0]} 行 × {shape[1]} 列")

            # Step 2: LLM analysis
            print(f"  [P1.5] 分析数据特征...")
            analysis = self.analyze_preview(preview)

            # Step 3: Generate cleaning script
            print(f"  [P1.5] 生成清洗脚本...")
            code = self.generate_cleaning_script(file_path, preview, analysis)
            script_path = str(SCRIPTS_DIR / f"clean_{file_path.stem}.py")
            Path(script_path).write_text(code, encoding="utf-8")

            # Step 4: Execute with healing
            print(f"  [P1.5] 执行清洗脚本...")
            exec_result = self.execute_with_healing(script_path, fname)

            # Step 5: Copy outputs from container back to host
            self._sync_outputs_from_container()

            # Step 6: Collect outputs
            cleaning_outputs = self._collect_cleaning_results(file_path)
            exec_result.update(cleaning_outputs)
            all_results[fname] = exec_result
            all_figures.extend(cleaning_outputs.get("figures", []))

            if exec_result["status"] == "success":
                cleaned = cleaning_outputs.get("cleaned_file")
                n_figs = len(cleaning_outputs.get("figures", []))
                print(f"  [P1.5] 清洗完成: {cleaned or '(未找到输出)'}")
                print(f"  [P1.5] 生成图片: {n_figs} 张")
            else:
                print(f"  [P1.5] 清洗失败 (尝试 {exec_result['attempts']} 次)")

        # ── Update context ──
        ctx["phase"] = "P1.5_complete"
        ctx.setdefault("data_cleaning", {}).update({
            "files_processed": len(data_files),
            "results": {
                fname: {
                    "status": r.get("status"),
                    "cleaned_file": r.get("cleaned_file"),
                    "figures": r.get("figures", []),
                }
                for fname, r in all_results.items()
            },
            "previews": {
                fname: {
                    "shape": p.get("shape"),
                    "columns": p.get("columns"),
                    "data_type": p.get("data_type", "unknown") if isinstance(p, dict) else "unknown",
                }
                for fname, p in all_previews.items()
            },
            "all_figures": all_figures,
        })

        # Store stdout summaries for downstream agents (modeling/coding)
        stdout_summaries = {}
        for fname, r in all_results.items():
            if r.get("status") == "success" and r.get("stdout"):
                stdout_summaries[fname] = r["stdout"][-2000:]
        ctx["data_cleaning"]["stdout_summaries"] = stdout_summaries

        save_context(ctx)

        # ── Print summary ──
        success = sum(1 for r in all_results.values() if r.get("status") == "success")
        failed = len(all_results) - success
        print(f"\n[P1.5-DONE] 清洗完成: {success} 成功, {failed} 失败")
        print(f"  总图片数: {len(all_figures)}")
        print(f"  清洗后文件在: {DATA_DIR}")
        print(f"  EDA 图片在: {FIGURES_DIR}")

        return ctx
