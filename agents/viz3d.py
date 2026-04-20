"""P2.7 — 3D scientific visualization (PyVista + Plotly).

Generates three families of 3D assets for the paper:
  - PyVista mesh/surface renders (PNG, 300 dpi) — publication figures
  - Plotly interactive HTML — embeddable, reviewer-friendly
  - Optional .m (Octave) counterpart — for teams that prefer MATLAB output

Design: one agent, LLM chooses figure spec from context, scripts run in
sandbox. Inspired by PyVista's MIT-licensed plotting examples.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path

from agents.orchestrator import call_model, load_context, save_context
from agents.utils import container_name, docker_exec, host_to_container_path, parse_json
from agents.octave_runner import run_m_inline

BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", BASE_DIR / "vol"))
SCRIPTS_DIR = VOL_HOST / "scripts"
FIGURES_DIR = VOL_HOST / "outputs" / "figures"
HTML_DIR = VOL_HOST / "outputs" / "interactive"


_HEADER = """\
import os, json, numpy as np
os.environ.setdefault('PYVISTA_OFF_SCREEN', 'true')
import pyvista as pv
pv.OFF_SCREEN = True
try:
    pv.start_xvfb()
except Exception:
    pass
import plotly.graph_objects as go
FIG_DIR = {fig_dir!r}
HTML_DIR = {html_dir!r}
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(HTML_DIR, exist_ok=True)
_artifacts = {{'png': [], 'html': [], 'mesh': []}}
"""


def _gen_surface_script(expr: str, stem: str, fig_dir: str, html_dir: str,
                        x_range: tuple[float, float] = (-4.0, 4.0),
                        y_range: tuple[float, float] = (-4.0, 4.0)) -> str:
    """3D surface (PyVista PNG + Plotly HTML) from f(x,y)=expr."""
    return textwrap.dedent(f"""\
        {_HEADER.format(fig_dir=fig_dir, html_dir=html_dir)}

        xs = np.linspace({x_range[0]}, {x_range[1]}, 140)
        ys = np.linspace({y_range[0]}, {y_range[1]}, 140)
        X, Y = np.meshgrid(xs, ys)
        Z = {expr}

        # PyVista surface render
        grid = pv.StructuredGrid(X, Y, Z)
        grid['height'] = Z.ravel(order='F')
        p = pv.Plotter(off_screen=True, window_size=[1400, 900])
        p.add_mesh(grid, scalars='height', cmap='viridis',
                   show_edges=False, smooth_shading=True,
                   scalar_bar_args={{'title': 'f(x,y)'}})
        p.add_axes(interactive=False)
        p.camera_position = 'iso'
        p.camera.zoom(1.05)
        png_path = os.path.join(FIG_DIR, '{stem}_pv.png')
        p.screenshot(png_path)
        _artifacts['png'].append(png_path)
        print(f'[VIZ3D] pyvista surface -> {{png_path}}')

        mesh_path = os.path.join(FIG_DIR, '{stem}.vtk')
        grid.save(mesh_path)
        _artifacts['mesh'].append(mesh_path)

        # Plotly interactive HTML
        fig = go.Figure(go.Surface(x=xs, y=ys, z=Z, colorscale='Viridis',
                                   contours={{'z': {{'show': True, 'usecolormap': True,
                                                    'project': {{'z': True}}}}}}))
        fig.update_layout(title='3D Surface: f(x,y)',
                          scene=dict(xaxis_title='x', yaxis_title='y', zaxis_title='f'),
                          margin=dict(l=0, r=0, t=40, b=0))
        html_path = os.path.join(HTML_DIR, '{stem}.html')
        fig.write_html(html_path, include_plotlyjs='cdn')
        _artifacts['html'].append(html_path)
        print(f'[VIZ3D] plotly surface  -> {{html_path}}')

        print('[VIZ3D-DONE]', json.dumps(_artifacts))
    """)


def _gen_trajectory_script(stem: str, fig_dir: str, html_dir: str) -> str:
    """3D phase-space trajectory (Lorenz-like default) + PyVista + Plotly."""
    return textwrap.dedent(f"""\
        {_HEADER.format(fig_dir=fig_dir, html_dir=html_dir)}
        from scipy.integrate import solve_ivp

        # Default: Lorenz attractor — replace rhs() with model's 3D system
        def rhs(t, s, sigma=10.0, rho=28.0, beta=8/3):
            x, y, z = s
            return [sigma*(y-x), x*(rho-z)-y, x*y-beta*z]

        sol = solve_ivp(rhs, (0, 40), [1.0, 1.0, 1.0],
                        t_eval=np.linspace(0, 40, 8000),
                        rtol=1e-8, atol=1e-10)
        pts = np.column_stack(sol.y)

        # PyVista polyline
        spline = pv.Spline(pts, 2000)
        spline['t'] = np.linspace(0, 1, spline.n_points)
        p = pv.Plotter(off_screen=True, window_size=[1400, 900])
        p.add_mesh(spline, scalars='t', cmap='plasma', line_width=2,
                   scalar_bar_args={{'title': 'time'}})
        p.add_axes(interactive=False)
        p.camera_position = 'iso'
        png_path = os.path.join(FIG_DIR, '{stem}_pv.png')
        p.screenshot(png_path)
        _artifacts['png'].append(png_path)
        print(f'[VIZ3D] pyvista trajectory -> {{png_path}}')

        fig = go.Figure(go.Scatter3d(
            x=sol.y[0], y=sol.y[1], z=sol.y[2], mode='lines',
            line=dict(color=np.linspace(0,1,sol.t.size), colorscale='Plasma', width=3)))
        fig.update_layout(title='3D Phase Trajectory',
                          scene=dict(xaxis_title='x', yaxis_title='y', zaxis_title='z'),
                          margin=dict(l=0, r=0, t=40, b=0))
        html_path = os.path.join(HTML_DIR, '{stem}.html')
        fig.write_html(html_path, include_plotlyjs='cdn')
        _artifacts['html'].append(html_path)
        print(f'[VIZ3D] plotly trajectory  -> {{html_path}}')
        print('[VIZ3D-DONE]', json.dumps(_artifacts))
    """)


def _gen_volume_script(stem: str, fig_dir: str, html_dir: str) -> str:
    """3D scalar field volume render (useful for diffusion/PDE solutions)."""
    return textwrap.dedent(f"""\
        {_HEADER.format(fig_dir=fig_dir, html_dir=html_dir)}

        # Default: Gaussian blob — replace with actual PDE solution field
        n = 60
        ax = np.linspace(-3, 3, n)
        X, Y, Z = np.meshgrid(ax, ax, ax, indexing='ij')
        V = np.exp(-(X**2 + Y**2 + Z**2)) \\
            + 0.6*np.exp(-((X-1.2)**2 + Y**2 + (Z-0.8)**2)/0.4)

        grid = pv.ImageData(dimensions=V.shape,
                            spacing=(ax[1]-ax[0],)*3, origin=(ax[0],)*3)
        grid['field'] = V.flatten(order='F')

        p = pv.Plotter(off_screen=True, window_size=[1400, 900])
        p.add_volume(grid, scalars='field', cmap='inferno', opacity='sigmoid')
        p.add_axes(interactive=False)
        p.camera_position = 'iso'
        png_path = os.path.join(FIG_DIR, '{stem}_pv.png')
        p.screenshot(png_path)
        _artifacts['png'].append(png_path)
        print(f'[VIZ3D] pyvista volume -> {{png_path}}')

        iso = grid.contour(isosurfaces=[0.3, 0.6, 0.85], scalars='field')
        p2 = pv.Plotter(off_screen=True, window_size=[1400, 900])
        p2.add_mesh(iso, cmap='inferno', opacity=0.6, show_scalar_bar=True)
        p2.add_axes(interactive=False)
        iso_path = os.path.join(FIG_DIR, '{stem}_iso.png')
        p2.screenshot(iso_path)
        _artifacts['png'].append(iso_path)
        print(f'[VIZ3D] pyvista isosurfaces -> {{iso_path}}')

        fig = go.Figure(go.Volume(x=X.flatten(), y=Y.flatten(), z=Z.flatten(),
                                  value=V.flatten(), opacity=0.1, surface_count=20,
                                  colorscale='Inferno'))
        fig.update_layout(title='3D Scalar Field',
                          scene=dict(xaxis_title='x', yaxis_title='y', zaxis_title='z'),
                          margin=dict(l=0, r=0, t=40, b=0))
        html_path = os.path.join(HTML_DIR, '{stem}.html')
        fig.write_html(html_path, include_plotlyjs='cdn')
        _artifacts['html'].append(html_path)
        print(f'[VIZ3D] plotly volume   -> {{html_path}}')
        print('[VIZ3D-DONE]', json.dumps(_artifacts))
    """)


_OCTAVE_SURFACE_TEMPLATE = """\
[X, Y] = meshgrid(linspace({x0}, {x1}, 120), linspace({y0}, {y1}, 120));
Z = {expr};
figure('position', [0 0 1200 800]);
surf(X, Y, Z, 'EdgeColor', 'none');
colormap('viridis'); colorbar;
shading interp; lighting gouraud;
xlabel('x'); ylabel('y'); zlabel('f(x,y)');
title('MATLAB/Octave Surface');
view(45, 30);
print('-dpng', '-r200', '{out_path}');
fprintf('[OCTAVE-VIZ] surface -> %s\\n', '{out_path}');
"""


def _octave_surface_script(expr_matlab: str, out_path: str,
                            x_range: tuple[float, float], y_range: tuple[float, float]) -> str:
    return _OCTAVE_SURFACE_TEMPLATE.format(
        x0=x_range[0], x1=x_range[1], y0=y_range[0], y1=y_range[1],
        expr=expr_matlab, out_path=out_path,
    )


_EXTRACT_SYS = """\
从建模结果中判断是否需要 3D 可视化，并抽取参数。严格输出 JSON：
{
  "needs_3d": true,
  "kinds": ["surface","trajectory","volume"],
  "objective_expr_python": "np.sin(np.sqrt(X**2+Y**2))",
  "objective_expr_matlab": "sin(sqrt(X.^2+Y.^2))",
  "x_range": [-4.0, 4.0],
  "y_range": [-4.0, 4.0]
}
kinds 可从 [surface, trajectory, volume] 里选 1-3 个最相关的。
若问题是 3D 物理场/PDE/扩散，加 volume；有三维动力系统或时序轨迹，加 trajectory；
有 2 变量目标函数/势能面/效用面，加 surface。
"""


def _detect_3d_spec(ctx: dict) -> dict:
    modeling = ctx.get("modeling", {})
    summary = (
        f"类型: {modeling.get('model_type', '')}\n"
        f"方程(截断): {str(modeling.get('equations_latex', ''))[:600]}\n"
        f"题目: {str(ctx.get('competition', {}).get('problem_text', ''))[:400]}\n"
    )
    try:
        raw = call_model(_EXTRACT_SYS, summary + "\n请输出 JSON：", task="extraction")
        return parse_json(raw)
    except Exception as exc:
        print(f"  [viz3d] 参数抽取失败: {exc}")
        return {}


def _run_py(script_path: Path) -> tuple[int, str, str]:
    container_path = host_to_container_path(str(script_path))
    return docker_exec(container_name(), f"python3 {container_path}", timeout=420)


class Viz3DAgent:
    """P2.7 — 3D scientific visualization (PyVista + Plotly + optional Octave)."""

    def run(self, ctx: dict | None = None, force_all: bool = False,
            with_matlab: bool = True) -> dict:
        if ctx is None:
            ctx = load_context()

        SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)
        HTML_DIR.mkdir(parents=True, exist_ok=True)

        fig_dir = host_to_container_path(str(FIGURES_DIR))
        html_dir = host_to_container_path(str(HTML_DIR))

        print("[viz3d] 检测 3D 需求...")
        spec = _detect_3d_spec(ctx)
        kinds = spec.get("kinds") or []
        needs = bool(spec.get("needs_3d")) or force_all
        if force_all:
            kinds = ["surface", "trajectory", "volume"]
        if not kinds:
            kinds = ["surface"]

        if not needs:
            print("[viz3d-SKIP] 当前模型不需要 3D 可视化")
            ctx.setdefault("viz3d", {}).update({"status": "skipped", "kinds": []})
            save_context(ctx)
            return {"status": "skipped", "figures": [], "html": []}

        expr_py = spec.get("objective_expr_python") or "np.sin(np.sqrt(X**2+Y**2))+0.1*X"
        expr_ml = spec.get("objective_expr_matlab") or "sin(sqrt(X.^2+Y.^2))+0.1.*X"
        x_range = tuple(spec.get("x_range", (-4.0, 4.0)))
        y_range = tuple(spec.get("y_range", (-4.0, 4.0)))

        scripts: list[tuple[str, str]] = []
        if "surface" in kinds:
            scripts.append(("surface_3d",
                            _gen_surface_script(expr_py, "surface_3d", fig_dir, html_dir,
                                                 x_range=x_range, y_range=y_range)))
        if "trajectory" in kinds:
            scripts.append(("trajectory_3d",
                            _gen_trajectory_script("trajectory_3d", fig_dir, html_dir)))
        if "volume" in kinds:
            scripts.append(("volume_3d",
                            _gen_volume_script("volume_3d", fig_dir, html_dir)))

        all_png: list[str] = []
        all_html: list[str] = []
        all_mesh: list[str] = []

        for stem, code in scripts:
            sp = SCRIPTS_DIR / f"viz3d_{stem}.py"
            sp.write_text(code, encoding="utf-8")
            print(f"  [viz3d] 运行 {stem} ...")
            code_exit, out, err = _run_py(sp)
            if code_exit != 0:
                print(f"  [viz3d] {stem} 失败 (exit={code_exit}):\n{err[-400:]}")
                continue
            for line in out.splitlines():
                if "[VIZ3D-DONE]" in line:
                    try:
                        payload = json.loads(line.split("]", 1)[1].strip())
                        all_png.extend(payload.get("png", []))
                        all_html.extend(payload.get("html", []))
                        all_mesh.extend(payload.get("mesh", []))
                    except Exception:
                        pass

        matlab_figs: list[str] = []
        if with_matlab and "surface" in kinds:
            try:
                out_png = f"{fig_dir}/surface_3d_octave.png"
                body = _octave_surface_script(expr_ml, out_png, x_range, y_range)
                exit_code, o, e = run_m_inline(body, name="viz3d_surface_octave", timeout=240)
                if exit_code == 0:
                    host_png = str(FIGURES_DIR / "surface_3d_octave.png")
                    matlab_figs.append(host_png)
                    print(f"  [viz3d] octave surface -> {host_png}")
                else:
                    print(f"  [viz3d] octave surface 失败: {e[-300:]}")
            except Exception as exc:
                print(f"  [viz3d] octave 调用异常: {exc}")

        figures = all_png + matlab_figs
        ctx.setdefault("viz3d", {}).update({
            "status": "done",
            "kinds": kinds,
            "figures": figures,
            "html": all_html,
            "mesh_files": all_mesh,
            "matlab_figures": matlab_figs,
        })
        save_context(ctx)

        print(f"\n[viz3d-DONE] png={len(figures)} html={len(all_html)} mesh={len(all_mesh)}")
        return {
            "status": "done",
            "figures": figures,
            "html": all_html,
            "mesh_files": all_mesh,
        }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="3D scientific visualization")
    parser.add_argument("--all", action="store_true", help="强制生成全部 3 种类型")
    parser.add_argument("--no-matlab", action="store_true", help="跳过 Octave 输出")
    args = parser.parse_args()
    agent = Viz3DAgent()
    r = agent.run(force_all=args.all, with_matlab=not args.no_matlab)
    print(json.dumps(r, ensure_ascii=False, indent=2))
