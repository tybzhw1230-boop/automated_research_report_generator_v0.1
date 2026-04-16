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
from automated_research_report_generator.crews.investment_thesis_crew.investment_thesis_crew import (
    InvestmentThesisCrew,
)
from automated_research_report_generator.crews.operating_metrics_crew.operating_metrics_crew import (
    OperatingMetricsCrew,
)
from automated_research_report_generator.crews.peer_info_crew.peer_info_crew import PeerInfoCrew
from automated_research_report_generator.crews.risk_crew.risk_crew import RiskCrew
from automated_research_report_generator.crews.valuation_crew.valuation_crew import ValuationCrew
from automated_research_report_generator.crews.writeup_crew.writeup_crew import WriteupCrew


TOPIC_CREW_CASES = [
    ("history_background_crew", HistoryBackgroundCrew),
    ("industry_crew", IndustryCrew),
    ("business_crew", BusinessCrew),
    ("risk_crew", RiskCrew),
]


def _task_interpolation_inputs() -> dict[str, str]:
    """
    目的：为 task 模板插值测试提供一组稳定且完整的公共输入。
    功能：覆盖各专题 crew 常见的路径、上游文本和期间占位符，便于直接调用 CrewAI 的插值逻辑。
    实现逻辑：返回一份最小但足够完整的字符串字典，避免测试因缺少无关输入而失败。
    可调参数：当前无显式参数，固定返回回归场景所需的样例值。
    默认参数及原因：当前期默认写为 `2025H1A`，原因是本轮故障就集中在半年期标签替换。
    """

    return {
        "company_name": "测试公司",
        "industry": "测试行业",
        "pdf_file_path": "sample.pdf",
        "page_index_file_path": "sample_page_index.json",
        "document_metadata_file_path": "sample_document_metadata.json",
        "analysis_source_dir": "analysis/sources",
        "file_source_output_path": "outputs/file_source.md",
        "search_source_output_path": "outputs/search_source.md",
        "pack_output_path": "outputs/pack.md",
        "peer_list_source_output_path": "outputs/peer_list.md",
        "peer_data_source_output_path": "outputs/peer_data.md",
        "finance_computed_metrics_output_path": "outputs/finance_computed.md",
        "finance_analysis_output_path": "outputs/finance_analysis.md",
        "operating_metrics_analysis_output_path": "outputs/operating_metrics_analysis.md",
        "peer_info_peer_data_source_text": "# peer data\n",
        "peer_info_peer_list_source_text": "# peer list\n",
        "industry_pack_text": "# industry pack\n",
        "business_pack_text": "# business pack\n",
        "FY-3": "2022A",
        "FY-2": "2023A",
        "FY-1": "2024A",
        "FQ-1": "2024H1A",
        "FQ0/FY0": "2025H1A",
        "FQ0_OR_FY0": "2025H1A",
        "FY1": "2026E",
        "FY2": "2027E",
        "FY3": "2028E",
        "FY4": "2029E",
        "FY5": "2030E",
    }


def _interpolate_task_template(task) -> str:
    """
    目的：统一触发 CrewAI task 模板的真实插值逻辑。
    功能：把 description 和 expected_output 渲染成最终 prompt 文本，供期间占位回归测试复用。
    实现逻辑：调用 `interpolate_inputs_and_add_conversation_history()`，再拼接插值后的 description 与 expected_output。
    可调参数：`task` 为任意 CrewAI Task 对象。
    默认参数及原因：固定使用 `_task_interpolation_inputs()`，原因是这里关注模板渲染结果而不是调用方差异。
    """

    task.interpolate_inputs_and_add_conversation_history(_task_interpolation_inputs())
    return f"{task.description}\n{task.expected_output}"


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
    extract_description = tasks_config["extract_from_pdf"]["description"]

    assert "先按固定主题枚举筛页，再按需读取相关页面。" in extract_description

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
    extract_description = tasks_config["extract_financial_data"]["description"]
    assert "先按固定主题枚举筛页，再按需读取相关页面。" in extract_description
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
    extract_description = tasks_config["extract_from_pdf"]["description"]

    assert "先按固定主题枚举筛页，再按需读取相关页面。" in extract_description

    assert not re.search(
        r"\{閺堢喖妫縖1-5]\}|\{妫板嫭绁撮張鐒?-5]\}|\{閺堫亝娼甸張鐔兼？[1-5]\}",
        extract_output,
    )
    assert not re.search(
        r"\{閺堢喖妫縖1-5]\}|\{妫板嫭绁撮張鐒?-5]\}|\{閺堫亝娼甸張鐔兼？[1-5]\}",
        search_output,
    )


def test_period_placeholders_interpolate_to_current_period_alias_in_runtime_tasks() -> None:
    """
    目的：锁住 CrewAI 运行时插值后不再残留旧的 `{FQ0/FY0}` 占位符。
    功能：覆盖 finance、operating metrics 和 peer info 三类任务，验证最终 prompt 会落成真实期间标签。
    实现逻辑：分别实例化任务对象，直接调用 CrewAI 的插值方法，再检查渲染结果。
    可调参数：无。
    默认参数及原因：当前期固定为 `2025H1A`，原因是这是本轮回归要保护的真实失败样本。
    """

    financial_text = _interpolate_task_template(FinancialCrew().extract_financial_data())
    operating_extract_text = _interpolate_task_template(OperatingMetricsCrew().extract_from_pdf())
    operating_search_text = _interpolate_task_template(OperatingMetricsCrew().search_public_sources())
    peer_text = _interpolate_task_template(PeerInfoCrew().collect_peer_data())

    assert "{FQ0/FY0}" not in financial_text
    assert "2025H1A" in financial_text

    assert "{FQ0/FY0}" not in operating_extract_text
    assert "{FQ0/FY0}" not in operating_search_text
    assert "2025H1A" in operating_extract_text
    assert "2025H1A" in operating_search_text
    assert "FY-3 | FY-2 | FY-1 | FQ0/FY0" not in operating_extract_text
    assert "| 指标名称 | 指标口径/定义 | 2022A | 2023A | 2024A | 2025H1A |" in operating_extract_text
    assert "| 指标名称 | 公司名称 | 2023A | 2024A | 2025H1A |" in operating_search_text

    assert "{FQ0/FY0}" not in peer_text
    assert "2025H1A" in peer_text


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


def test_writeup_crew_exposes_three_agents_four_tasks_and_tool_boundaries() -> None:
    """
    目的：锁定 writeup crew 已扩展为 report、pitch、snapshot 三个 agent 和四个 task。
    功能：检查运行时结构、工具边界，以及新 task 的固定 context 映射没有回退。
    实现逻辑：实例化 `WriteupCrew`，再读取其 `agents.yaml` 和 `tasks.yaml` 做运行时与配置级双重断言。
    可调参数：无。
    默认参数及原因：只校验结构和接口，不校验 prompt 全文，原因是这里关注的是执行接线稳定性。
    """

    crew_instance = WriteupCrew()
    runtime = crew_instance.crew()

    assert len(runtime.agents) == 3
    assert len(runtime.tasks) == 4
    assert len(crew_instance.agents_config) == 3
    assert len(crew_instance.tasks_config) == 4
    assert runtime.process == Process.sequential

    assert crew_instance.report_editor().tools == []
    assert crew_instance.pitch_material_writer().tools == []
    assert {type(tool).__name__ for tool in crew_instance.investment_snapshot_slide_writer().tools} == {
        "InvestmentSnapshotPptTool",
    }

    assert crew_instance.compile_report().tools == []
    assert crew_instance.create_pitch_material().tools == []
    assert {type(tool).__name__ for tool in crew_instance.create_investment_snapshot_ppt().tools} == {
        "InvestmentSnapshotPptTool",
    }
    assert {type(tool).__name__ for tool in crew_instance.export_final_report().tools} == {
        "MarkdownToPdfTool",
    }

    config_root = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "writeup_crew"
        / "config"
    )
    agents_config = yaml.safe_load((config_root / "agents.yaml").read_text(encoding="utf-8")) or {}
    tasks_config = yaml.safe_load((config_root / "tasks.yaml").read_text(encoding="utf-8")) or {}

    assert set(agents_config.keys()) == {
        "report_editor",
        "pitch_material_writer",
        "investment_snapshot_slide_writer",
    }
    assert set(tasks_config.keys()) == {
        "compile_report",
        "create_pitch_material",
        "create_investment_snapshot_ppt",
        "export_final_report",
    }
    assert "context" not in tasks_config["create_pitch_material"]
    assert "context" not in tasks_config["create_investment_snapshot_ppt"]

    pitch_description = tasks_config["create_pitch_material"]["description"]
    snapshot_description = tasks_config["create_investment_snapshot_ppt"]["description"]

    for placeholder in [
        "investment_thesis_text",
        "history_background_pack_text",
        "industry_pack_text",
        "business_pack_text",
        "finance_pack_text",
    ]:
        assert placeholder in pitch_description

    for placeholder in [
        "industry_pack_text",
        "business_pack_text",
        "finance_pack_text",
        "investment_thesis_text",
        "risk_pack_text",
    ]:
        assert placeholder in snapshot_description
    assert "{pitch_material_markdown_path}" in tasks_config["create_pitch_material"]["output_file"]
    assert "{investment_snapshot_ppt_path}" in tasks_config["create_investment_snapshot_ppt"]["description"]


def test_investment_thesis_crew_uses_fact_first_prompt_and_selected_source_inputs() -> None:
    """
    目的：锁定 thesis crew 的输入边界和 prompt 已收口到“事实先行、商业判断收束”。
    功能：检查运行时结构、配置键、关键 source 占位符，以及禁止 ENTRY 编号和空泛资本市场腔的硬约束。
    实现逻辑：实例化 `InvestmentThesisCrew`，再读取 `agents.yaml` 和 `tasks.yaml` 做运行时与配置级断言。
    可调参数：无。
    默认参数及原因：只校验结构和关键 prompt 约束，不校验全文逐句一致，原因是这里关注的是行为边界。
    """

    crew_instance = InvestmentThesisCrew()
    runtime = crew_instance.crew()

    assert len(runtime.agents) == 4
    assert len(runtime.tasks) == 4
    assert len(crew_instance.agents_config) == 4
    assert len(crew_instance.tasks_config) == 4
    assert runtime.process == Process.sequential

    config_root = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "investment_thesis_crew"
        / "config"
    )
    agents_config = yaml.safe_load((config_root / "agents.yaml").read_text(encoding="utf-8")) or {}
    tasks_config = yaml.safe_load((config_root / "tasks.yaml").read_text(encoding="utf-8")) or {}

    assert set(agents_config.keys()) == {
        "bull_agent",
        "neutral_agent",
        "bear_agent",
        "thesis_synthesizer",
    }
    assert set(tasks_config.keys()) == {
        "build_bull_case",
        "build_neutral_case",
        "build_bear_case",
        "synthesize_final_investment_case",
    }

    for agent_name in ["bull_agent", "neutral_agent", "bear_agent", "thesis_synthesizer"]:
        backstory = agents_config[agent_name]["backstory"]
        assert "先复原" in backstory
        assert "不要输出 ENTRY 编号" in backstory
        assert "首次出现必须括号解释" in backstory

    bull_description = tasks_config["build_bull_case"]["description"]
    neutral_description = tasks_config["build_neutral_case"]["description"]
    bear_description = tasks_config["build_bear_case"]["description"]
    synth_description = tasks_config["synthesize_final_investment_case"]["description"]

    for description in [bull_description, neutral_description, bear_description]:
        for placeholder in [
            "{history_background_file_source_text}",
            "{industry_file_source_text}",
            "{industry_search_source_text}",
            "{business_file_source_text}",
            "{peer_info_peer_data_source_text}",
            "{finance_file_source_text}",
            "{finance_computed_metrics_text}",
            "{operating_metrics_file_source_text}",
            "{risk_file_source_text}",
            "{risk_search_source_text}",
        ]:
            assert placeholder in description
        assert "材料名 + 事实 + 商业含义" in description
        assert "禁止输出 `ENTRY F_xxx / D_xxx / S_xxx`" in description
        assert "首次出现必须括号解释" in description
        assert "怎么挣钱" in description
        assert "挣钱能力" in description or "挣得怎么样" in description
        assert "能挣多久" in description or "靠什么持续" in description
        assert "逻辑打断" in description or "哪一环最可能断掉" in description

    assert "投资经理给内部同事的商业判断 memo" in synth_description
    assert "先复原事实，再收束判断" in synth_description
    assert "证据表达继续使用“材料名 + 事实 + 商业含义”" in synth_description
    assert "不要输出 `ENTRY F_xxx / D_xxx / S_xxx`" in synth_description
    assert "主营业务、行业位置、市场地位、竞争优势、当前财务状态" in synth_description
