from __future__ import annotations

from pathlib import Path
from typing import List

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.llm_config import get_heavy_llm

PROJECT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_CREW_LOG_FILE = str(PROJECT_LOG_DIR / "investment_thesis_crew.json")
INVESTMENT_THESIS_AGENT_REASONING = False


@CrewBase
class InvestmentThesisCrew:
    """
    目的：封装 v0.3 thesis 阶段的三视角辩论执行单元。
    功能：让 bull、neutral、bear 三个视角先分别成文，再由 synthesizer 综合共识与分歧。
    实现逻辑：通过 4 个 agent + 4 个 task 顺序执行，并显式隔离前三个立场任务的上下文。
    可调参数：日志路径、模型温度、任务 YAML 配置和输出路径。
    默认参数及原因：默认采用 `Process.sequential`，原因是最终综合必须建立在前三份立场稿已经产出的前提下。
    """

    agents: List[BaseAgent]
    tasks: List[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"
    output_log_file_path: str | bool | None = DEFAULT_CREW_LOG_FILE

    def _build_agent(self, config_name: str, temperature: float) -> Agent:
        """
        目的：统一构造 thesis 阶段的各视角 agent。
        功能：按角色配置和温度返回稳定的 `Agent` 实例。
        实现逻辑：四个 agent 共用同一套运行边界，只在角色设定和温度上略有差异。
        可调参数：`config_name` 和 `temperature`。
        默认参数及原因：默认 `max_iter=18`，原因是 thesis 辩论需要推理，但不应无限展开。
        """

        return Agent(
            config=self.agents_config[config_name],  # type: ignore[index]
            tools=[],
            llm=get_heavy_llm(temperature=temperature),
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
            reasoning=INVESTMENT_THESIS_AGENT_REASONING,
            max_reasoning_attempts=None,
            inject_date=True,
        )

    @agent
    def bull_agent(self) -> Agent:
        """
        目的：定义激进视角 agent。
        功能：从增长潜力和低估可能出发，形成 bullish case。
        实现逻辑：复用统一 agent 构造器，仅调整角色配置和温度。
        可调参数：Agent 配置和温度。
        默认参数及原因：默认 `temperature=0.45`，原因是 bullish case 需要一定展开度。
        """

        return self._build_agent("bull_agent", temperature=0.45)

    @agent
    def neutral_agent(self) -> Agent:
        """
        目的：定义中立视角 agent。
        功能：平衡收益与风险，形成 base case。
        实现逻辑：复用统一 agent 构造器，仅调整角色配置和温度。
        可调参数：Agent 配置和温度。
        默认参数及原因：默认 `temperature=0.3`，原因是中立视角需要更收敛的平衡判断。
        """

        return self._build_agent("neutral_agent", temperature=0.3)

    @agent
    def bear_agent(self) -> Agent:
        """
        目的：定义保守视角 agent。
        功能：从风险、估值过高和证据不足出发，形成 bearish case。
        实现逻辑：复用统一 agent 构造器，仅调整角色配置和温度。
        可调参数：Agent 配置和温度。
        默认参数及原因：默认 `temperature=0.35`，原因是 bearish case 需要展开风险链，但不应过度发散。
        """

        return self._build_agent("bear_agent", temperature=0.35)

    @agent
    def thesis_synthesizer(self) -> Agent:
        """
        目的：定义三视角综合 agent。
        功能：识别 bull、neutral、bear 的共识与分歧，并输出最终 thesis。
        实现逻辑：复用统一 agent 构造器，仅调整角色配置和温度。
        可调参数：Agent 配置和温度。
        默认参数及原因：默认 `temperature=0.25`，原因是综合阶段更强调收束而非继续发散。
        """

        return self._build_agent("thesis_synthesizer", temperature=0.25)

    @task
    def build_bull_case(self) -> Task:
        """
        目的：定义激进视角 task。
        功能：基于上游分析包与估值包输出 bullish thesis 文件。
        实现逻辑：复用 YAML 配置，并显式把 `context=[]` 作为独立分析边界。
        可调参数：任务配置和输出路径。
        默认参数及原因：默认开启 Markdown 输出，原因是该 task 直接产出下游综合要消费的文件。
        """

        return Task(
            config=self.tasks_config["build_bull_case"],  # type: ignore[index]
            context=[],
            tools=[],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def build_neutral_case(self) -> Task:
        """
        目的：定义中立视角 task。
        功能：输出独立的 neutral/base case 文件。
        实现逻辑：复用 YAML 配置，并显式把 `context=[]` 作为独立分析边界。
        可调参数：任务配置和输出路径。
        默认参数及原因：默认开启 Markdown 输出，原因是该 task 直接产出下游综合要消费的文件。
        """

        return Task(
            config=self.tasks_config["build_neutral_case"],  # type: ignore[index]
            context=[],
            tools=[],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def build_bear_case(self) -> Task:
        """
        目的：定义保守视角 task。
        功能：输出独立的 bearish thesis 文件。
        实现逻辑：复用 YAML 配置，并显式把 `context=[]` 作为独立分析边界。
        可调参数：任务配置和输出路径。
        默认参数及原因：默认开启 Markdown 输出，原因是该 task 直接产出下游综合要消费的文件。
        """

        return Task(
            config=self.tasks_config["build_bear_case"],  # type: ignore[index]
            context=[],
            tools=[],
            async_execution=False,
            output_json=None,
            output_pydantic=None,
            human_input=False,
            cache=True,
            markdown=True,
        )

    @task
    def synthesize_final_investment_case(self) -> Task:
        """
        目的：定义最终综合 task。
        功能：基于三份立场稿形成最终 thesis 文件。
        实现逻辑：显式把 bull、neutral、bear 三个 task 作为唯一 context，阻断默认前序上下文注入。
        可调参数：任务配置、上下文和输出路径。
        默认参数及原因：默认依赖前三个 task，原因是最终综合必须建立在明确的立场差异之上。
        """

        return Task(
            config=self.tasks_config["synthesize_final_investment_case"],  # type: ignore[index]
            context=[self.build_bull_case(), self.build_neutral_case(), self.build_bear_case()],
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
        目的：返回 thesis 阶段使用的 Crew 实例。
        功能：保证日志目录存在，并构造顺序执行的四任务 investment_thesis_crew。
        实现逻辑：按 bull -> neutral -> bear -> synthesizer 的固定顺序组织 tasks。
        可调参数：日志路径和缓存配置。
        默认参数及原因：默认采用 `Process.sequential`，原因是最终综合依赖前三个 task 的成文结果。
        """

        if isinstance(self.output_log_file_path, str):
            Path(self.output_log_file_path).parent.mkdir(parents=True, exist_ok=True)
        return Crew(
            name="investment_thesis_crew",
            agents=[
                self.bull_agent(),
                self.neutral_agent(),
                self.bear_agent(),
                self.thesis_synthesizer(),
            ],
            tasks=[
                self.build_bull_case(),
                self.build_neutral_case(),
                self.build_bear_case(),
                self.synthesize_final_investment_case(),
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
