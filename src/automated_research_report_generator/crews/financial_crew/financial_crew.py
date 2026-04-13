from __future__ import annotations

from pathlib import Path
from typing import List

import yaml
from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.llm_config import get_heavy_llm, get_lite_llm
from automated_research_report_generator.tools import FinancialMetricsCalculatorTool
from automated_research_report_generator.tools.pdf_page_tools import (
    ReadPdfPageIndexTool,
    ReadPdfPagesTool,
)

PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREW_LOG_FILE = str(PROJECT_LOG_DIR / "financial_crew.json")
ANALYSIS_PROFILE_KEYS = ("crew_name", "pack_name", "pack_title")


def load_analysis_profile(module_file: str) -> dict[str, str]:
    """
    目的：从当前专题自己的 `tasks.yaml` 里读取 Flow 运行所需的最小 profile 信息。
    功能：抽取 `crew_name`、`pack_name` 和 `pack_title`，供 Flow 编排与产物命名使用。
    实现逻辑：定位到当前模块同目录下的 `config/tasks.yaml`，只读取 `synthesize_and_output` 中维护的 profile 字段。
    可调参数：`module_file`，用于定位当前专题 crew 所在目录。
    默认参数及原因：默认只认 `synthesize_and_output`，原因是最终 pack 的命名语义应由最终汇总任务单点维护。
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
class FinancialCrew:
    """
    目的：承接财务专题的四段式 source-based 分析流程。
    功能：在本专题内显式定义抽取、计算、分析、汇总四个 agent 和四个 task，并输出中间产物与最终 pack。
    实现逻辑：围绕“先抽事实、再补算、再解释、最后汇总”的顺序组织 CrewAI runtime，避免把计算和分析混在同一任务里。
    可调参数：模型温度、日志路径、任务配置，以及 PDF 工具和财务计算工具的挂载方式。
    默认参数及原因：默认使用 `Process.sequential`，原因是四个阶段之间存在明确前后依赖，顺序执行最稳定。
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
    compute_temperature = 0.05
    analysis_temperature = 0.25
    pdf_page_index_tool = ReadPdfPageIndexTool()
    pdf_page_reader_tool = ReadPdfPagesTool()
    financial_metrics_calculator_tool = FinancialMetricsCalculatorTool()

    @agent
    def extract_agent(self) -> Agent:
        """
        目的：构建财务专题的抽取 agent。
        功能：从 PDF 中抽取公司原始财务数据，并从同行 source 中抽取中位数与平均数。
        实现逻辑：只挂载 PDF 相关工具，让该 agent 专注于材料提取，不承担规则计算和结论分析。
        可调参数：`extract_temperature`、`max_iter`、PDF 工具和 YAML 中的角色设定。
        默认参数及原因：默认温度为 `0.1`，原因是抽取任务更强调稳定复现而不是自由发挥。
        """

        return Agent(
            config=self.agents_config["extract_agent"],  # type: ignore[index]
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool],
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
    def compute_agent(self) -> Agent:
        """
        目的：构建财务专题的规则计算 agent。
        功能：基于抽取结果补算缺失指标，并把公司指标与同行中位数、平均数整理成统一事实表。
        实现逻辑：同时挂载 PDF 工具和财务计算工具，允许在缺字段时回读 PDF，再通过确定性工具完成公式计算。
        可调参数：`compute_temperature`、`max_iter`、工具列表和 YAML 中的角色设定。
        默认参数及原因：默认温度为 `0.05`，原因是该任务以事实整理和规则计算为主，应尽量减少随意扩展。
        """

        return Agent(
            config=self.agents_config["compute_agent"],  # type: ignore[index]
            tools=[
                self.pdf_page_index_tool,
                self.pdf_page_reader_tool,
                self.financial_metrics_calculator_tool,
            ],
            llm=get_heavy_llm(temperature=float(self.compute_temperature)),
            function_calling_llm=get_lite_llm(temperature=0.1),
            max_iter=16,
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
    def financial_analysis_agent(self) -> Agent:
        """
        目的：构建财务专题的分析 agent。
        功能：基于计算结果、行业研究、业务研究和 PDF 文字证据解释财务变化与同行差异。
        实现逻辑：只挂载 PDF 读取工具，便于先根据计算结果确定分析重点，再回读 PDF 查找原因证据。
        可调参数：`analysis_temperature`、`max_iter`、PDF 工具和 YAML 中的角色设定。
        默认参数及原因：默认温度为 `0.25`，原因是该任务需要一定归纳能力，但仍需受事实边界约束。
        """

        return Agent(
            config=self.agents_config["financial_analysis_agent"],  # type: ignore[index]
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
        目的：构建财务专题的最终汇总 agent。
        功能：把抽取表格、计算结果和分析结论组装成最终财务分析 pack。
        实现逻辑：不挂额外工具，只消费三个上游中间产物，输出最终专题 pack。
        可调参数：`default_temperature`、`max_iter` 和 YAML 中的角色设定。
        默认参数及原因：默认温度为 `0.35`，原因是汇总任务需要一定组织能力，但不应再扩展事实边界。
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
    def extract_financial_data(self) -> Task:
        """
        目的：定义财务专题的材料抽取任务。
        功能：从 PDF 抽取公司原始财务数据，并从同行 source 中抽取同行中位数和平均数。
        实现逻辑：读取 `extract_financial_data` 配置，显式挂载 PDF 工具，不在此阶段引入规则计算工具。
        可调参数：任务 YAML、PDF 工具和 `file_source_output_path` 输入。
        默认参数及原因：默认串行执行，原因是计算任务必须建立在抽取结果之上。
        """

        task_config = dict(self.tasks_config["extract_financial_data"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.extract_agent(),
            tools=[self.pdf_page_index_tool, self.pdf_page_reader_tool],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def compute_financial_metrics(self) -> Task:
        """
        目的：定义财务专题的指标计算任务。
        功能：补算缺失指标，并输出公司指标与同行中位数、平均数组成的统一事实表。
        实现逻辑：读取 `compute_financial_metrics` 配置，并把抽取任务结果通过 `context` 注入计算阶段。
        可调参数：任务 YAML、财务计算工具、PDF 工具和计算结果输出路径。
        默认参数及原因：默认串行执行，原因是该任务必须在抽取结果稳定后再进行。
        """

        task_config = dict(self.tasks_config["compute_financial_metrics"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.compute_agent(),
            context=[self.extract_financial_data()],
            tools=[
                self.pdf_page_index_tool,
                self.pdf_page_reader_tool,
                self.financial_metrics_calculator_tool,
            ],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def analyze_financial_performance(self) -> Task:
        """
        目的：定义财务专题的分析任务。
        功能：解释公司各年财务变化与公司相对同行的差异原因。
        实现逻辑：读取 `analyze_financial_performance` 配置，并通过 `context` 接入抽取结果与计算结果。
        可调参数：任务 YAML、PDF 工具、行业/业务文本输入和分析结果输出路径。
        默认参数及原因：默认串行执行，原因是分析必须基于前两步产物开展。
        """

        task_config = dict(self.tasks_config["analyze_financial_performance"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.financial_analysis_agent(),
            context=[self.extract_financial_data(), self.compute_financial_metrics()],
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
        目的：定义财务专题的最终汇总任务。
        功能：组装原始表格、计算表格和分析结论，输出最终财务分析 pack。
        实现逻辑：读取 `synthesize_and_output` 配置，并显式依赖前三个任务的输出。
        可调参数：任务 YAML 和 `pack_output_path` 输入。
        默认参数及原因：默认串行执行，原因是最终 pack 必须建立在三个上游中间产物都已完成的前提上。
        """

        task_config = dict(self.tasks_config["synthesize_and_output"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.synthesizing_agent(),
            context=[
                self.extract_financial_data(),
                self.compute_financial_metrics(),
                self.analyze_financial_performance(),
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
        目的：返回当前财务专题自己的 CrewAI runtime。
        功能：固定组装四个 agent 和四个 task，维持“抽取 -> 计算 -> 分析 -> 汇总”的执行边界。
        实现逻辑：显式声明 `Crew(...)` 参数，不再复用跨专题 helper。
        可调参数：日志路径、task 列表、agent 列表和未来可追加的运行控制项。
        默认参数及原因：默认使用 `Process.sequential`，原因是四个步骤间的依赖关系明确且强。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)

        return Crew(
            name=self.crew_name,
            agents=[
                self.extract_agent(),
                self.compute_agent(),
                self.financial_analysis_agent(),
                self.synthesizing_agent(),
            ],
            tasks=[
                self.extract_financial_data(),
                self.compute_financial_metrics(),
                self.analyze_financial_performance(),
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
