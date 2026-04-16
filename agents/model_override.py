"""Model override — 持久化每任务或全局的模型覆盖。

覆盖文件：config/model_override.json
格式：
{
  "global": "or:anthropic/claude-opus-4-6" | null,
  "per_task": {
    "modeling": "or:anthropic/claude-sonnet-4-6",
    "codegen":  "ds:deepseek-chat"
  }
}

call_model() 的覆盖优先级：
  1. 显式 model= 参数（代码里直接传）
  2. per_task[task] 覆盖
  3. global 覆盖
  4. TOML model_routes.toml 正常路由
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_CFG_DIR = Path(__file__).parent.parent / "config"
_OVERRIDE_FILE = _CFG_DIR / "model_override.json"

# 任务名 → 可读描述
TASK_LABELS: dict[str, str] = {
    "modeling":   "P2 数学建模（Opus）",
    "codegen":    "P3 代码生成（Sonnet）",
    "writing":    "P4 论文撰写（Sonnet）",
    "extraction": "经验/知识提炼（DeepSeek）",
    "review":     "P5 审校评分（DeepSeek）",
    "healing":    "代码自愈（DeepSeek）",
    "default":    "其余所有任务",
}


# persistence

def _load() -> dict:
    if not _OVERRIDE_FILE.exists():
        return {"global": None, "per_task": {}}
    try:
        return json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"global": None, "per_task": {}}


def _save(data: dict) -> None:
    _CFG_DIR.mkdir(parents=True, exist_ok=True)
    _OVERRIDE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ─────────────────────────────────────── public API ──

def get_override(task: str | None) -> str | None:
    """返回指定 task 的有效覆盖模型，无则返回 None。"""
    data = _load()
    if task:
        per = data.get("per_task", {})
        if isinstance(per, dict) and task in per and per[task]:
            return per[task]
    return data.get("global") or None


def set_override(task: str | None, model: str) -> None:
    """持久化覆盖。task=None 表示全局覆盖。"""
    data = _load()
    if task:
        data.setdefault("per_task", {})[task] = model
    else:
        data["global"] = model
    _save(data)
    _print_active(data)


def clear_override(task: str | None = None) -> None:
    """清除覆盖。task=None 清全局；task='all' 清一切。"""
    if task == "all":
        _save({"global": None, "per_task": {}})
        print("  ✓ 已清除所有模型覆盖，恢复 TOML 路由")
        return
    data = _load()
    if task:
        removed = data.get("per_task", {}).pop(task, None)
        verb = f"[{task}]" if removed else f"[{task}]（原本未设置）"
    else:
        removed = data.get("global")
        data["global"] = None
        verb = "[global]" if removed else "[global]（原本未设置）"
    _save(data)
    print(f"  ✓ 已清除覆盖 {verb}")


def list_overrides() -> dict:
    return _load()


def _print_active(data: dict) -> None:
    g = data.get("global")
    per = {k: v for k, v in (data.get("per_task") or {}).items() if v}
    if not g and not per:
        print("  （当前无任何覆盖，全部走 TOML 路由）")
        return
    if g:
        print(f"  全局覆盖 → {g}")
    for t, m in per.items():
        label = TASK_LABELS.get(t, t)
        print(f"  [{t}] {label} → {m}")


# ─────────────────────────────────────── TOML reader ──

def _read_toml_models() -> dict[str, list[str]]:
    """从 model_routes.toml 读取每个 task 的模型列表。"""
    toml_path = _CFG_DIR / "model_routes.toml"
    if not toml_path.exists():
        return {}
    try:
        import tomllib
        text = toml_path.read_text(encoding="utf-8-sig")
        data = tomllib.loads(text)
    except Exception:
        return {}

    result: dict[str, list[str]] = {}
    for task, cfg in data.items():
        if isinstance(cfg, dict):
            models = cfg.get("models", [])
            if isinstance(models, list):
                result[task] = [str(m) for m in models if m]
    return result


def _all_unique_models(toml: dict[str, list[str]]) -> list[str]:
    """收集所有任务里出现过的去重模型列表（保持出现顺序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for models in toml.values():
        for m in models:
            if m not in seen:
                seen.add(m)
                result.append(m)
    return result


# ─────────────────────────────────────── interactive CLI ──

def run_override_cli() -> None:
    """
    交互式命令行：/override_model
    列出 TOML 里的可用模型，让用户选择覆盖目标 task 和模型。
    """
    toml_models = _read_toml_models()
    current = _load()

    # ── 步骤 1：选择要覆盖的任务 ──
    print("\n" + "─" * 56)
    print("  /override_model — 模型临时覆盖")
    print("─" * 56)
    print("  当前覆盖状态：")
    _print_active(current)
    print()

    task_choices: list[tuple[str | None, str]] = [(None, "全局覆盖（作用于所有任务）")]
    for t, label in TASK_LABELS.items():
        per = current.get("per_task", {})
        cur_model = per.get(t, "")
        suffix = f"  [当前: {cur_model}]" if cur_model else ""
        toml_first = toml_models.get(t, [""])[0]
        default_str = f"  TOML首选: {toml_first}" if toml_first and not cur_model else ""
        task_choices.append((t, f"{label}{suffix}{default_str}"))

    # 额外选项
    task_choices.append(("__clear_all__", "清除所有覆盖，恢复 TOML 路由"))
    task_choices.append(("__exit__", "退出不修改"))

    for i, (_, desc) in enumerate(task_choices):
        print(f"  {i}. {desc}")
    print()

    try:
        raw = input("  请选择编号（回车退出）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        return

    if not raw:
        print("  已取消")
        return

    try:
        idx = int(raw)
        if idx < 0 or idx >= len(task_choices):
            raise ValueError
    except ValueError:
        print("  无效编号，已退出")
        return

    selected_task, _ = task_choices[idx]

    if selected_task == "__exit__":
        print("  已取消")
        return

    if selected_task == "__clear_all__":
        clear_override("all")
        return

    # ── 步骤 2：选择模型 ──
    print()
    task_label = TASK_LABELS.get(selected_task, selected_task) if selected_task else "全局"
    print(f"  [{task_label}] 可用模型：")
    print("─" * 56)

    # 该 task 专属模型 + 所有模型去重合并
    task_specific = toml_models.get(selected_task, []) if selected_task else []
    all_models = task_specific[:]
    for m in _all_unique_models(toml_models):
        if m not in all_models:
            all_models.append(m)

    # 当前覆盖值
    cur_override = get_override(selected_task) if selected_task else current.get("global")

    model_choices: list[str | None] = []
    for i, m in enumerate(all_models):
        marker = " ← 当前覆盖" if m == cur_override else ""
        in_toml = " (TOML首选)" if task_specific and m == task_specific[0] else ""
        print(f"  {i}. {m}{in_toml}{marker}")
        model_choices.append(m)

    # 自定义 + 清除 + 退出
    custom_idx = len(model_choices)
    clear_idx  = custom_idx + 1
    exit_idx   = custom_idx + 2
    print(f"  {custom_idx}. [输入自定义模型 ID]")
    if cur_override:
        print(f"  {clear_idx}. [清除此任务的覆盖，恢复 TOML]")
    print(f"  {exit_idx}. 退出不修改")
    print()

    try:
        raw2 = input("  请选择编号（回车退出）: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")
        return

    if not raw2:
        print("  已取消")
        return

    try:
        idx2 = int(raw2)
    except ValueError:
        print("  无效编号，已退出")
        return

    if idx2 == exit_idx:
        print("  已取消")
        return

    if cur_override and idx2 == clear_idx:
        clear_override(selected_task)
        return

    if idx2 == custom_idx:
        try:
            custom = input("  请输入模型 ID（格式 or:xxx / ds:xxx / qwen:xxx）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消")
            return
        if not custom:
            print("  已取消")
            return
        chosen = custom
    elif 0 <= idx2 < len(model_choices):
        chosen = model_choices[idx2]  # type: ignore[assignment]
    else:
        print("  无效编号，已退出")
        return

    set_override(selected_task, chosen)  # type: ignore[arg-type]
    scope = f"[{selected_task}]" if selected_task else "[全局]"
    print(f"\n  ✓ 已设置 {scope} → {chosen}")
    print("  下次运行 main.py 时生效（无需重启）。")
    print(f"  清除：python main.py /override_model  →  选择「清除」选项")
    print("─" * 56 + "\n")
