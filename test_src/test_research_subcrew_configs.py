from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from crewai import Process

from automated_research_report_generator.crews.business_crew.business_crew import BusinessCrew
from automated_research_report_generator.crews.due_diligence_crew.due_diligence_crew import (
    DueDiligenceCrew,
)
from automated_research_report_generator.crews.financial_crew.financial_crew import FinancialCrew
from automated_research_report_generator.crews.history_background_crew.history_background_crew import (
    HistoryBackgroundCrew,
)
from automated_research_report_generator.crews.industry_crew.industry_crew import IndustryCrew
from automated_research_report_generator.crews.operating_metrics_crew.operating_metrics_crew import (
    OperatingMetricsCrew,
)
from automated_research_report_generator.crews.peer_info_crew.peer_info_crew import PeerInfoCrew
from automated_research_report_generator.crews.risk_crew.risk_crew import RiskCrew
from automated_research_report_generator.crews.valuation_crew.valuation_crew import ValuationCrew


TOPIC_CREW_CASES = [
    ("history_background_crew", HistoryBackgroundCrew),
    ("industry_crew", IndustryCrew),
    ("business_crew", BusinessCrew),
    ("risk_crew", RiskCrew),
]


@pytest.mark.parametrize(("crew_dir_name", "crew_class"), TOPIC_CREW_CASES)
def test_topic_crews_expose_three_agents_three_tasks_and_period_placeholders(
    crew_dir_name: str,
    crew_class,
) -> None:
    """
    目的：锁定普通专题 crew 仍保持三段式 source-based 结构。
    功能：检查任务数量、工具挂载边界，以及 extract/search 模板里不存在旧的未来期占位符。
    实现逻辑：直接实例化 crew，再读取 `tasks.yaml` 做配置级断言。
    可调参数：专题目录名和 crew 类。
    默认参数及原因：不再约束旧 registry 条目集合，原因是当前项目已经切到 source-based 主链。
    """

    crew_instance = crew_class()
    runtime = crew_instance.crew()
    extract_task = crew_instance.extract_from_pdf()
    search_task = crew_instance.search_public_sources()
    synth_task = crew_instance.synthesize_and_output()

    assert len(runtime.agents) == 3
    assert len(runtime.tasks) == 3
    assert len(crew_instance.agents_config) == 3
    assert len(crew_instance.tasks_config) == 3

    assert {type(tool).__name__ for tool in extract_task.tools} == {
        "ReadPdfPageIndexTool",
        "ReadPdfPagesTool",
    }
    expected_search_tools = set() if crew_dir_name == "risk_crew" else {"SerperDevTool"}
    assert {type(tool).__name__ for tool in search_task.tools} == expected_search_tools
    assert synth_task.tools == []

    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / crew_dir_name
        / "config"
        / "tasks.yaml"
    )
    tasks_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    assert set(tasks_config.keys()) == {
        "extract_from_pdf",
        "search_public_sources",
        "synthesize_and_output",
    }

    extract_output = tasks_config["extract_from_pdf"]["expected_output"]
    search_output = tasks_config["search_public_sources"]["expected_output"]

    assert not re.search(
        r"\{閺堢喖妫縖1-5]\}|\{妫板嫭绁撮張鐒?-5]\}|\{閺堫亝娼甸張鐔兼？[1-5]\}",
        extract_output,
    )
    assert not re.search(
        r"\{閺堢喖妫縖1-5]\}|\{妫板嫭绁撮張鐒?-5]\}|\{閺堫亝娼甸張鐔兼？[1-5]\}",
        search_output,
    )


def test_peer_info_crew_exposes_peer_list_peer_data_and_synthesis_tasks() -> None:
    """
    目的：锁定同行专题维持 peer list、peer data、synth 三段式。
    功能：检查任务名、工具边界和 YAML 配置键，避免回退到通用 extract/search 模板。
    实现逻辑：实例化 `PeerInfoCrew` 并读取其任务配置。
    可调参数：无。
    默认参数及原因：不校验大段 expected_output 细节，原因是这里关注运行接口。
    """

    crew_instance = PeerInfoCrew()
    runtime = crew_instance.crew()
    build_task = crew_instance.build_peer_list()
    collect_task = crew_instance.collect_peer_data()
    synth_task = crew_instance.synthesize_and_output()

    assert len(runtime.agents) == 3
    assert len(runtime.tasks) == 3
    assert len(crew_instance.agents_config) == 3
    assert len(crew_instance.tasks_config) == 3

    assert {type(tool).__name__ for tool in build_task.tools} == {"SerperDevTool"}
    assert {type(tool).__name__ for tool in collect_task.tools} == {"TusharePeerDataTool"}
    assert synth_task.tools == []

    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "peer_info_crew"
        / "config"
        / "tasks.yaml"
    )
    tasks_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    assert set(tasks_config.keys()) == {
        "build_peer_list",
        "collect_peer_data",
        "synthesize_and_output",
    }


def test_financial_crew_exposes_four_step_runtime() -> None:
    """
    目的：锁定财务专题已经升级为抽取、计算、分析、汇总四段式。
    功能：检查 agent 数量、task 数量、工具边界和 YAML 配置键。
    实现逻辑：实例化 `FinancialCrew`，分别读取四个任务对象和配置文件做断言。
    可调参数：无。
    默认参数及原因：不校验大段表格正文，原因是这里主要保护运行接口与工具边界。
    """

    crew_instance = FinancialCrew()
    runtime = crew_instance.crew()
    extract_task = crew_instance.extract_financial_data()
    compute_task = crew_instance.compute_financial_metrics()
    analysis_task = crew_instance.analyze_financial_performance()
    synth_task = crew_instance.synthesize_and_output()

    assert len(runtime.agents) == 4
    assert len(runtime.tasks) == 4
    assert len(crew_instance.agents_config) == 4
    assert len(crew_instance.tasks_config) == 4

    assert {type(tool).__name__ for tool in extract_task.tools} == {
        "ReadPdfPageIndexTool",
        "ReadPdfPagesTool",
    }
    assert {type(tool).__name__ for tool in compute_task.tools} == {
        "ReadPdfPageIndexTool",
        "ReadPdfPagesTool",
        "FinancialMetricsCalculatorTool",
    }
    assert {type(tool).__name__ for tool in analysis_task.tools} == {
        "ReadPdfPageIndexTool",
        "ReadPdfPagesTool",
    }
    assert synth_task.tools == []

    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "financial_crew"
        / "config"
        / "tasks.yaml"
    )
    tasks_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    assert set(tasks_config.keys()) == {
        "extract_financial_data",
        "compute_financial_metrics",
        "analyze_financial_performance",
        "synthesize_and_output",
    }

    extract_output = tasks_config["extract_financial_data"]["expected_output"]
    assert not re.search(
        r"\{閺堢喖妫縖1-5]\}|\{妫板嫭绁撮張鐒?-5]\}|\{閺堫亝娼甸張鐔兼？[1-5]\}",
        extract_output,
    )


def test_operating_metrics_crew_exposes_four_step_runtime() -> None:
    """
    目的：锁定运营指标专题已经升级为抽取、搜索、分析、汇总四段式。
    功能：检查 agent 数量、task 数量、工具边界和 YAML 配置键。
    实现逻辑：实例化 `OperatingMetricsCrew`，分别读取四个任务对象和配置文件做断言。
    可调参数：无。
    默认参数及原因：不校验大段正文细节，原因是这里主要保护运行接口与工具边界。
    """

    crew_instance = OperatingMetricsCrew()
    runtime = crew_instance.crew()
    extract_task = crew_instance.extract_from_pdf()
    search_task = crew_instance.search_public_sources()
    analysis_task = crew_instance.analyze_operating_metrics()
    synth_task = crew_instance.synthesize_and_output()

    assert len(runtime.agents) == 4
    assert len(runtime.tasks) == 4
    assert len(crew_instance.agents_config) == 4
    assert len(crew_instance.tasks_config) == 4

    assert {type(tool).__name__ for tool in extract_task.tools} == {
        "ReadPdfPageIndexTool",
        "ReadPdfPagesTool",
        "SerperDevTool",
    }
    assert {type(tool).__name__ for tool in search_task.tools} == {"SerperDevTool"}
    assert {type(tool).__name__ for tool in analysis_task.tools} == {
        "ReadPdfPageIndexTool",
        "ReadPdfPagesTool",
    }
    assert synth_task.tools == []

    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "operating_metrics_crew"
        / "config"
        / "tasks.yaml"
    )
    tasks_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    assert set(tasks_config.keys()) == {
        "extract_from_pdf",
        "search_public_sources",
        "analyze_operating_metrics",
        "synthesize_and_output",
    }

    extract_output = tasks_config["extract_from_pdf"]["expected_output"]
    search_output = tasks_config["search_public_sources"]["expected_output"]

    assert not re.search(
        r"\{閺堢喖妫縖1-5]\}|\{妫板嫭绁撮張鐒?-5]\}|\{閺堫亝娼甸張鐔兼？[1-5]\}",
        extract_output,
    )
    assert not re.search(
        r"\{閺堢喖妫縖1-5]\}|\{妫板嫭绁撮張鐒?-5]\}|\{閺堫亝娼甸張鐔兼？[1-5]\}",
        search_output,
    )


def test_due_diligence_crew_uses_seven_packs_plus_risk_intermediate_only() -> None:
    """
    目的：锁定尽调专题已经收缩为“7 个 pack + 1 份风险跨专题分析中间结果”的输入边界。
    功能：检查 due diligence crew 的单任务结构，以及 `tasks.yaml` 中保留和删除的占位符集合。
    实现逻辑：实例化 `DueDiligenceCrew` 后读取配置文件，逐项断言关键输入字段是否符合极简方案。
    可调参数：无。
    默认参数及原因：只检查接口级占位符，不校验整段 prompt 文案细节，原因是这里主要保护输入边界不回退。
    """

    crew_instance = DueDiligenceCrew()
    runtime = crew_instance.crew()
    task_instance = crew_instance.generate_diligence_questions()

    assert len(runtime.agents) == 1
    assert len(runtime.tasks) == 1
    assert len(crew_instance.agents_config) == 1
    assert len(crew_instance.tasks_config) == 1
    assert task_instance.tools == []

    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "due_diligence_crew"
        / "config"
        / "tasks.yaml"
    )
    tasks_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    assert set(tasks_config.keys()) == {"generate_diligence_questions"}

    description = tasks_config["generate_diligence_questions"]["description"]

    for placeholder in [
        "{history_background_pack_text}",
        "{industry_pack_text}",
        "{business_pack_text}",
        "{peer_info_pack_text}",
        "{finance_pack_text}",
        "{operating_metrics_pack_text}",
        "{risk_pack_text}",
        "{risk_search_source_text}",
        "{diligence_output_path}",
    ]:
        assert placeholder in description

    for placeholder in [
        "{history_background_file_source_text}",
        "{history_background_search_source_text}",
        "{industry_file_source_text}",
        "{industry_search_source_text}",
        "{business_file_source_text}",
        "{business_search_source_text}",
        "{peer_info_peer_list_source_text}",
        "{peer_info_peer_data_source_text}",
        "{finance_file_source_text}",
        "{finance_computed_metrics_text}",
        "{finance_analysis_text}",
        "{operating_metrics_file_source_text}",
        "{operating_metrics_search_source_text}",
        "{risk_file_source_text}",
    ]:
        assert placeholder not in description


def test_valuation_crew_uses_deterministic_parallel_then_summarize_structure() -> None:
    """
    目的：锁定估值专题已经改成“前两步并行、第三步汇总”的确定性三步流水线。
    功能：检查 agent 工具归属、Crew process、任务异步边界，以及 `tasks.yaml` 中按任务收窄后的输入占位符。
    实现逻辑：实例化 `ValuationCrew` 后读取运行时对象和配置文件，分别做结构级和 prompt 边界断言。
    可调参数：无。
    默认参数及原因：不校验整段估值正文细节，原因是这里主要保护结构、工具边界和输入接口。
    """

    crew_instance = ValuationCrew()
    runtime = crew_instance.crew()
    peer_task = crew_instance.build_peer_set()
    intrinsic_task = crew_instance.derive_intrinsic_valuation()
    summarize_task = crew_instance.summarize_valuation()

    assert len(runtime.agents) == 3
    assert len(runtime.tasks) == 3
    assert len(crew_instance.agents_config) == 3
    assert len(crew_instance.tasks_config) == 3
    assert runtime.process == Process.sequential

    peer_agent = crew_instance.peer_analyst()
    cashflow_agent = crew_instance.cashflow_analyst()
    valuation_agent = crew_instance.valuation_analyst()

    assert {type(tool).__name__ for tool in peer_agent.tools} == {
        "TushareValuationDataTool",
        "ComparableValuationTool",
    }
    assert {type(tool).__name__ for tool in cashflow_agent.tools} == {
        "TushareValuationDataTool",
        "IntrinsicValuationTool",
    }
    assert {type(tool).__name__ for tool in valuation_agent.tools} == {
        "FootballFieldTool",
    }

    assert peer_task.async_execution is True
    assert intrinsic_task.async_execution is True
    assert summarize_task.async_execution is False
    assert len(summarize_task.context) == 2

    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "valuation_crew"
        / "config"
        / "tasks.yaml"
    )
    tasks_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    assert set(tasks_config.keys()) == {
        "build_peer_set",
        "derive_intrinsic_valuation",
        "summarize_valuation",
    }

    peer_description = tasks_config["build_peer_set"]["description"]
    intrinsic_description = tasks_config["derive_intrinsic_valuation"]["description"]
    summarize_description = tasks_config["summarize_valuation"]["description"]

    assert "{peer_info_peer_data_source_text}" in peer_description
    assert "{peer_info_peer_list_source_text}" not in peer_description
    assert "{finance_file_source_text}" not in peer_description
    assert "{operating_metrics_file_source_text}" not in peer_description
    assert "{operating_metrics_search_source_text}" not in peer_description
    assert "{risk_file_source_text}" not in peer_description
    assert "{risk_search_source_text}" in peer_description

    assert "{finance_pack_text}" in intrinsic_description
    assert "{operating_metrics_pack_text}" in intrinsic_description
    assert "{risk_pack_text}" in intrinsic_description
    assert "{risk_search_source_text}" in intrinsic_description
    assert "{peer_info_pack_text}" not in intrinsic_description
    assert "{peer_info_peer_list_source_text}" not in intrinsic_description
    assert "{peer_info_peer_data_source_text}" not in intrinsic_description
    assert "{finance_file_source_text}" not in intrinsic_description
    assert "{operating_metrics_file_source_text}" not in intrinsic_description
    assert "{operating_metrics_search_source_text}" not in intrinsic_description
    assert "{risk_file_source_text}" not in intrinsic_description

    assert "`build_peer_set`" in summarize_description
    assert "`derive_intrinsic_valuation`" in summarize_description
    assert "{risk_pack_text}" in summarize_description
    assert "{risk_search_source_text}" in summarize_description
    assert "{peer_info_peer_data_source_text}" not in summarize_description
    assert "{finance_file_source_text}" not in summarize_description
    assert "{operating_metrics_file_source_text}" not in summarize_description
    assert "{operating_metrics_search_source_text}" not in summarize_description
    assert "{risk_file_source_text}" not in summarize_description
