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

PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREW_LOG_FILE = str(PROJECT_LOG_DIR / "operating_metrics_crew.json")
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
class OperatingMetricsCrew:
    """
    目的：承接运营指标专题的四段式 source-based 分析流程。
    功能：在本专题内显式定义抽取、搜索、分析、汇总四类 agent 与 task，并输出 source md、中间分析产物和最终 pack。
    实现逻辑：围绕“先抽公司指标，再找同行可比，再分析，再汇总”的顺序直接组装 CrewAI runtime，避免把专题专属逻辑散落到公共 helper。
    可调参数：模型温度、日志路径、任务配置，以及各阶段所挂载的 PDF / 搜索工具。
    默认参数及原因：当前保持 4-agent / 4-task 的最小闭环，原因是既满足运营指标专题的新链路，又尽量不扩散到其他专题。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = DEFAULT_CREW_LOG_FILE

    crew_name = CREW_PROFILE["crew_name"]
    pack_name = CREW_PROFILE["pack_name"]
    pack_title = CREW_PROFILE["pack_title"]
    default_temperature = 0.35
    extract_temperature = 0.1
    search_temperature = 0.1
    analysis_temperature = 0.25
    pdf_page_index_tool = ReadPdfPageIndexTool()
    pdf_page_reader_tool = ReadPdfPagesTool()
    search_tool = SerperDevTool()

    @agent
    def extract_agent(self) -> Agent:
        """
        目的：构建运营指标专题的抽取 agent。
        功能：先从 PDF 抽取公司已披露的 operating metrics，再结合行业分析补充行业关键指标定义，统一写入 `file source`。
        实现逻辑：同时挂载 PDF 工具和搜索工具，让同一个 agent 在固定顺序里先抽事实、再补行业口径，但不直接做同行可比分析。
        可调参数：`extract_temperature`、`max_iter`、工具列表和 YAML 中的 agent persona。
        默认参数及原因：默认温度为 `0.1` 且启用工具调用模型，原因是该任务更强调稳定抽取与有限补充，而不是发散写作。
        """

        return Agent(
            config=self.agents_config["extract_agent"],  # type: ignore[index]
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool, self.search_tool],
            llm=get_heavy_llm(temperature=float(self.extract_temperature)),
            function_calling_llm=get_lite_llm(temperature=0.1),
            max_iter=14,
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
        目的：构建运营指标专题的同行搜索 agent。
        功能：基于可比公司名单搜索公开披露的 peer operating metrics，并生成 `search source` Markdown。
        实现逻辑：只挂载搜索工具，把同行可比数据整理与公司 PDF 抽取阶段分开，避免职责混淆。
        可调参数：`search_temperature`、`max_iter`、工具列表和 YAML 中的搜索角色设定。
        默认参数及原因：默认温度为 `0.1` 且启用工具调用模型，原因是该阶段更看重事实补齐与口径收敛。
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
    def operating_metrics_analysis_agent(self) -> Agent:
        """
        目的：构建运营指标专题的内部分析 agent。
        功能：基于 file source、search source、业务分析和行业分析，解释公司指标趋势与同行相对位置。
        实现逻辑：只挂载 PDF 工具，允许 agent 在发现关键解释缺口时回读 PDF，但不再去扩展新的搜索范围。
        可调参数：`analysis_temperature`、`max_iter`、工具列表和 YAML 中的分析角色设定。
        默认参数及原因：默认温度为 `0.25`，原因是该阶段需要适度归纳，但仍要受上游事实边界约束。
        """

        return Agent(
            config=self.agents_config["operating_metrics_analysis_agent"],  # type: ignore[index]
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool],
            llm=get_heavy_llm(temperature=float(self.analysis_temperature)),
            function_calling_llm=get_lite_llm(temperature=0.1),
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

    @agent
    def synthesizing_agent(self) -> Agent:
        """
        目的：构建运营指标专题的综合撰写 agent。
        功能：消费 file source、search source 和内部分析产物，输出最终专题 pack。
        实现逻辑：不再额外挂工具，只在现有三份上游产物边界内组织表格和分析。
        可调参数：`default_temperature`、`max_iter` 和 YAML 中的综合写作角色设定。
        默认参数及原因：默认温度为 `0.35` 且关闭工具调用模型，原因是综合阶段只允许在既有事实边界内重组表达。
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
        目的：定义运营指标专题的抽取任务。
        功能：让 agent 先抽取公司已披露指标，再补行业关键指标，统一生成 `file source`。
        实现逻辑：直接读取 `extract_from_pdf` 配置，并显式挂载 PDF 工具和搜索工具。
        可调参数：任务 YAML 内容、工具列表和 `file_source_output_path` 输入。
        默认参数及原因：默认 `async_execution=True`，原因是它可以与同行搜索任务并行推进。
        """

        task_config = dict(self.tasks_config["extract_from_pdf"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.extract_agent(),
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool, self.search_tool],
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
        目的：定义运营指标专题的同行搜索任务。
        功能：让 agent 只使用搜索工具生成 `search source`，整理同行可比指标。
        实现逻辑：直接读取 `search_public_sources` 配置，去掉 profile 字段后交给 CrewAI。
        可调参数：任务 YAML 内容、搜索工具和 `search_source_output_path` 输入。
        默认参数及原因：默认 `async_execution=True`，原因是它与抽取任务天然可并行。
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
    def analyze_operating_metrics(self) -> Task:
        """
        目的：定义运营指标专题的内部分析任务。
        功能：在 source 完成后，基于业务与行业文本解释公司指标趋势、同行差异与关键缺口。
        实现逻辑：读取 `analyze_operating_metrics` 配置，并显式把前两个 source 任务作为 `context` 接入分析阶段。
        可调参数：任务 YAML 内容、PDF 工具列表和 `operating_metrics_analysis_output_path` 输入。
        默认参数及原因：默认 `async_execution=False`，原因是分析必须建立在两份 source 都稳定落盘之后。
        """

        task_config = dict(self.tasks_config["analyze_operating_metrics"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.operating_metrics_analysis_agent(),
            context=[self.extract_from_pdf(), self.search_public_sources()],
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def synthesize_and_output(self) -> Task:
        """
        目的：定义运营指标专题的综合写作任务。
        功能：等待 file source、search source 和内部分析完成后，输出最终 pack。
        实现逻辑：直接读取 `synthesize_and_output` 配置，并显式把前三任务通过 `context` 接入综合阶段。
        可调参数：任务 YAML 内容、`pack_output_path` 输入以及未来专题专属的上下文拼接方式。
        默认参数及原因：默认 `async_execution=False`，原因是最终汇总必须建立在三份上游产物都稳定之后。
        """

        task_config = dict(self.tasks_config["synthesize_and_output"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.synthesizing_agent(),
            context=[
                self.extract_from_pdf(),
                self.search_public_sources(),
                self.analyze_operating_metrics(),
            ],
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
        目的：返回运营指标专题自己的 CrewAI runtime。
        功能：固定组装 4 个 agent 和 4 个 task，维持“抽取/同行搜索并行，分析和汇总串行”的执行边界。
        实现逻辑：显式在本文件里声明 `Crew(...)` 参数，而不是再走跨专题 helper。
        可调参数：日志路径、task 列表、agent 列表以及未来按专题追加的运行控制开关。
        默认参数及原因：默认使用 `Process.sequential`，原因是整体仍是稳定的四步链路，只把前两步下放到任务级并行。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)

        return Crew(
            name=self.crew_name,
            agents=[
                self.extract_agent(),
                self.search_agent(),
                self.operating_metrics_analysis_agent(),
                self.synthesizing_agent(),
            ],
            tasks=[
                self.extract_from_pdf(),
                self.search_public_sources(),
                self.analyze_operating_metrics(),
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
