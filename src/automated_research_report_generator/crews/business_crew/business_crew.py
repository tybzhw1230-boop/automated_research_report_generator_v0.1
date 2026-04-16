from __future__ import annotations

from pathlib import Path
from typing import List

import yaml
from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.llm_config import get_heavy_llm, get_lite_llm
from automated_research_report_generator.tools.pdf_page_tools import (
    ReadPdfPageIndexTool,
    ReadPdfPagesTool,
)

ANALYSIS_PROFILE_KEYS = ("crew_name", "pack_name", "pack_title")


def load_analysis_profile(module_file: str) -> dict[str, str]:
    """
    目的：从当前专题自己的 `tasks.yaml` 中读取 Flow 运行所需的最小 profile 信息。
    功能：抽取 `crew_name`、`pack_name` 和 `pack_title`，供 Flow 编排与产物命名使用。
    实现逻辑：定位到当前模块同目录下的 `config/tasks.yaml`，只读取 `synthesize_and_output` 中维护的 profile 字段。
    可调参数：`module_file`，用于定位当前专题 crew 所在目录。
    默认参数及原因：默认只认 `synthesize_and_output`，原因是最终 pack 的命名语义应由综合任务单点维护。
    """

    config_path = Path(module_file).resolve().parent / "config" / "tasks.yaml"
    tasks_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    task_payload = tasks_config.get("synthesize_and_output")
    if not isinstance(task_payload, dict):
        raise ValueError(
            f"Missing synthesize_and_output config in {config_path.as_posix()}"
        )

    missing_keys = [
        key for key in ANALYSIS_PROFILE_KEYS if task_payload.get(key) is None
    ]
    if missing_keys:
        missing_display = ", ".join(sorted(missing_keys))
        raise ValueError(
            f"Missing analysis profile keys in {config_path.as_posix()}: {missing_display}"
        )

    return {key: str(task_payload[key]) for key in ANALYSIS_PROFILE_KEYS}


CREW_PROFILE = load_analysis_profile(__file__)


@CrewBase
class BusinessCrew:
    """
    目的：承接当前专题的 v0.3 source-based 分析流程。
    功能：在本专题内直接定义 file、search、synthesize 三类 agent 与 task，并输出 source md 和最终 pack。
    实现逻辑：不再依赖公共 helper，而是在本文件内显式组装 CrewAI runtime，方便后续加入专题专属分支。
    可调参数：模型温度、日志路径、任务配置，以及未来按专题继续扩展的专属逻辑。
    默认参数及原因：当前保持 3-agent / 3-task 的最小骨架，原因是先维持现有 POC 的真实运行边界。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = str(PROJECT_ROOT / "logs" / "business_crew.json")

    crew_name = CREW_PROFILE["crew_name"]
    pack_name = CREW_PROFILE["pack_name"]
    pack_title = CREW_PROFILE["pack_title"]
    default_temperature = 0.35
    extract_temperature = 0.1
    search_temperature = 0.1
    pdf_page_index_tool = ReadPdfPageIndexTool()
    pdf_page_reader_tool = ReadPdfPagesTool()
    search_tool = SerperDevTool()

    @agent
    def extract_agent(self) -> Agent:
        """
        目的：构建本专题的 PDF 抽取 agent。
        功能：只挂载 PDF 页索引和读页工具，负责生成 `file source` Markdown。
        实现逻辑：直接在本专题文件内声明 agent 参数，后续如有专属 PDF 分支可在这里扩展。
        可调参数：`extract_temperature`、`max_iter`、工具列表和 YAML 中的 agent persona。
        默认参数及原因：默认温度为 `0.1` 且启用工具调用模型，原因是该任务强调稳定抽取而不是自由发挥。
        """

        return Agent(
            config=self.agents_config["extract_agent"],  # type: ignore[index]
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool],
            llm=get_heavy_llm(temperature=float(self.extract_temperature)),
            function_calling_llm=get_lite_llm(temperature=0.1),
            max_iter=12,
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

    @agent
    def search_agent(self) -> Agent:
        """
        目的：构建本专题的公开资料搜索 agent。
        功能：只挂载搜索工具，负责生成 `search source` Markdown。
        实现逻辑：把搜索阶段的运行参数留在本专题文件里，便于未来针对单个专题加入专属搜索策略。
        可调参数：`search_temperature`、`max_iter`、工具列表和 YAML 中的搜索角色设定。
        默认参数及原因：默认温度为 `0.1` 且启用工具调用模型，原因是搜索阶段更看重补齐事实边界。
        """

        return Agent(
            config=self.agents_config["search_agent"],  # type: ignore[index]
            tools=[self.search_tool],
            llm=get_heavy_llm(temperature=float(self.search_temperature)),
            function_calling_llm=get_lite_llm(temperature=0.1),
            max_iter=12,
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

    @agent
    def synthesizing_agent(self) -> Agent:
        """
        目的：构建本专题的综合撰写 agent。
        功能：不再额外挂工具，只消费两份 source md 生成最终专题 pack。
        实现逻辑：把综合阶段的模型参数与 persona 直接放在本专题文件里，便于未来加入专题专属判断分支。
        可调参数：`default_temperature`、`max_iter` 和 YAML 中的综合写作角色设定。
        默认参数及原因：默认温度为 `0.35` 且关闭工具调用模型，原因是综合阶段只允许在 source 边界内组织表达。
        """

        return Agent(
            config=self.agents_config["synthesizing_agent"],  # type: ignore[index]
            tools=[],
            llm=get_heavy_llm(temperature=float(self.default_temperature)),
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
    def extract_from_pdf(self) -> Task:
        """
        目的：定义本专题的 PDF 抽取任务。
        功能：让 agent 只使用 PDF 工具生成 `file source`，并把结果写到 Flow 指定路径。
        实现逻辑：直接读取本专题 `extract_from_pdf` 配置，去掉仅供 profile 使用的字段后交给 CrewAI。
        可调参数：任务 YAML 内容、PDF 工具列表和 `file_source_output_path` 输入。
        默认参数及原因：默认 `async_execution=True`，原因是它需要与公开搜索任务并行运行。
        """

        task_config = dict(self.tasks_config["extract_from_pdf"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.extract_agent(),
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool],
            async_execution=True,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def search_public_sources(self) -> Task:
        """
        目的：定义本专题的公开资料搜索任务。
        功能：让 agent 只使用搜索工具生成 `search source`，并把结果写到 Flow 指定路径。
        实现逻辑：直接读取本专题 `search_public_sources` 配置，去掉 profile 字段后交给 CrewAI。
        可调参数：任务 YAML 内容、搜索工具和 `search_source_output_path` 输入。
        默认参数及原因：默认 `async_execution=True`，原因是它与 PDF 抽取天然可并行。
        """

        task_config = dict(self.tasks_config["search_public_sources"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.search_agent(),
            tools=[self.search_tool],
            async_execution=True,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def synthesize_and_output(self) -> Task:
        """
        目的：定义本专题的综合写作任务。
        功能：等待 `file source` 与 `search source` 完成后，基于两份 source 输出最终 pack。
        实现逻辑：直接读取本专题 `synthesize_and_output` 配置，并显式把前两任务通过 `context` 接入综合阶段。
        可调参数：任务 YAML 内容、`pack_output_path` 输入以及未来专题专属的上下文拼接方式。
        默认参数及原因：默认 `async_execution=False`，原因是综合写作必须在两个 source 都完成后再开始。
        """

        task_config = dict(self.tasks_config["synthesize_and_output"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.synthesizing_agent(),
            context=[self.extract_from_pdf(), self.search_public_sources()],
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
        目的：返回当前专题自己的 CrewAI runtime。
        功能：固定组装 3 个 agent 和 3 个 task，维持“file/search 先并行，synthesize 后串行”的执行边界。
        实现逻辑：显式在本文件里声明 `Crew(...)` 参数，而不是再走跨专题 helper。
        可调参数：日志路径、task 列表、agent 列表以及未来按专题追加的运行控制开关。
        默认参数及原因：默认使用 `Process.sequential`，原因是专题整体仍是确定性的三步，只把前两步交给任务级并行。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)

        return Crew(
            name=self.crew_name,
            agents=[
                self.extract_agent(),
                self.search_agent(),
                self.synthesizing_agent(),
            ],
            tasks=[
                self.extract_from_pdf(),
                self.search_public_sources(),
                self.synthesize_and_output(),
            ],
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
