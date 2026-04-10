from __future__ import annotations

import argparse

from automated_research_report_generator.flow.common import (
    DEFAULT_PDF_PATH,
    reset_runtime_logging_state,
)
from automated_research_report_generator.flow.research_flow import ResearchReportFlow

# 设计目的：统一 v2 的命令行入口和脚本入口，避免不同入口各自拼装 flow 调用。
# 模块功能：提供默认输入、启动主流程、生成流程图，并解析命令行参数。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：`--pdf` 和 `--plot`。
# 默认参数及原因：默认读取 `DEFAULT_PDF_PATH` 并执行 `kickoff()`，原因是更贴近日常本地运行。


def default_inputs() -> dict[str, str]:
    """
    设计目的：集中定义命令行和脚本共用的默认输入。
    模块功能：返回主流程启动时使用的最小输入字典。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：当前只包含 `pdf_file_path`。
    默认参数及原因：默认使用 `DEFAULT_PDF_PATH`，原因是方便本地直接运行。
    """

    return {"pdf_file_path": DEFAULT_PDF_PATH.as_posix()}


def kickoff(inputs: dict[str, str] | None = None):
    """
    设计目的：统一流程启动入口。
    模块功能：合并默认输入与外部参数，再执行 `ResearchReportFlow.kickoff()`。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：`inputs`。
    默认参数及原因：先加载默认输入，原因是命令行、脚本和测试都能复用同一套入口。
    """

    merged_inputs = default_inputs()
    if inputs:
        merged_inputs.update(inputs)
    reset_runtime_logging_state()
    flow = ResearchReportFlow()
    return flow.kickoff(inputs=merged_inputs)


def run():
    """
    设计目的：给脚本入口和 `pyproject` 命令提供最短调用路径。
    模块功能：直接转调 `kickoff()`。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：无。
    默认参数及原因：不额外包参数，原因是保持入口简单，避免出现多套默认值。
    """

    return kickoff()


def plot():
    """
    设计目的：提供流程图生成入口，方便检查 flow 结构。
    模块功能：实例化 `ResearchReportFlow` 并调用 `plot()`。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：当前固定输出名 `ResearchReportFlowV2`。
    默认参数及原因：固定图名，原因是便于持续覆盖最新版本而不是产生过多临时文件。
    """

    flow = ResearchReportFlow()
    return flow.plot("ResearchReportFlowV2")


def _parse_args() -> argparse.Namespace:
    """
    设计目的：集中管理命令行参数解析。
    模块功能：解析 PDF 路径和是否绘图两个参数。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：`--pdf` 与 `--plot`。
    默认参数及原因：默认沿用 `DEFAULT_PDF_PATH`，原因是和 `default_inputs()` 保持一致。
    """

    parser = argparse.ArgumentParser(description="Run the CrewAI v2 research-report flow.")
    parser.add_argument("--pdf", default=DEFAULT_PDF_PATH.as_posix(), help="PDF path to analyze.")
    parser.add_argument("--plot", action="store_true", help="Generate the flow plot instead of running the flow.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.plot:
        plot()
    else:
        kickoff({"pdf_file_path": args.pdf})
