from __future__ import annotations

"""
目的：为仓库内的 live harness、监控与汇总逻辑提供统一的内部测试模块入口。
功能：集中承载 live case 声明、运行器、事件监听、失控监控和结果数据模型。
实现逻辑：只暴露内部测试所需的轻量模块命名空间，不改动生产业务入口。
可调参数：当前无显式参数，具体运行参数由 `live_runner.py` 的 CLI 负责解析。
默认参数及原因：默认仅作为内部模块被 `python -m automated_research_report_generator.testing.live_runner` 调用，原因是避免把实验性 harness 暴露成正式生产 CLI。
"""

__all__ = ["live_cases", "live_models", "live_monitor", "live_runner"]
