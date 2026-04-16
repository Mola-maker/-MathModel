"""LLM 路由健康检查器。

两种模式：
  完整模式 (/checkLLM)    — 每个唯一模型发送 1-token 测试调用，报告延迟与状态
  快速模式 (启动前自检)   — 只验证 API key 是否存在 + 非占位符，不发网络请求
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ─────────────────────────────────────────── provider 元信息 ──

_PROVIDER_META = {
    "or":   {"name": "OpenRouter",  "key_env": "OPENROUTER_API_KEY",  "url_env": "OPENROUTER_BASE_URL",  "default_url": "https://openrouter.ai/api/v1"},
    "ds":   {"name": "DeepSeek",    "key_env": "DEEPSEEK_API_KEY",    "url_env": "DEEPSEEK_BASE_URL",    "default_url": "https://api.deepseek.com"},
    "qwen": {"name": "Dashscope",   "key_env": "QWEN_API_KEY",        "url_env": "QWEN_BASE_URL",        "default_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
}

_PLACEHOLDERS = {
    "PASTE_YOUR_DEEPSEEK_KEY_HERE",
    "PASTE_YOUR_QWEN_API_KEY_HERE",
    "PASTE_YOUR_OPENROUTER_KEY_HERE",
    "your_key_here",
    "sk-xxx",
}


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "…" + key[-4:]


def _key_ok(key: str) -> bool:
    return bool(key) and key.strip() not in _PLACEHOLDERS and len(key) > 8


# ─────────────────────────────────────────── provider checks ──

def _check_provider_key(prefix: str) -> dict:
    """Return {ok, key_masked, url, name} for a provider without any network call."""
    meta = _PROVIDER_META.get(prefix, {})
    key  = os.getenv(meta.get("key_env", ""), "").strip()
    url  = os.getenv(meta.get("url_env", ""), meta.get("default_url", ""))
    ok   = _key_ok(key)
    return {
        "prefix":     prefix,
        "name":       meta.get("name", prefix),
        "ok":         ok,
        "key_masked": _mask_key(key) if ok else "（未配置）",
        "url":        url,
        "error":      "" if ok else f"{meta.get('key_env','')} 未设置或为占位符",
    }


def _call_model_test(spec: str, timeout: float = 15.0) -> dict:
    """Send a 1-token completion to verify the model is reachable. Returns {ok, latency_ms, error}."""
    from openai import OpenAI, APIError

    for prefix, meta in _PROVIDER_META.items():
        if spec.startswith(f"{prefix}:"):
            model_id = spec[len(prefix) + 1:]
            key = os.getenv(meta["key_env"], "").strip()
            url = os.getenv(meta.get("url_env", ""), meta["default_url"])
            if not _key_ok(key):
                return {"ok": False, "latency_ms": 0, "error": "API key 未配置"}
            client = OpenAI(api_key=key, base_url=url, timeout=timeout, max_retries=0)
            t0 = time.time()
            try:
                client.chat.completions.create(
                    model=model_id,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
                return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "error": ""}
            except APIError as e:
                msg = str(e)
                # 403/401 = auth error (key wrong but endpoint reachable)
                if "401" in msg or "403" in msg or "authentication" in msg.lower():
                    return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                            "error": f"认证失败 — API key 无效或无权限 ({msg[:80]})"}
                # 404 = model not found on this provider
                if "404" in msg or "not found" in msg.lower() or "not a valid model" in msg.lower():
                    return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                            "error": f"模型不存在于该提供商 ({msg[:80]})"}
                return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "error": msg[:120]}
            except Exception as e:
                return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "error": str(e)[:120]}
    return {"ok": False, "latency_ms": 0, "error": f"未知提供商前缀: {spec}"}


# ─────────────────────────────────────────── fast startup check ──

def run_startup_check() -> bool:
    """
    快速自检（仅验证 key，不发 LLM 请求）。
    返回 True 表示至少有一个提供商可用；
    打印单行摘要，便于管道启动时快速感知。
    """
    from agents.model_router import _load_routes
    from agents.model_override import list_overrides

    routes = _load_routes()

    # 收集所有用到的提供商前缀
    used_prefixes: set[str] = set()
    for cfg in routes.values():
        for spec in (cfg.get("models") or []):
            pfx = spec.split(":")[0] if ":" in spec else "or"
            used_prefixes.add(pfx)
    # 也检查覆盖里的提供商
    overrides = list_overrides()
    for spec in ([overrides.get("global")] + list((overrides.get("per_task") or {}).values())):
        if spec and ":" in spec:
            used_prefixes.add(spec.split(":")[0])

    parts: list[str] = []
    any_ok = False
    for pfx in sorted(used_prefixes):
        r = _check_provider_key(pfx)
        if r["ok"]:
            parts.append(f"{r['name']} OK")
            any_ok = True
        else:
            parts.append(f"{r['name']} ERR")

    # Active overrides notice
    per = {k: v for k, v in (overrides.get("per_task") or {}).items() if v}
    g = overrides.get("global")
    override_note = ""
    if g or per:
        items = []
        if g:
            items.append(f"global→{g}")
        for t, m in per.items():
            items.append(f"{t}→{m}")
        override_note = "  [override: " + ", ".join(items) + "]"

    status = "  ".join(parts)
    print(f"[LLM CHECK] {status}{override_note}")
    return any_ok


# ─────────────────────────────────────────── full CLI check ──

def run_check_cli() -> None:
    """
    完整交互式检查：验证所有路由模型的实际可用性（发 1-token 请求）。
    python main.py /checkLLM
    """
    from agents.model_router import _load_routes
    from agents.model_override import list_overrides, TASK_LABELS

    routes  = _load_routes()
    current = list_overrides()

    W = 60
    print("\n" + "─" * W)
    print("  /checkLLM — LLM 路由健康检查")
    print("─" * W)

    # ── 1. 提供商 key 状态 ──
    print("\n  提供商配置")
    print("  " + "─" * (W - 2))

    used_prefixes: set[str] = set()
    for cfg in routes.values():
        for spec in (cfg.get("models") or []):
            if ":" in spec:
                used_prefixes.add(spec.split(":")[0])

    provider_ok: dict[str, bool] = {}
    for pfx in ("or", "ds", "qwen"):
        r = _check_provider_key(pfx)
        provider_ok[pfx] = r["ok"]
        mark = "OK" if r["ok"] else "ERR"
        key_info = r["key_masked"] if r["ok"] else r["error"]
        used_mark = "" if pfx in used_prefixes else "  (未使用)"
        print(f"  {mark} {r['name']:<12} {r['url']:<42} {key_info}{used_mark}")

    # ── 2. 当前覆盖 ──
    per = {k: v for k, v in (current.get("per_task") or {}).items() if v}
    g   = current.get("global")
    print(f"\n  当前覆盖")
    print("  " + "─" * (W - 2))
    if not g and not per:
        print("  （无覆盖，全部走 TOML 路由）")
    else:
        if g:
            print(f"  全局 → {g}")
        for t, m in per.items():
            print(f"  [{t}] {TASK_LABELS.get(t, t)} → {m}")

    # ── 3. 每个唯一模型的实际测试 ──
    print(f"\n  模型实际调用测试（每个唯一模型发 1-token 请求）")
    print("  " + "─" * (W - 2))

    # 收集所有需要测试的 spec，去重，并记录关联的 task
    spec_tasks: dict[str, list[str]] = {}
    for task, cfg in routes.items():
        for spec in (cfg.get("models") or [])[:1]:  # 只测每个 task 的首选模型
            spec_tasks.setdefault(spec, []).append(task)
    # 覆盖里的也测
    for spec in ([g] + list(per.values())):
        if spec:
            spec_tasks.setdefault(spec, []).append("(override)")

    pass_count = 0
    fail_count = 0
    already: dict[str, dict] = {}   # spec → result (避免同一 spec 重复测)

    for spec, tasks in spec_tasks.items():
        pfx = spec.split(":")[0] if ":" in spec else "or"
        task_str = ", ".join(dict.fromkeys(tasks))  # dedupe preserve order
        short_spec = spec[len(pfx) + 1:]            # strip prefix for display

        if spec in already:
            r = already[spec]
            print(f"  NEXT {spec:<45}  (同上)")
            continue

        if not provider_ok.get(pfx, False):
            result = {"ok": False, "latency_ms": 0, "error": "提供商 key 未配置，跳过"}
            mark = "ERR"
        else:
            print(f"  … 测试 {spec:<45}", end="", flush=True)
            result = _call_model_test(spec)
            # clear the "… testing" line
            print(f"\r  {'':60}", end="\r")
            mark = "OK" if result["ok"] else "ERR"

        already[spec] = result
        if result["ok"]:
            pass_count += 1
            print(f"  {mark} {spec:<45}  {result['latency_ms']}ms   [{task_str}]")
        else:
            fail_count += 1
            err = result["error"] or "未知错误"
            print(f"  {mark} {spec:<45}  [{task_str}]")
            print(f"      NEXT {err}")

    # ── 4. 总结 ──
    total = pass_count + fail_count
    print()
    print("─" * W)
    if fail_count == 0:
        print(f"  OK 全部通过  {pass_count}/{total} 个模型可用")
    elif pass_count == 0:
        print(f"  ERR 全部失败  0/{total} 可用 — 请检查 .env 中的 API key")
    else:
        print(f"   部分通过  {pass_count}/{total} 可用，{fail_count} 个模型不可达")
        print("    提示: 可用 /override_model 将失败任务切换到可用模型")
    print("─" * W + "\n")
