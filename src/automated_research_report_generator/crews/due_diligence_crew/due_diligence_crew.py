from __future__ import annotations

from pathlib import Path
from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.llm_config import get_heavy_llm

# 目的：把尽调问题生成阶段收口成一个只消费 pack 和 source md 的最小单元。
# 功能：提供 1 个尽调分析 agent 和 1 个输出任务，在 analysis 阶段末尾生成尽调问题清单。
# 实现逻辑：不再接入 registry 或 review 工具，只基于 Flow 传入的 pack 文本和 source 文本成文。
# 可调参数：日志路径、模型温度和任务 YAML 配置。
# 默认参数及原因：默认采用 `Process.sequential`，原因是当前 crew 只有一个单点输出任务。
PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREW_LOG_FILE = str(PROJECT_LOG_DIR / "due_diligence_crew.json")


@CrewBase
class DueDiligenceCrew:
    """
    目的：封装 analysis 阶段末尾的尽调问题生成单元。
    功能：基于 7 个专题 pack 和 14 份 source md，输出高优先级尽调问题清单。
    实现逻辑：通过 1 个 agent 和 1 个 task 完成只读分析和 Markdown 成文。
    可调参数：日志路径、模型温度、任务 YAML 配置和输出路径。
    默认参数及原因：默认中低温运行，原因是尽调问题需要判断和排序，但不应发散成开放式写作。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = DEFAULT_CREW_LOG_FILE

    @agent
    def due_diligence_agent(self) -> Agent:
        """
        目的：集中定义尽调问题设计角色。
        功能：基于专题 pack 与 source md 提炼最可能改变投资判断的问题。
        实现逻辑：只挂空工具集，强制该阶段留在文本输入边界内。
        可调参数：Agent 配置、模型温度和最大迭代次数。
        默认参数及原因：默认 `temperature=0.3`，原因是问题优先级排序需要一定判断空间，但仍应收敛。
        """

        return Agent(
            config=self.agents_config["due_diligence_agent"],  # type: ignore[index]
            tools=[],
            llm=get_heavy_llm(temperature=0.3),
            function_calling_llm=None,
            max_iter=18,
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
    def generate_diligence_questions(self) -> Task:
        """
        目的：定义 analysis 末尾的尽调问题生成任务。
        功能：驱动尽调 agent 基于专题 pack 与 source md 输出问题清单。
        实现逻辑：复用 YAML 配置，并把输出文件路径由 Flow 在运行时注入。
        可调参数：任务配置、输出路径和 Markdown 开关。
        默认参数及原因：默认开启 Markdown 输出，原因是该任务直接产出下游 thesis 与报告要使用的文件。
        """

        return Task(
            config=self.tasks_config["generate_diligence_questions"],  # type: ignore[index]
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
        目的：统一返回尽调问题生成 crew。
        功能：确保日志目录存在，并构造顺序执行的单任务 Crew。
        实现逻辑：只收 1 个 agent 和 1 个 task，保持 analysis 末尾的单点输出边界。
        可调参数：日志路径和缓存配置。
        默认参数及原因：默认采用 `Process.sequential`，原因是这里只有一个尽调问题任务。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)
        return Crew(
            name="due_diligence_crew",
            agents=[self.due_diligence_agent()],
            tasks=[self.generate_diligence_questions()],
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
        )
