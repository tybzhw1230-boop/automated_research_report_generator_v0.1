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
from automated_research_report_generator.tools import TusharePeerDataTool

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
class PeerInfoCrew:
    """
    目的：承接同行专题的专用三阶段流程。
    功能：先筛选可比公司，再通过 Tushare 拉取同行数据，最后综合输出同行信息分析包。
    实现逻辑：把同行专题从通用的 PDF/搜索双源骨架改成“同行清单 source + 同行数据 source + pack”三步，并把职责分别挂到三个 agent。
    可调参数：模型温度、日志路径、任务配置，以及同行筛选和 Tushare 取数工具。
    默认参数及原因：保持 3-agent / 3-task 的最小稳定结构，原因是同行清单、同行数据和综合写作的职责边界明显不同。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = str(PROJECT_ROOT / "logs" / "peer_info_crew.json")

    crew_name = CREW_PROFILE["crew_name"]
    pack_name = CREW_PROFILE["pack_name"]
    pack_title = CREW_PROFILE["pack_title"]
    default_temperature = 0.35
    peer_list_temperature = 0.2
    peer_data_temperature = 0.1
    search_tool = SerperDevTool()
    tushare_peer_data_tool = TusharePeerDataTool()

    @agent
    def peer_list_agent(self) -> Agent:
        """
        目的：构建同行清单筛选 agent。
        功能：结合行业分析包、业务分析包和公开搜索结果，输出可比公司名单、纳入理由和排除理由。
        实现逻辑：只挂载搜索工具，不暴露 PDF 工具和 Tushare 工具，确保第一步专注于样本筛选解释。
        可调参数：`peer_list_temperature`、`max_iter`、搜索工具列表和 YAML 里的角色设定。
        默认参数及原因：默认温度为 `0.2`，原因是同行筛选需要一定判断空间，但仍要保持收敛。
        """

        return Agent(
            config=self.agents_config["peer_list_agent"],  # type: ignore[index]
            tools=[self.search_tool],
            llm=get_heavy_llm(temperature=float(self.peer_list_temperature)),
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
    def peer_data_agent(self) -> Agent:
        """
        目的：构建同行数据取数 agent。
        功能：基于已确认的同行清单，用 Tushare 批量拉取同行财务、估值和资本结构等数据底稿。
        实现逻辑：只挂载 `TusharePeerDataTool`，不允许回退到公开搜索补数，避免同行数据口径混杂。
        可调参数：`peer_data_temperature`、`max_iter`、Tushare 工具和 YAML 里的角色设定。
        默认参数及原因：默认温度为 `0.1`，原因是这个阶段更强调稳定取数和结构化整理。
        """
        return Agent(
            config=self.agents_config["peer_data_agent"],  # type: ignore[index]
            tools=[self.tushare_peer_data_tool],
            llm=get_heavy_llm(temperature=float(self.peer_data_temperature)),
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
        目的：构建同行专题综合写作 agent。
        功能：消费同行清单 source 和同行数据 source，输出最终同行信息分析包。
        实现逻辑：不再挂载额外工具，只做带边界说明的综合、归纳和结构化写作。
        可调参数：`default_temperature`、`max_iter` 和 YAML 里的综合 persona。
        默认参数及原因：默认温度为 `0.35`，原因是综合阶段需要组织表达，但不应脱离 source 边界。
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
    def build_peer_list(self) -> Task:
        """
        目的：定义同行清单筛选任务。
        功能：让 agent 基于行业和业务上下文加公开搜索输出同行清单 source。
        实现逻辑：直接读取本专题 `build_peer_list` 配置，并只挂搜索工具。
        可调参数：任务 YAML 内容、搜索工具和 `peer_list_source_output_path` 输入。
        默认参数及原因：默认串行执行，原因是同行数据取数必须建立在同行清单已经收敛之后。
        """

        task_config = dict(self.tasks_config["build_peer_list"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.peer_list_agent(),
            tools=[self.search_tool],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def collect_peer_data(self) -> Task:
        """
        目的：定义同行数据取数任务。
        功能：让 agent 基于上一阶段确定的同行名单，通过 Tushare 生成同行数据 source。
        实现逻辑：读取 `collect_peer_data` 配置，并显式把 `build_peer_list` 输出作为上下文接入。
        可调参数：任务 YAML 内容、Tushare 工具和 `peer_data_source_output_path` 输入。
        默认参数及原因：默认串行执行，原因是同行名单必须先确认，Tushare 才能稳定批量取数。
        """

        task_config = dict(self.tasks_config["collect_peer_data"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.peer_data_agent(),
            context=[self.build_peer_list()],
            tools=[self.tushare_peer_data_tool],
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
        目的：定义同行专题综合写作任务。
        功能：等同行清单 source 和同行数据 source 都完成后，输出最终同行信息分析包。
        实现逻辑：读取 `synthesize_and_output` 配置，并把前两步结果通过 `context` 接入综合阶段。
        可调参数：任务 YAML 内容和 `pack_output_path` 输入。
        默认参数及原因：默认串行执行，原因是综合写作必须建立在前两步已完成的前提上。
        """

        task_config = dict(self.tasks_config["synthesize_and_output"])  # type: ignore[index]
        for key in ANALYSIS_PROFILE_KEYS:
            task_config.pop(key, None)
        return Task(
            config=task_config,
            agent=self.synthesizing_agent(),
            context=[self.build_peer_list(), self.collect_peer_data()],
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
        目的：返回当前同行专题自己的 CrewAI runtime。
        功能：固定组装 3 个 agent 和 3 个 task，维持“同行清单 -> 同行数据 -> 综合输出”的执行边界。
        实现逻辑：显式在本文件里声明 `Crew(...)` 参数，而不是复用其他专题的 PDF/source 骨架。
        可调参数：日志路径、task 列表、agent 列表以及后续可能追加的运行控制参数。
        默认参数及原因：默认使用 `Process.sequential`，原因是三步之间存在明确前后依赖。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)

        return Crew(
            name=self.crew_name,
            agents=[
                self.peer_list_agent(),
                self.peer_data_agent(),
                self.synthesizing_agent(),
            ],
            tasks=[
                self.build_peer_list(),
                self.collect_peer_data(),
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
