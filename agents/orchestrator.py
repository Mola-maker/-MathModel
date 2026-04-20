from __future__ import annotations

import json
import os
import time
from pathlib import Path

from openai import APIConnectionError, APIError, APITimeoutError, BadRequestError, OpenAI, RateLimitError

from agents.model_router import get_route_budget, get_route_models, get_route_timeout
from agents.model_override import get_override
from agents.data_recorder import get_recorder

CONTEXT_PATH = Path(os.getenv("CONTEXT_STORE", "context_store/context.json"))

_PROVIDER_CONFIG: dict[str, dict] = {
    "or": {
        "base_url": lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "api_key": lambda: os.getenv("OPENROUTER_API_KEY", ""),
    },
    "ds": {
        "base_url": lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": lambda: os.getenv("DEEPSEEK_API_KEY", ""),
    },
    "qwen": {
        "base_url": lambda: os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api_key": lambda: os.getenv("QWEN_API_KEY", ""),
    },
}

DEFAULT_CONTEXT = {
    "phase": "init",
    "competition": {
        "problem_text": "",
        "selected_problem": "",
        "tasks": [],
        "keywords": [],
    },
    "research": {
        "modeler_results": {},
        "coder_results": {},
        "writer_results": {},
        "references": [],
    },
    "modeling": {
        "model_type": "",
        "assumptions": [],
        "variables": {},
        "primary_model": {},
        "validation": {},
        "equations_latex": "",
        "solution_method": "",
        "comparison": {},
    },
    "code_execution": {
        "status": "",
        "artifacts": [],
        "current_script": "",
        "iterations": 0,
        "max_iterations": int(os.getenv("MAX_HEAL_ITERATIONS", "5")),
        "last_error": None,
    },
    "results": {
        "solver_stdout": "",
        "metrics": {},
        "figures": [],
        "sensitivity": {},
    },
    "paper": {
        "sections": {},
        "bibtex": "",
        "pdf_path": "",
    },
    "review": {
        "scores": {},
        "suggestions": [],
        "report_path": "",
    },
}


def _parse_model_spec(spec: str) -> tuple[str, str]:
    for prefix in _PROVIDER_CONFIG:
        if spec.startswith(f"{prefix}:"):
            return prefix, spec[len(prefix) + 1 :]
    return "or", spec


def _get_client(provider: str, timeout_seconds: float) -> OpenAI:
    cfg = _PROVIDER_CONFIG.get(provider, _PROVIDER_CONFIG["or"])
    return OpenAI(
        api_key=cfg["api_key"](),
        base_url=cfg["base_url"](),
        timeout=timeout_seconds,
        max_retries=0,
    )


def _provider_available(provider: str) -> bool:
    cfg = _PROVIDER_CONFIG.get(provider, _PROVIDER_CONFIG["or"])
    key = (cfg["api_key"]() or "").strip()
    if not key:
        return False
    if key == "PASTE_YOUR_DEEPSEEK_KEY_HERE":
        return False
    if key == "PASTE_YOUR_QWEN_API_KEY_HERE":
        return False
    return True


def load_context() -> dict:
    if not CONTEXT_PATH.exists():
        CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_context(DEFAULT_CONTEXT)
        return json.loads(json.dumps(DEFAULT_CONTEXT, ensure_ascii=False))
    return json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))


def save_context(ctx: dict) -> None:
    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_PATH.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")


def get_client() -> OpenAI:
    return _get_client("or", timeout_seconds=get_route_timeout("default"))


def _normalize_content(response: object) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise RuntimeError(f"Model returned no choices: {response}")

    first = choices[0]
    message = getattr(first, "message", None)
    content = getattr(message, "content", None)
    if content is None:
        content = getattr(first, "text", None)

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        content = "\n".join([p for p in parts if p])

    if not content:
        raise RuntimeError("Model returned empty content")
    return str(content)


def _should_continue_on_bad_request(err: BadRequestError) -> bool:
    msg = str(err).lower()
    patterns = [
        "not a valid model id",
        "model",
        "invalid",
        "content exists risk",
        "content policy",
        "safety",
        "developer instruction is not enabled",
    ]
    if "not a valid model id" in msg:
        return True
    if "developer instruction is not enabled" in msg:
        return True
    if "content exists risk" in msg or "content policy" in msg or "safety" in msg:
        return True
    # certain providers return 400 for incompatible params/model; move to next model
    if "provider returned error" in msg:
        return True
    return False


def call_model(system: str, user: str, model: str | None = None, task: str | None = None) -> str:
    # Override priority: explicit model= > per_task override > global override > TOML
    effective_model = model or get_override(task) or get_override(None)
    specs = get_route_models(task, effective_model)
    if not specs:
        raise RuntimeError(
            f"No model route available for task={task}. Check config/model_routes.toml or MODEL_ROUTES_FILE"
        )

    timeout_seconds = get_route_timeout(task)
    budget_seconds = get_route_budget(task)

    max_input_chars = int(os.getenv("MODEL_MAX_INPUT_CHARS", "32000"))
    if len(system) > max_input_chars:
        system = system[:max_input_chars]
    if len(user) > max_input_chars:
        user = user[:max_input_chars] + "\n\n[TRUNCATED]"

    start = time.time()
    last_err: Exception | None = None

    for spec in specs:
        if time.time() - start > budget_seconds:
            break

        provider, model_id = _parse_model_spec(spec)
        if not _provider_available(provider):
            continue

        client = _get_client(provider, timeout_seconds=timeout_seconds)

        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = _normalize_content(response)
            # Record token usage
            recorder = get_recorder()
            recorder.record_from_response(task or "default", response)
            print(f"  [LLM] {task or 'default'} -> {provider}:{model_id}")
            return content
        except BadRequestError as e:
            last_err = e
            msg = str(e).lower()
            # Dynamic shrink when provider rejects context length.
            if "maximum context length" in msg or "reduce the length" in msg or "context length" in msg:
                if len(user) > 4000:
                    user = user[: max(4000, len(user) // 2)] + "\n\n[TRUNCATED_MORE]"
                    continue
            if _should_continue_on_bad_request(e):
                continue
            raise
        except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Model call failed after retries and fallbacks: {last_err}")


def _message_to_dict(msg: object) -> dict:
    """Convert an OpenAI ChatCompletionMessage into a plain dict."""
    result: dict = {"role": getattr(msg, "role", "assistant")}
    content = getattr(msg, "content", None)
    result["content"] = content if content is not None else ""
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        result["tool_calls"] = []
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            result["tool_calls"].append({
                "id": getattr(tc, "id", None),
                "type": "function",
                "function": {
                    "name": getattr(fn, "name", "") if fn else "",
                    "arguments": getattr(fn, "arguments", "") if fn else "",
                },
            })
    return result


def call_with_tools(
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    task: str | None = None,
) -> dict:
    """Call a model with OpenAI function-calling; return the assistant message as dict.

    Falls back across the configured model list just like call_model().
    The returned dict always has role='assistant' and may include a
    'tool_calls' list. Caller decides whether to execute the calls and
    loop again by appending tool results and calling this function once more.
    """
    effective_model = model or get_override(task) or get_override(None)
    specs = get_route_models(task, effective_model)
    if not specs:
        raise RuntimeError(
            f"No model route available for task={task}. Check config/model_routes.toml"
        )

    timeout_seconds = get_route_timeout(task)
    budget_seconds = get_route_budget(task)
    start = time.time()
    last_err: Exception | None = None

    for spec in specs:
        if time.time() - start > budget_seconds:
            break
        provider, model_id = _parse_model_spec(spec)
        if not _provider_available(provider):
            continue
        client = _get_client(provider, timeout_seconds=timeout_seconds)
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise RuntimeError("Model returned no choices")
            msg_obj = choices[0].message
            recorder = get_recorder()
            try:
                recorder.record_from_response(task or "default", response)
            except Exception:
                pass
            print(f"  [LLM-tools] {task or 'default'} -> {provider}:{model_id}")
            return _message_to_dict(msg_obj)
        except BadRequestError as e:
            last_err = e
            if _should_continue_on_bad_request(e):
                continue
            raise
        except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"call_with_tools failed after retries: {last_err}")


class Orchestrator:
    def __init__(self) -> None:
        self.ctx = load_context()

    def reload(self) -> None:
        self.ctx = load_context()

    def save(self) -> None:
        save_context(self.ctx)

    def set_problem(self, problem_text: str, selected: str, tasks: list[str]) -> None:
        self.ctx["competition"]["problem_text"] = problem_text
        self.ctx["competition"]["selected_problem"] = selected
        self.ctx["competition"]["tasks"] = tasks
        self.save()

    def get_status(self) -> dict:
        return {
            "problem": self.ctx.get("competition", {}).get("selected_problem", ""),
            "model_type": self.ctx.get("modeling", {}).get("model_type", ""),
            "execution_status": self.ctx.get("code_execution", {}).get("status", ""),
            "artifacts": self.ctx.get("code_execution", {}).get("artifacts", []),
        }
