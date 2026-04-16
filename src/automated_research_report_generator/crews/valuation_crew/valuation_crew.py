from __future__ import annotations

from pathlib import Path
from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.llm_config import get_heavy_llm
from automated_research_report_generator.tools import (
    ComparableValuationTool,
    FootballFieldTool,
    IntrinsicValuationTool,
    TushareValuationDataTool,
)

# 目的：把可比估值、内在价值估值和最终估值汇总拆开，方便分别复核每一层假设。
# 功能：提供 3 个估值 agent，前两步并行执行可比和内在价值估值，最后串行汇总成最终估值结论。
# 实现逻辑：估值阶段只消费 Flow 传入的专题 pack 与少量高价值中间结果，不再依赖 manager 二次调度或全量 source 输入。
# 可调参数：估值 agent 的 temperature、max_iter 和 `output_log_file_path`。
# 默认参数及原因：DCF 角色默认更低温度，原因是模型推导比文字判断更需要收敛。
shared_tushare_valuation_data_tool = TushareValuationDataTool()


@CrewBase
class ValuationCrew:
    """
    目的：把可比估值、内在价值估值和最终估值汇总拆开，便于分别复核每一层假设。
    功能：集中声明可比公司、现金流估值和估值汇总三类角色及任务。
    实现逻辑：先并行做 peer set 和内在价值推导，最后把两类结果合并成估值结论。
    可调参数：YAML 配置、日志路径、工具列表、模型温度和迭代次数。
    默认参数及原因：默认采用确定性的顺序 Crew，并把前两步交给任务级并行，原因是这样既能保留 DAG，又不需要 manager 二次调度。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = str(PROJECT_ROOT / "logs" / "valuation_crew.json")

    @agent
    def peer_analyst(self) -> Agent:
        """
        目的：集中定义可比公司分析角色。
        功能：为同行池筛选和相对估值注入专题 pack 文本与可比估值工具。
        实现逻辑：只挂载 Tushare 和可比估值工具，不再暴露 registry 或搜索工具。
        可调参数：Agent 配置、工具列表、temperature 和迭代次数。
        默认参数及原因：默认 `temperature=0.5`，原因是可比池构建需要一定展开空间。
        """

        return Agent(
            config=self.agents_config["peer_analyst"],  # type: ignore[index]
            tools=[
                shared_tushare_valuation_data_tool,
                ComparableValuationTool(),
            ],
            llm=get_heavy_llm(temperature=0.5),
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

    @agent
    def cashflow_analyst(self) -> Agent:
        """
        目的：集中定义内在价值分析角色。
        功能：为 DCF 类任务注入专题 pack 文本与内在价值估值工具。
        实现逻辑：只挂载 Tushare 和内在价值工具，保持估值链路最小化。
        可调参数：Agent 配置、工具列表、temperature 和迭代次数。
        默认参数及原因：默认 `temperature=0.1`，原因是现金流建模更需要严谨收敛。
        """

        return Agent(
            config=self.agents_config["cashflow_analyst"],  # type: ignore[index]
            tools=[
                shared_tushare_valuation_data_tool,
                IntrinsicValuationTool(),
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

    @agent
    def valuation_analyst(self) -> Agent:
        """
        目的：集中定义最终估值汇总角色。
        功能：把可比估值和内在价值结果综合成最终估值判断，并生成 football field。
        实现逻辑：只挂载 football field 工具，强制第三步只做汇总、解释和表达，不再重跑上游估值。
        可调参数：Agent 配置、工具列表、temperature 和迭代次数。
        默认参数及原因：默认 `temperature=0.5`，原因是汇总阶段需要一定判断展开空间。
        """

        return Agent(
            config=self.agents_config["valuation_analyst"],  # type: ignore[index]
            tools=[FootballFieldTool()],
            llm=get_heavy_llm(temperature=0.5),
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
    def build_peer_set(self) -> Task:
        """
        目的：定义可比公司集合构建任务。
        功能：创建同行筛选和相对估值的起始任务。
        实现逻辑：直接读取 YAML 配置并输出 Markdown。
        可调参数：任务配置和输出格式。
        默认参数及原因：默认异步执行，原因是它可与内在价值任务并行。
        """

        return Task(
            config=self.tasks_config["build_peer_set"],  # type: ignore[index]
            tools=[],
            async_execution=True,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def derive_intrinsic_valuation(self) -> Task:
        """
        目的：定义内在价值推导任务。
        功能：创建 DCF 或简化现金流估值任务。
        实现逻辑：直接读取 YAML 配置并输出 Markdown。
        可调参数：任务配置和输出格式。
        默认参数及原因：默认异步执行，原因是它可与可比估值任务并行。
        """

        return Task(
            config=self.tasks_config["derive_intrinsic_valuation"],  # type: ignore[index]
            tools=[],
            async_execution=True,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def summarize_valuation(self) -> Task:
        """
        目的：把不同估值方法汇总成统一结论。
        功能：创建估值汇总任务，并接入前两步结果作为上下文。
        实现逻辑：直接读取 YAML 配置，并显式依赖可比和内在价值任务。
        可调参数：任务配置和上下文依赖。
        默认参数及原因：默认串行执行，原因是最终估值结论必须同时参考前两步结果。
        """

        return Task(
            config=self.tasks_config["summarize_valuation"],  # type: ignore[index]
            context=[self.build_peer_set(), self.derive_intrinsic_valuation()],
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
        目的：统一返回估值阶段使用的 Crew 实例。
        功能：确保日志目录存在，并构造“前两步并行、第三步串行”的 valuation crew。
        实现逻辑：使用 `Process.sequential` 固定 Crew 级调度，再由任务级 `async_execution` 显式定义并行边界。
        可调参数：日志路径、缓存和 tracing。
        默认参数及原因：默认不再使用 hierarchical manager，原因是当前估值链路已经有明确 DAG，不需要 manager 二次调度。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)
        return Crew(
            name="valuation_crew",
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
        )
