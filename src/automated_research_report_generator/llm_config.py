from __future__ import annotations

import os

from crewai import LLM

OPENROUTER_API_BASE = os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1")
DEFAULT_HEAVY_MODEL = os.getenv(
    "HEAVY_LLM_MODEL",
    "openrouter/google/gemini-3.1-flash-lite-preview")
DEFAULT_LITE_MODEL = os.getenv(
    "LITE_LLM_MODEL", 
    "openrouter/google/gemini-2.5-flash-lite")


def get_heavy_llm(
    temperature: float = 0.5,
    timeout: float | int | None = 45,
    max_retries: int | None = 5,
) -> LLM:
    """
    目的：给项目里的重型分析任务提供统一主模型入口。
    功能：返回默认用于分析、估值、thesis 和写作阶段的 `LLM` 实例。
    实现逻辑：直接显式写出 OpenRouter 所需参数，避免额外隐藏一层 helper。
    可调参数：`temperature`、`timeout` 和 `max_retries`。
    默认参数及原因：默认 `0.5 / 45 / 5`，原因是重型任务需要适度展开，同时保持可控超时与重试。
    """
    return LLM(
        model=DEFAULT_HEAVY_MODEL,
        base_url=OPENROUTER_API_BASE,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )


def get_lite_llm(
    temperature: float = 0.1,
    timeout: float | int | None = 45,
    max_retries: int | None = 5,
) -> LLM:
    """
    目的：给项目里的轻型或工具调用任务提供统一模型入口。
    功能：返回默认用于轻量任务和 function calling 的 `LLM` 实例。
    实现逻辑：直接显式写出 OpenRouter 所需参数；若未单独配置轻模型，则使用内置的默认 lite 模型。
    可调参数：`temperature`、`timeout` 和 `max_retries`。
    默认参数及原因：默认 `0.1 / 45 / 5`，原因是轻型任务更强调收敛、稳定和工具调用可控性。
    """
    return LLM(
        model=DEFAULT_LITE_MODEL,
        base_url=OPENROUTER_API_BASE,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )

__all__ = ["get_heavy_llm", "get_lite_llm"]
