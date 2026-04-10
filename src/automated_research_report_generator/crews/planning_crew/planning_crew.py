from __future__ import annotations

from pathlib import Path
from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.flow.models import RegistrySeedPlan
from automated_research_report_generator.llm_config import get_heavy_llm
from automated_research_report_generator.tools import RegistrySeedTool
from automated_research_report_generator.tools.pdf_page_tools import (
    ReadPdfPageIndexTool,
    ReadPdfPagesTool,
)

# 设计目的：把规划阶段单独固定下来，先形成统一的研究范围和问题骨架，避免后续 crew 各自发散。
# 模块功能：提供规划 agent，顺序执行研究范围、问题树和证据地图三步任务，并写稳定日志。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：planner 的 temperature、max_iter 和 `output_log_file_path`。
# 默认参数及原因：temperature 默认 0.2、max_iter 默认 25，原因是规划更看重结构稳定和多轮查页能力。

PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREW_LOG_FILE = str(PROJECT_LOG_DIR / "planning_crew.json")

# 设计目的：在同一 crew 内复用 PDF 索引和 PDF 正文读取工具，保证所有任务看到的是同一份页码视图。
# 模块功能：提供页码定位和页面正文读取。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：后续如果 PDF 访问后端变化，可在这里统一替换工具类。
# 默认参数及原因：当前直接使用默认构造，因为项目已经把标准行为封装在工具类里。
shared_pdf_page_index_tool = ReadPdfPageIndexTool()
shared_pdf_page_reader_tool = ReadPdfPagesTool()


@CrewBase
class PlanningCrew:
    """
    设计目的：把研究范围、问题树和初版证据地图固定在同一个规划 crew 里。
    模块功能：集中声明规划阶段的 agent、task、日志路径和顺序执行方式。
    实现逻辑：先定义研究边界，再生成问题树，最后落第一版证据地图，三步始终按顺序串接。
    可调参数：YAML 配置、日志输出路径、模型温度和迭代次数。
    默认参数及原因：默认采用顺序执行和固定日志文件，原因是规划结果需要稳定、可追踪、便于人工复核。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = DEFAULT_CREW_LOG_FILE

    @agent
    def research_planner(self) -> Agent:
        """
        设计目的：集中定义规划阶段唯一的 planner agent。
        模块功能：把 PDF 工具和 registry 工具注入同一个规划角色。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：agent 配置、工具列表、temperature 和迭代次数。
        默认参数及原因：默认 `temperature=0.9` 且不允许 delegation，原因是规划阶段需要直觉。
        """
        return Agent(
            config=self.agents_config["research_planner"],  # type: ignore[index]
            tools=[
                shared_pdf_page_index_tool,
                shared_pdf_page_reader_tool,
                RegistrySeedTool(),
            ],
            llm=get_heavy_llm(temperature=0.9),
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
    def define_research_scope(self) -> Task:
        """
        设计目的：先明确本轮研究的边界和重点。
        模块功能：创建研究范围定义任务。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：任务配置和输出格式。
        默认参数及原因：默认开启 markdown 输出，原因是研究范围更适合人工复核。
        """

        return Task(
            config=self.tasks_config["define_research_scope"],  # type: ignore[index]
            tools=[],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def seed_question_tree(self) -> Task:
        """
        设计目的：基于研究范围生成第一版问题树。
        模块功能：创建问题树任务，并串上前一任务作为上下文。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：任务配置和上下文依赖。
        默认参数及原因：默认依赖 `define_research_scope()`，原因是问题树必须先继承研究边界。
        """

        return Task(
            config=self.tasks_config["seed_question_tree"],  # type: ignore[index]
            context=[self.define_research_scope()],
            tools=[],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def seed_registry_judgments(self) -> Task:
        """
        设计目的：把问题树收敛成一组可直接写入 registry 的结构化 judgments。
        模块功能：创建结构化 judgment seed 任务，并串上问题树作为上游上下文。
        实现逻辑：使用 `RegistrySeedPlan` 结构化输出，避免 Flow 再反向解析 markdown。
        可调参数：任务配置和上下文依赖。
        默认参数及原因：默认依赖 `seed_question_tree()`，原因是 judgment seed 要继承问题树的研究重点。
        """

        return Task(
            config=self.tasks_config["seed_registry_judgments"],  # type: ignore[index]
            context=[self.seed_question_tree()],
            tools=[],
            async_execution=False,
            output_json=RegistrySeedPlan,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=False,
        )

    @task
    def seed_evidence_map(self) -> Task:
        """
        设计目的：把问题树进一步落成初版证据地图。
        模块功能：创建证据地图任务，并串上问题树结果。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：任务配置和上下文依赖。
        默认参数及原因：默认依赖 `seed_question_tree()`，原因是证据地图要围绕问题树展开。
        """

        return Task(
            config=self.tasks_config["seed_evidence_map"],  # type: ignore[index]
            context=[self.seed_question_tree(), self.seed_registry_judgments()],
            tools=[],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @crew
    def crew(self) -> Crew:
        """
        设计目的：统一返回规划阶段使用的 Crew 实例。
        模块功能：确保日志目录存在，并构造顺序执行的 planning crew。
        实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
        可调参数：日志路径、缓存、tracing 和 `chat_llm`。
        默认参数及原因：默认采用 `Process.sequential`，原因是三步规划任务天然前后依赖。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)
        return Crew(
            name="planning_crew",
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
