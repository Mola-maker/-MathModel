"""MATLAB-equivalent mathematical modeling visualization (P2.5 / post-P3).

Generates Python scripts that run in the sandbox and produce MATLAB-quality
mathematical plots using scipy + matplotlib:

  - Phase portrait + direction field  (ODE / dynamical systems)
  - 3D surface + contour projection   (optimization / multivariable models)
  - Parameter sensitivity heatmap     (any model with ≥2 parameters)
  - Time-domain solution curves       (ODE / difference equations)

Calling convention
------------------
    from agents.matlab_viz import MatlabVizAgent
    agent = MatlabVizAgent()
    result = agent.run()          # reads context automatically
    # or
    result = agent.run(ctx=ctx, model_hint="ode")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import container_name, docker_exec, host_to_container_path, vol_host

BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", str(BASE_DIR / "vol")))
SCRIPTS_DIR = VOL_HOST / "scripts"
FIGURES_DIR = VOL_HOST / "outputs" / "figures"

# ── Shared header injected into every generated script ──────────────────────

_HEADER = """\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy import integrate, linalg, optimize
import os, warnings, json
warnings.filterwarnings('ignore')

plt.rcParams.update({{
    'font.size': 11, 'axes.titlesize': 12, 'axes.titleweight': 'bold',
    'axes.labelsize': 11, 'axes.linewidth': 1.3,
    'axes.spines.top': False, 'axes.spines.right': False,
    'xtick.labelsize': 10, 'ytick.labelsize': 10,
    'legend.fontsize': 10, 'legend.frameon': False,
    'figure.dpi': 300, 'savefig.dpi': 300, 'savefig.bbox': 'tight',
}})
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

COLORS = dict(
    primary='#2E5B88', secondary='#E85D4C',
    tertiary='#4A9B7F', neutral='#7F7F7F', light='#B8D4E8'
)
FIG_DIR = {fig_dir!r}
os.makedirs(FIG_DIR, exist_ok=True)
_figs = []
"""


# ── Script generators ────────────────────────────────────────────────────────

def _gen_ode_phase_portrait(
    equations: str,
    fig_dir: str,
    x_label: str = "x",
    y_label: str = "y",
    x_range: tuple[float, float] = (-3.0, 3.0),
    y_range: tuple[float, float] = (-3.0, 3.0),
    n_traj: int = 12,
    stem: str = "ode_phase",
) -> str:
    """Phase portrait + direction field for a 2-state autonomous ODE system.

    The generated script contains a placeholder ODE that the user can swap
    for their actual model.  The LLM is asked to fill in dx_dt and dy_dt.
    """
    example_note = (
        "# 请将下面的 dx_dt / dy_dt 替换为你的实际模型方程\n"
        "# dx/dt = -x + x*y   → Lotka-Volterra 捕食者-猎物示例\n"
        "# dy/dt =  y - x*y"
    )
    fdir = repr(fig_dir)
    return textwrap.dedent(f"""\
        {_HEADER.format(fig_dir=fig_dir)}

        # ── 模型方程 (ODE 右端项) ────────────────────────────
        {example_note}
        # 从建模阶段得到的方程参考:
        # {equations[:300].replace(chr(10), chr(10)+'# ')}

        def dx_dt(x, y):
            return -x + x * y          # ← 替换为实际方程

        def dy_dt(x, y):
            return y - x * y           # ← 替换为实际方程

        def ode_system(t, state):
            x, y = state
            return [dx_dt(x, y), dy_dt(x, y)]

        # ── 方向场 ───────────────────────────────────────────
        x_range = ({x_range[0]}, {x_range[1]})
        y_range = ({y_range[0]}, {y_range[1]})
        X, Y = np.meshgrid(
            np.linspace(*x_range, 22),
            np.linspace(*y_range, 22)
        )
        DX = dx_dt(X, Y)
        DY = dy_dt(X, Y)
        speed = np.sqrt(DX**2 + DY**2)
        speed[speed == 0] = 1e-10
        DXn, DYn = DX / speed, DY / speed

        fig, ax = plt.subplots(figsize=(7, 6))
        ax.streamplot(
            np.linspace(*x_range, 22), np.linspace(*y_range, 22),
            DX, DY,
            color=np.log1p(speed), cmap='Blues',
            linewidth=0.9, arrowsize=1.2, density=1.4
        )
        ax.quiver(X[::3, ::3], Y[::3, ::3],
                  DXn[::3, ::3], DYn[::3, ::3],
                  color='#7F7F7F', alpha=0.35, scale=28, width=0.003)

        # ── 特征轨迹 ─────────────────────────────────────────
        t_span = (0, 20)
        t_eval = np.linspace(*t_span, 3000)
        cmap_traj = plt.cm.get_cmap('tab20', {n_traj})
        for _k in range({n_traj}):
            _ang = 2 * np.pi * _k / {n_traj}
            _r   = 0.8 + 1.5 * (_k % 3) / 3
            _x0  = _r * np.cos(_ang)
            _y0  = _r * np.sin(_ang)
            if not (x_range[0] < _x0 < x_range[1] and y_range[0] < _y0 < y_range[1]):
                continue
            try:
                sol = integrate.solve_ivp(
                    ode_system, t_span, [_x0, _y0], t_eval=t_eval,
                    method='RK45', rtol=1e-6, atol=1e-9, max_step=0.1
                )
                if sol.success:
                    _mask = (
                        (sol.y[0] > x_range[0]) & (sol.y[0] < x_range[1]) &
                        (sol.y[1] > y_range[0]) & (sol.y[1] < y_range[1])
                    )
                    ax.plot(sol.y[0][_mask], sol.y[1][_mask],
                            color=cmap_traj(_k), lw=1.2, alpha=0.75)
                    ax.plot(_x0, _y0, 'o', color=cmap_traj(_k), ms=4)
            except Exception:
                pass

        # ── 平衡点标注 ───────────────────────────────────────
        for _eq in [(0, 0), (1, 1)]:
            ax.plot(*_eq, '*', color=COLORS['secondary'], ms=14, zorder=5,
                    label=f'平衡点 {{_eq}}')

        ax.set_xlim(*x_range); ax.set_ylim(*y_range)
        ax.set_xlabel('{x_label}', fontsize=12)
        ax.set_ylabel('{y_label}', fontsize=12)
        ax.set_title('相平面图 (Phase Portrait)', fontsize=13)
        ax.legend(fontsize=9, loc='upper right')
        _fp = os.path.join(FIG_DIR, '{stem}.png')
        plt.savefig(_fp); plt.close(); _figs.append(_fp)
        print(f'[MATLAB-VIZ] 相图已保存: {{_fp}}')

        # ── 时域解曲线 ───────────────────────────────────────
        fig, axes = plt.subplots(2, 1, figsize=(9, 5), sharex=True)
        _ic_list = [[1.0, 0.5], [0.5, 1.5], [2.0, 1.0]]
        for _ic, _color in zip(_ic_list, [COLORS['primary'], COLORS['secondary'], COLORS['tertiary']]):
            try:
                sol = integrate.solve_ivp(
                    ode_system, (0, 25), _ic,
                    t_eval=np.linspace(0, 25, 2000),
                    method='RK45', rtol=1e-7
                )
                if sol.success:
                    axes[0].plot(sol.t, sol.y[0], color=_color, lw=1.5,
                                 label=f'IC={{_ic}}')
                    axes[1].plot(sol.t, sol.y[1], color=_color, lw=1.5)
            except Exception:
                pass
        axes[0].set_ylabel('{x_label}(t)'); axes[0].legend(fontsize=8)
        axes[1].set_ylabel('{y_label}(t)'); axes[1].set_xlabel('时间 t')
        plt.suptitle('ODE 时域解 (多初值)', fontsize=12)
        _fp2 = os.path.join(FIG_DIR, '{stem}_time.png')
        plt.tight_layout(); plt.savefig(_fp2); plt.close(); _figs.append(_fp2)
        print(f'[MATLAB-VIZ] 时域解已保存: {{_fp2}}')
        print('[MATLAB-VIZ-DONE] figs:', json.dumps(_figs))
    """)


def _gen_3d_surface(
    fig_dir: str,
    objective_expr: str = "np.sin(np.sqrt(X**2 + Y**2)) + 0.1*X",
    x_label: str = "x₁",
    y_label: str = "x₂",
    x_range: tuple[float, float] = (-4.0, 4.0),
    y_range: tuple[float, float] = (-4.0, 4.0),
    stem: str = "surface_3d",
) -> str:
    """3D surface + contour projection + gradient field."""
    return textwrap.dedent(f"""\
        {_HEADER.format(fig_dir=fig_dir)}
        from mpl_toolkits.mplot3d import Axes3D   # noqa

        # ── 目标函数 ─────────────────────────────────────────
        # 请替换为实际的目标函数或约束曲面
        X_grid = np.linspace({x_range[0]}, {x_range[1]}, 120)
        Y_grid = np.linspace({y_range[0]}, {y_range[1]}, 120)
        X, Y = np.meshgrid(X_grid, Y_grid)
        Z = {objective_expr}

        # ── 图1: 3D 曲面 + 等高线投影 ────────────────────────
        fig = plt.figure(figsize=(13, 5))
        ax1 = fig.add_subplot(121, projection='3d')
        surf = ax1.plot_surface(X, Y, Z, cmap='viridis', alpha=0.85,
                                linewidth=0, antialiased=True)
        ax1.contourf(X, Y, Z, zdir='z',
                     offset=Z.min() - 0.5*(Z.max()-Z.min()),
                     cmap='viridis', alpha=0.4, levels=15)
        ax1.set_xlabel('{x_label}'); ax1.set_ylabel('{y_label}')
        ax1.set_zlabel('f(x,y)')
        ax1.set_title('目标函数 3D 曲面')
        fig.colorbar(surf, ax=ax1, shrink=0.5, pad=0.12)

        # ── 图2: 等高线图 + 梯度场 ──────────────────────────
        ax2 = fig.add_subplot(122)
        cs = ax2.contourf(X, Y, Z, levels=20, cmap='viridis', alpha=0.8)
        ax2.contour(X, Y, Z, levels=20, colors='white', linewidths=0.4, alpha=0.6)
        plt.colorbar(cs, ax=ax2)
        # 梯度向量场
        _step = 8
        DX = np.gradient(Z, axis=1)[::_step, ::_step]
        DY = np.gradient(Z, axis=0)[::_step, ::_step]
        ax2.quiver(
            X[::_step, ::_step], Y[::_step, ::_step],
            -DX, -DY,
            color='white', alpha=0.55, scale=None, width=0.004
        )
        # 标注最小值
        _imin = np.unravel_index(np.argmin(Z), Z.shape)
        ax2.plot(X[_imin], Y[_imin], '*', color=COLORS['secondary'],
                 ms=14, label=f'最优 ({{X[_imin]:.2f}}, {{Y[_imin]:.2f}})', zorder=5)
        ax2.set_xlabel('{x_label}'); ax2.set_ylabel('{y_label}')
        ax2.set_title('等高线图 + 负梯度方向')
        ax2.legend(fontsize=9)
        plt.suptitle('优化目标函数可视化', fontsize=13, y=1.01)
        _fp = os.path.join(FIG_DIR, '{stem}.png')
        plt.tight_layout(); plt.savefig(_fp); plt.close(); _figs.append(_fp)
        print(f'[MATLAB-VIZ] 3D曲面已保存: {{_fp}}')
        print('[MATLAB-VIZ-DONE] figs:', json.dumps(_figs))
    """)


def _gen_sensitivity(
    fig_dir: str,
    model_summary: str = "",
    param1_name: str = "α",
    param2_name: str = "β",
    stem: str = "sensitivity",
) -> str:
    """2D parameter sensitivity heatmap + 1D sensitivity scan."""
    return textwrap.dedent(f"""\
        {_HEADER.format(fig_dir=fig_dir)}

        # ── 参数敏感性分析 ───────────────────────────────────
        # 模型摘要: {model_summary[:200]}
        # 替换下面的 model_output() 为你的模型计算函数

        def model_output(a, b):
            \"\"\"示例模型输出: 请替换为实际建模函数.\"\"\"
            # 示例: 种群增长模型 dx/dt = a*x*(1-x/b)
            # 稳定种群密度 x* = b, 增长率 a
            from scipy.integrate import solve_ivp
            try:
                sol = solve_ivp(
                    lambda t, x: [a * x[0] * (1 - x[0] / max(b, 0.1))],
                    (0, 30), [0.1], t_eval=np.linspace(0, 30, 200),
                    rtol=1e-6
                )
                return float(sol.y[0, -1]) if sol.success else 0.0
            except Exception:
                return 0.0

        n_grid = 25
        a_vals = np.linspace(0.1, 3.0, n_grid)
        b_vals = np.linspace(0.5, 5.0, n_grid)
        Z_grid = np.zeros((n_grid, n_grid))
        for _i, _a in enumerate(a_vals):
            for _j, _b in enumerate(b_vals):
                Z_grid[_i, _j] = model_output(_a, _b)
        print('[MATLAB-VIZ] 敏感性网格计算完成')

        fig, axes = plt.subplots(1, 2, figsize=(13, 5))

        # 热力图
        im = axes[0].imshow(
            Z_grid, origin='lower', aspect='auto', cmap='RdYlBu_r',
            extent=[b_vals[0], b_vals[-1], a_vals[0], a_vals[-1]]
        )
        plt.colorbar(im, ax=axes[0], label='模型输出')
        _lvls = np.linspace(Z_grid.min(), Z_grid.max(), 12)
        axes[0].contour(
            np.linspace(b_vals[0], b_vals[-1], n_grid),
            np.linspace(a_vals[0], a_vals[-1], n_grid),
            Z_grid, levels=_lvls, colors='white', linewidths=0.5, alpha=0.6
        )
        axes[0].set_xlabel('{param2_name}'); axes[0].set_ylabel('{param1_name}')
        axes[0].set_title('参数敏感性热力图')

        # 1D 扫描 (固定另一参数在中间值)
        _mid_b = b_vals[n_grid // 2]
        _mid_a = a_vals[n_grid // 2]
        _y_a = [model_output(_a, _mid_b) for _a in a_vals]
        _y_b = [model_output(_mid_a, _b) for _b in b_vals]
        axes[1].plot(a_vals, _y_a, color=COLORS['primary'], lw=2,
                     label=f'{param1_name} 扫描 ({param2_name}={{_mid_b:.2f}}固定)')
        axes[1].plot(b_vals, _y_b, color=COLORS['secondary'], lw=2, ls='--',
                     label=f'{param2_name} 扫描 ({param1_name}={{_mid_a:.2f}}固定)')
        axes[1].set_xlabel('参数值'); axes[1].set_ylabel('模型输出')
        axes[1].set_title('单参数敏感性扫描')
        axes[1].legend()
        plt.suptitle('参数敏感性分析', fontsize=13, y=1.01)
        _fp = os.path.join(FIG_DIR, '{stem}.png')
        plt.tight_layout(); plt.savefig(_fp); plt.close(); _figs.append(_fp)
        print(f'[MATLAB-VIZ] 参数敏感性图已保存: {{_fp}}')
        print('[MATLAB-VIZ-DONE] figs:', json.dumps(_figs))
    """)


def _gen_eigenvalue_stability(
    fig_dir: str,
    stem: str = "stability",
) -> str:
    """Eigenvalue spectrum + Jacobian stability analysis."""
    return textwrap.dedent(f"""\
        {_HEADER.format(fig_dir=fig_dir)}

        # ── 雅可比矩阵稳定性分析 ─────────────────────────────
        # 请替换为实际模型在平衡点处的雅可比矩阵
        # 示例: SIR 模型在无病平衡点 E0=(1,0,0) 处的雅可比

        def jacobian_at_equilibrium(beta=0.5, gamma=0.2, mu=0.02):
            \"\"\"SIR 示例雅可比，请替换为实际模型.\"\"\"
            return np.array([
                [-(beta + mu),  0,          0   ],
                [ beta,        -(gamma+mu), 0   ],
                [ 0,            gamma,      -mu ],
            ])

        param_sweep = np.linspace(0.1, 1.5, 40)
        all_eigs = []
        for _beta in param_sweep:
            _J = jacobian_at_equilibrium(beta=_beta)
            _eigs = linalg.eigvals(_J)
            all_eigs.append(_eigs)

        fig, axes = plt.subplots(1, 2, figsize=(13, 5))

        # 特征值轨迹 (参数变化时)
        for _k in range(all_eigs[0].shape[0]):
            _re = [e[_k].real for e in all_eigs]
            _im = [e[_k].imag for e in all_eigs]
            axes[0].plot(param_sweep, _re, lw=2, label=f'λ₀{{_k+1}} (实部)')
        axes[0].axhline(0, color='black', lw=1.2, ls='--', alpha=0.6)
        axes[0].fill_between(param_sweep,
                             axes[0].get_ylim()[0] if axes[0].get_ylim()[0] < 0 else -1,
                             0, alpha=0.08, color=COLORS['tertiary'], label='稳定区')
        axes[0].set_xlabel('参数 β'); axes[0].set_ylabel('特征值实部')
        axes[0].set_title('特征值实部随参数变化')
        axes[0].legend(fontsize=9)

        # 复平面特征值图
        _J_mid = jacobian_at_equilibrium(beta=param_sweep[len(param_sweep)//2])
        _eigs_mid = linalg.eigvals(_J_mid)
        axes[1].axhline(0, color='black', lw=1); axes[1].axvline(0, color='black', lw=1)
        axes[1].scatter(_eigs_mid.real, _eigs_mid.imag,
                        s=120, color=COLORS['secondary'], zorder=5)
        for _e in _eigs_mid:
            axes[1].annotate(f'({{_e.real:.3f}}, {{_e.imag:.3f}}i)',
                             (_e.real, _e.imag), textcoords='offset points',
                             xytext=(8, 5), fontsize=9)
        axes[1].set_xlabel('实部 Re(λ)'); axes[1].set_ylabel('虚部 Im(λ)')
        axes[1].set_title('复平面特征值 (稳定性判别)')
        _stable = all(_e.real < 0 for _e in _eigs_mid)
        axes[1].set_title(
            f'复平面特征值 — {{"渐近稳定" if _stable else "不稳定"}} ✓' if _stable
            else f'复平面特征值 — 不稳定 ⚠'
        )
        plt.suptitle('线性稳定性分析 (Jacobian 谱)', fontsize=13, y=1.01)
        _fp = os.path.join(FIG_DIR, '{stem}.png')
        plt.tight_layout(); plt.savefig(_fp); plt.close(); _figs.append(_fp)
        print(f'[MATLAB-VIZ] 稳定性图已保存: {{_fp}}')
        print('[MATLAB-VIZ-DONE] figs:', json.dumps(_figs))
    """)


# ── Execution helper ─────────────────────────────────────────────────────────

def _run_script(script_path: str) -> tuple[int, str, str]:
    """Run script in sandbox (docker exec) with local fallback."""
    container_path = host_to_container_path(script_path)
    try:
        exit_code, stdout, stderr = docker_exec(
            container_name(), f"python3 {container_path}", timeout=300
        )
        return exit_code, stdout, stderr
    except Exception as exc:
        print(f"  [matlab_viz] docker 不可用 ({exc})，使用本地执行")
        result = subprocess.run(
            ["python", script_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300
        )
        return result.returncode, result.stdout, result.stderr


# ── LLM-based equation extraction ────────────────────────────────────────────

_EXTRACT_SYS = """\
从建模结果中提取数学方程的关键参数，输出严格 JSON，不含 markdown:
{
  "model_type": "ode|optimization|regression|network|other",
  "state_vars": ["x", "y"],
  "params": {"a": 1.0, "b": 0.5},
  "equations_text": "dx/dt = ax(1-x/K)",
  "equilibria": [[0,0],[1,1]],
  "x_range": [-3.0, 3.0],
  "y_range": [-3.0, 3.0],
  "objective_expr": "np.sin(np.sqrt(X**2+Y**2))",
  "param1_name": "α",
  "param2_name": "β"
}
"""


def _extract_model_info(ctx: dict) -> dict:
    """Use LLM to extract structured model info from context."""
    modeling = ctx.get("modeling", {})
    equations = modeling.get("equations_latex", "")
    model_type = modeling.get("model_type", "")
    problem_summary = ctx.get("competition", {}).get("problem_text", "")[:500]

    user_prompt = (
        f"模型类型: {model_type}\n"
        f"方程 (LaTeX): {equations[:800]}\n"
        f"题目摘要: {problem_summary}\n\n"
        "请提取建模参数，输出 JSON："
    )
    try:
        from agents.utils import parse_json
        raw = call_model(_EXTRACT_SYS, user_prompt, task="extraction")
        return parse_json(raw)
    except Exception as exc:
        print(f"  [matlab_viz] 参数提取失败: {exc}")
        return {}


# ── Main agent class ──────────────────────────────────────────────────────────

class MatlabVizAgent:
    """P2.5: 生成 MATLAB 等效数学建模可视化图表。"""

    def run(self, ctx: dict | None = None, model_hint: str = "") -> dict:
        """
        生成并运行数学可视化脚本。

        Parameters
        ----------
        ctx       : context dict (如果为 None 则自动加载)
        model_hint: "ode" | "optimization" | "sensitivity" | "stability" | "all"
                    留空则根据 context 自动判断

        Returns
        -------
        dict with keys: figures, scripts_run, status
        """
        if ctx is None:
            ctx = load_context()

        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)
        fig_dir = host_to_container_path(str(FIGURES_DIR))

        # ── Extract model information via LLM ──────────────────────────
        print("[matlab_viz] 提取模型参数...")
        info = _extract_model_info(ctx)
        raw_model_type = (
            info.get("model_type")
            or ctx.get("modeling", {}).get("model_type", "")
            or model_hint
        ).lower()

        equations_text = info.get("equations_text", "") or ctx.get("modeling", {}).get(
            "equations_latex", ""
        )

        # Determine which visualizations to generate
        do_ode = any(k in raw_model_type for k in ("ode", "微分", "动力", "sir", "lotka"))
        do_opt = any(k in raw_model_type for k in ("optim", "规划", "最优", "linear", "nonlin"))
        do_stability = do_ode  # stability goes with ODE
        # Always generate sensitivity (universal)

        if model_hint == "all":
            do_ode = do_opt = do_stability = True
        elif model_hint:
            do_ode = "ode" in model_hint
            do_opt = "optim" in model_hint or "surface" in model_hint
            do_stability = "stab" in model_hint

        # Default: at minimum do sensitivity
        if not (do_ode or do_opt):
            print("[matlab_viz] 未识别模型类型，默认生成参数敏感性图")

        # ── Build parameter defaults from LLM extraction ──────────────
        x_range = tuple(info.get("x_range", (-3.0, 3.0)))
        y_range = tuple(info.get("y_range", (-3.0, 3.0)))
        state_vars = info.get("state_vars", ["x", "y"])
        x_label = state_vars[0] if state_vars else "x"
        y_label = state_vars[1] if len(state_vars) > 1 else "y"
        objective_expr = info.get("objective_expr", "np.sin(np.sqrt(X**2+Y**2)) + 0.1*X")
        param1 = info.get("param1_name", "α")
        param2 = info.get("param2_name", "β")
        model_summary = equations_text[:200]

        scripts_to_run: list[tuple[str, str]] = []  # (label, script_code)

        if do_ode:
            scripts_to_run.append((
                "ode_phase",
                _gen_ode_phase_portrait(
                    equations_text, fig_dir,
                    x_label=x_label, y_label=y_label,
                    x_range=x_range, y_range=y_range,
                )
            ))
        if do_opt:
            scripts_to_run.append((
                "surface_3d",
                _gen_3d_surface(fig_dir, objective_expr=objective_expr)
            ))
        if do_stability:
            scripts_to_run.append((
                "stability",
                _gen_eigenvalue_stability(fig_dir)
            ))

        # Always: sensitivity
        scripts_to_run.append((
            "sensitivity",
            _gen_sensitivity(fig_dir, model_summary, param1, param2)
        ))

        # ── Execute scripts ────────────────────────────────────────────
        all_figures: list[str] = []
        scripts_run: list[str] = []

        for label, code in scripts_to_run:
            script_path = str(SCRIPTS_DIR / f"matlab_viz_{label}.py")
            Path(script_path).write_text(code, encoding="utf-8")

            print(f"  [matlab_viz] 运行 {label}...")
            exit_code, stdout, stderr = _run_script(script_path)

            if exit_code != 0:
                print(f"  [matlab_viz] {label} 失败 (exit={exit_code}):\n{stderr[:400]}")
                # Try to auto-fix once via LLM
                fixed = self._heal(script_path, stderr)
                if fixed:
                    Path(script_path).write_text(fixed, encoding="utf-8")
                    exit_code, stdout, stderr = _run_script(script_path)

            scripts_run.append(script_path)

            # Parse figure paths from stdout
            for line in stdout.splitlines():
                if "[MATLAB-VIZ-DONE]" in line:
                    try:
                        figs = json.loads(line.split("figs:", 1)[1].strip())
                        all_figures.extend(figs)
                    except Exception:
                        pass
                elif "[MATLAB-VIZ]" in line and "已保存" in line:
                    m = re.search(r": (.+\.png)", line)
                    if m:
                        fp = m.group(1).strip()
                        if fp not in all_figures:
                            all_figures.append(fp)

        # ── Update context ─────────────────────────────────────────────
        ctx.setdefault("matlab_viz", {}).update({
            "figures": all_figures,
            "scripts": scripts_run,
            "model_type_detected": raw_model_type,
        })
        save_context(ctx)

        n = len(all_figures)
        print(f"\n[matlab_viz-DONE] 生成 {n} 张数学可视化图片")
        for fp in all_figures:
            print(f"  {fp}")

        return {"figures": all_figures, "scripts_run": scripts_run, "status": "done"}

    def _heal(self, script_path: str, stderr: str) -> str | None:
        """Ask LLM to fix a broken viz script (one attempt)."""
        code = Path(script_path).read_text(encoding="utf-8")
        fix_prompt = (
            f"以下 Python 可视化脚本运行出错，请修复。\n\n"
            f"错误:\n{stderr[-1500:]}\n\n"
            f"代码:\n```python\n{code[:6000]}\n```\n\n"
            "只修改出错部分，输出完整修复后的代码，用 ```python ... ``` 包裹。"
        )
        try:
            raw = call_model(
                "你是 Python matplotlib/scipy 专家，修复可视化脚本错误。",
                fix_prompt,
                task="codegen"
            )
            # Strip markdown fence
            lines = raw.strip().splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            fixed = "\n".join(lines).strip()
            return fixed if len(fixed) > 50 else None
        except Exception:
            return None


# ── CLI entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MATLAB-style mathematical visualization")
    parser.add_argument("--hint", default="",
                        choices=["ode", "optimization", "sensitivity", "stability", "all", ""],
                        help="Force a specific visualization type")
    args = parser.parse_args()
    agent = MatlabVizAgent()
    result = agent.run(model_hint=args.hint)
    print(json.dumps(result, ensure_ascii=False, indent=2))
