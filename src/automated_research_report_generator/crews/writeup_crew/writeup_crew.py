from __future__ import annotations

from pathlib import Path
from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.llm_config import get_heavy_llm
from automated_research_report_generator.tools.markdown_to_pdf_tool import MarkdownToPdfTool
from automated_research_report_generator.tools.pdf_page_tools import (
    ReadPdfPageIndexTool,
    ReadPdfPagesTool,
)

# 设计目的：把报告写作和 PDF 导出拆开，让 Markdown 生成和最终导出可以分别验证。
# 模块功能：提供写作角色，先生成 Markdown，再执行 PDF 导出，并写稳定日志。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：editor 的 temperature、max_iter 和 `output_log_file_path`。
# 默认参数及原因：默认 `temperature=0.15`，原因是写作需要表达弹性，但不能脱离证据。

PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREW_LOG_FILE = str(PROJECT_LOG_DIR / "writeup_crew.json")

# 设计目的：让写作阶段始终使用同一套 PDF 读取视图，保证引用页码和正文口径一致。
# 模块功能：提供页码索引与页面正文读取。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：后续如果 PDF 工具实现变化，可在这里统一替换。
# 默认参数及原因：当前直接使用默认构造，因为项目已经把常用行为封装在工具类里。
shared_pdf_page_index_tool = ReadPdfPageIndexTool()
shared_pdf_page_reader_tool = ReadPdfPagesTool()


@CrewBase
class WriteupCrew:
    """
    设计目的：把最终成文和 PDF 导出固定为一个独立阶段，避免和研究判断混在一起。
    模块功能：集中声明最终报告编辑任务、PDF 导出任务和写作阶段日志配置。
    实现逻辑：先生成最终 Markdown，再把同一份成文结果交给 PDF 导出工具。
    可调参数：YAML 配置、日志路径、模型温度、迭代次数和导出工具。
    默认参数及原因：默认顺序执行，原因是 PDF 必须建立在已经定稿的 Markdown 之上。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = DEFAULT_CREW_LOG_FILE

    @agent
    def report_editor(self) -> Agent:
        """
        设计目的：集中定义最终报告编辑角色。
        模块功能：为写作阶段注入 PDF、registry 等基础工具。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：agent 配置、工具列表、temperature 和迭代次数。
        默认参数及原因：默认 `temperature=0.1`，原因是写作需要弹性但必须保持稳。
        """
        return Agent(
            config=self.agents_config["report_editor"],  # type: ignore[index]
            tools=[],
            llm=get_heavy_llm(temperature=0.1),
            function_calling_llm=None,
            max_iter=25,
            max_rpm=None,
            max_execution_time=None,
            verbose=True,
            allow_delegation=False,
            step_callback=None,
            cache=True,
            allow_code_execution=False,
            max_retry_limit=2,
            respect_context_window=True,
            use_system_prompt=True,
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
        )

    @task
    def compile_report(self) -> Task:
        """
        设计目的：定义最终 Markdown 报告编写任务。
        模块功能：创建对应 Task 实例。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：任务配置和输出格式。
        默认参数及原因：默认输出 markdown，原因是这一步本身就是成文产物。
        """
        return Task(
            config=self.tasks_config["compile_report"],  # type: ignore[index]
            tools=[],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def export_final_report(self) -> Task:
        """
        设计目的：定义最终 PDF 导出任务。
        模块功能：在编写完成后调用 `MarkdownToPdfTool` 导出 PDF。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：任务配置、上下文依赖和工具列表。
        默认参数及原因：默认依赖 `compile_report()`，原因是 PDF 必须基于最终 Markdown。
        """

        return Task(
            config=self.tasks_config["export_final_report"],  # type: ignore[index]
            context=[self.compile_report()],
            tools=[MarkdownToPdfTool()],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=False,
        )

    @crew
    def crew(self) -> Crew:
        """
        设计目的：统一返回写作阶段使用的 Crew 实例。
        模块功能：确保日志目录存在，并构造顺序执行的 writeup crew。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：日志路径、缓存、tracing 和 `chat_llm`。
        默认参数及原因：默认采用 `Process.sequential`，原因是先写 Markdown、再导出 PDF。
        """
        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)
        return Crew(
            name="writeup_crew",
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            manager_llm=None,
            manager_agent=None,
            function_calling_llm=None,
            config=None,
            max_rpm=None,
            memory=False,
            cache=True,
            embedder=None,
            share_crew=False,
            step_callback=None,
            task_callback=None,
            planning=False,
            planning_llm=None,
            tracing=True,
            output_log_file=self.output_log_file_path,
            chat_llm=get_heavy_llm(),
        )
