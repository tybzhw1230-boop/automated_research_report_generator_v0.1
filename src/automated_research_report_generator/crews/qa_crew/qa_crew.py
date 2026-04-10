from __future__ import annotations

from pathlib import Path
from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.llm_config import get_heavy_llm
from automated_research_report_generator.flow.models import GateReviewOutput
from automated_research_report_generator.tools import ReadRegistryTool, StatusUpdateTool
from automated_research_report_generator.tools.pdf_page_tools import (
    ReadPdfPageIndexTool,
    ReadPdfPagesTool,
)

# 设计目的：把 research 质量闸门独立出来，让是否放行、是否返工由专门 QA 角色判断，而不是由作者自己判断。
# 模块功能：提供 research 覆盖度审查角色，输出定向重跑所需的 gate 结果，并写稳定日志。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：QA agent 的 temperature、max_iter 和 `output_log_file_path`。
# 默认参数及原因：默认 `temperature=0.1`，原因是 QA 更强调稳定和严格。

PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREW_LOG_FILE = str(PROJECT_LOG_DIR / "qa_crew.json")

# 设计目的：让 QA agent 共享同一套 PDF 读取视图，避免页码和引用口径不一致。
# 模块功能：提供页码索引与页面正文读取。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：后续如果 PDF 工具实现变化，可在这里统一替换。
# 默认参数及原因：目前直接使用默认构造，因为项目已经把常用行为封装在工具类里。
shared_pdf_page_index_tool = ReadPdfPageIndexTool()
shared_pdf_page_reader_tool = ReadPdfPagesTool()


@CrewBase
class QACrew:
    """
    设计目的：把 research 阶段是否放行、是否返工交给独立 QA 角色，避免作者自己给自己放行。
    模块功能：集中声明覆盖度审查角色及对应任务。
    实现逻辑：只执行 research 外部 QA gate 所需的覆盖度审查，再把结构化 gate 结果返回给 flow。
    可调参数：YAML 配置、日志路径、结构化输出模型、模型温度和迭代次数。
    默认参数及原因：默认顺序执行且温度较低，原因是 QA 更强调稳定、严格和可重复。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = DEFAULT_CREW_LOG_FILE
    run_consistency_review: bool = False

    @agent
    def coverage_reviewer(self) -> Agent:
        """
        设计目的：集中定义问题覆盖度审查角色。
        模块功能：让该角色检查问题树、证据闭环和资料缺口。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：agent 配置、工具列表、temperature 和迭代次数。
        默认参数及原因：默认 `temperature=0.1`，原因是覆盖审查更强调稳定和严格。
        """

        return Agent(
            config=self.agents_config["coverage_reviewer"],  # type: ignore[index]
            tools=[
                shared_pdf_page_index_tool,
                shared_pdf_page_reader_tool,
                StatusUpdateTool(),
                ReadRegistryTool(),
            ],
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
    def review_question_coverage(self) -> Task:
        """
        设计目的：定义问题覆盖度审查任务。
        模块功能：创建输出 `GateReviewOutput` 的 QA 任务。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：任务配置和结构化输出类型。
        默认参数及原因：默认不开 markdown，原因是下游主要直接消费结构化结果。
        """
        return Task(
            config=self.tasks_config["review_question_coverage"],  # type: ignore[index]
            tools=[],
            async_execution=False,
            output_json=GateReviewOutput,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=False,
        )

    @crew
    def crew(self) -> Crew:
        """
        设计目的：统一返回 QA 阶段使用的 Crew 实例。
        模块功能：确保日志目录存在，并构造顺序执行的 qa crew。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：日志路径、缓存、tracing 和 `chat_llm`。
        默认参数及原因：默认采用 `Process.sequential`，原因是 research 外部 gate 当前只有单任务审查，但仍需要稳定日志与统一入口。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)
        return Crew(
            name="qa_crew",
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
