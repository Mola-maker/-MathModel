"""
======================================================================
Model Router 简单的介绍
======================================================================

1. _load_routes()
   - 调用方: get_task_route() (仅内部)
   - 任务: 解析 TOML 文件抗乱码(BOM)与防崩溃兜底机制。
   - 返回值: dict (全局全量路由配置)
   - 关联文件: .env (获取配置路径), model_routes.toml (数据源)

2. get_task_route(task)
   - 调用方: 3 个 get_route_* 对外接口
   - 任务: 提取专属配置块；命中失败时逐级降级 (task -> default -> 硬编码备用)。
   - 返回值: dict (单任务配置块字典)
   - 关联文件:  TOML 

3. get_route_models(task, override)
   - 调用方: 外部主程序 / Agent 调度核心 (如 call_model)
   - 任务: 拼装去重后的模型出战名单，严格遵循优先级：特权(override) > 专属 > 默认。
   - 返回值: list[str] (可用模型名称列表，如 ["ds:deepseek-chat"])
   - 关联文件: 决定并分发给底层 API 请求模块实际使用的模型节点

4. get_route_timeout(task)
   - 调用方: 外部主程序 API 请求层
   - 任务: 提取单次大模型对话的极限等待时间（防止单次请求卡死，缺省 60.0）。
   - 返回值: float (秒数)
   - 关联文件: 直接注入底层网络库 (如 httpx/requests) 的 timeout 参数

5. get_route_budget(task)
   - 调用方: 外部 Agent 运行沙箱 / 任务编排器
   - 任务: 提取整个任务流程允许耗费的最高时间/资源上限（防止死循环，缺省 180.0）。
   - 返回值: float (秒数)
   - 关联文件: 用于 Orchestrator (编排中心) 控制全生命周期的强制截断退出
======================================================================
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
#采用相对路径
CURRENT_DIR = Path(__file__).parent.parent   # project root (E:\mathmodel)
DEFAULT_PATH = CURRENT_DIR / "config" / "model_routes.toml"
#os 负责读操作系统环境变量，tomllib 负责解析 TOML 格式的配置文件，pathlib 负责处理文件路径。

_ROUTES_PATH = Path(os.getenv("MODEL_ROUTES_FILE", DEFAULT_PATH))
# The MODEL_ROUTES_FILE environment variable can be set to specify a custom path for the model routing configuration.
# fallback机制，兜底模型系列和参数，确保即使没有配置文件也能正常运行。
_FALLBACK = {
    "default": {
        "models": [
            "qwen:qwen-plus",
            "qwen:qwen-turbo",
            "qwen:qwen-max",
        ],
        "timeout_seconds": 60,
        "budget_seconds": 180,
    }
}

# 这个模块负责根据任务类型（如不同阶段的建模任务）动态选择和配置使用的语言模型。
# 通过读取一个 TOML 格式的配置文件（路径可通过环境变量 MODEL_ROUTES_FILE 指定），它定义了不同任务对应的模型列表、超时时间和
def _load_routes() -> dict:
    if _ROUTES_PATH.exists():
        try:
            # Use utf-8-sig to tolerate BOM written by some editors/shells.
            text = _ROUTES_PATH.read_text(encoding="utf-8-sig")
            #Byte Order mask是win系统保存文件时候细化在开头偷偷夹带私货的几个特殊字符，utf-8-sig编码可以自动识别并去除这些字符，避免解析错误。
            data = tomllib.loads(text)
            if isinstance(data, dict):# 确保解析结果是一个字典
                return data
        except Exception:
            return _FALLBACK
    return _FALLBACK

# 提供了三个主要函数：get_task_route 根据任务类型获取对应的路由配置；

def get_task_route(task: str | None) -> dict:
    routes = _load_routes()#负责加载配置文件中的路由信息，如果解析失败，则返回预设的默认
    key = (task or "default").strip()
    route = routes.get(key) if isinstance(routes, dict) else None
    #返回的不是字典这不是炸了
    default_route = routes.get("default") if isinstance(routes, dict) else None

    if not isinstance(route, dict):
        route = default_route if isinstance(default_route, dict) else _FALLBACK["default"]

    return route


def get_route_models(task: str | None, override: str | None = None) -> list[str]:
    route = get_task_route(task)
    default_route = get_task_route("default")

    models: list[str] = []
    seen: set[str] = set()

    def add_model(m: str) -> None:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            models.append(m)
    #override是用来干啥的呢
    if override:
        add_model(override)

    for m in route.get("models", []) if isinstance(route.get("models", []), list) else []:
        add_model(str(m))

    if task and task != "default":
        for m in default_route.get("models", []) if isinstance(default_route.get("models", []), list) else []:
            add_model(str(m))
    #models便是最终的出战列表
    return models


def get_route_timeout(task: str | None) -> float:
    route = get_task_route(task)
    value = route.get("timeout_seconds", 60)
    try:
        return float(value)
    except Exception:
        return 60.0


def get_route_budget(task: str | None) -> float:
    route = get_task_route(task)
    value = route.get("budget_seconds", 180)
    try:
        return float(value)
    except Exception:
        return 180.0
