from __future__ import annotations

import json
from pathlib import Path

import pytest

import automated_research_report_generator.flow.research_flow as research_flow_module

from automated_research_report_generator.flow.research_flow import (
    ANALYSIS_STAGE_COMPLETED_EVENT,
    ResearchReportFlow,
)


def _build_flow(tmp_path: Path) -> ResearchReportFlow:
    """
    目的：给 flow 编排测试提供一份最小可运行的 Flow 状态。
    功能：填充分析、估值和最终报告阶段真正会读取的基础路径和公共输入字段。
    实现逻辑：直接实例化 `ResearchReportFlow`，再把临时目录路径写入 state。
    可调参数：`tmp_path`，用于隔离测试产物。
    默认参数及原因：只初始化当前测试必需字段，原因是这里关注的是 flow 编排而不是 PDF 预处理。
    """

    flow = ResearchReportFlow()
    run_cache_dir = tmp_path / ".cache" / "test-run" / "md"
    run_cache_dir.mkdir(parents=True, exist_ok=True)

    flow.state.run_slug = "test-run"
    flow.state.company_name = "Test Co"
    flow.state.industry = "Automation"
    flow.state.run_cache_dir = run_cache_dir.as_posix()
    flow.state.run_output_dir = run_cache_dir.as_posix()
    flow.state.pdf_file_path = (tmp_path / "sample.pdf").as_posix()
    flow.state.page_index_file_path = (tmp_path / "page_index.json").as_posix()
    flow.state.document_metadata_file_path = (tmp_path / "document_metadata.json").as_posix()
    flow.state.final_report_markdown_path = (run_cache_dir / "report.md").as_posix()
    flow.state.final_report_pdf_path = (run_cache_dir / "report.pdf").as_posix()
    flow.state.pitch_material_markdown_path = (run_cache_dir / "pitch.md").as_posix()
    flow.state.investment_snapshot_ppt_path = (run_cache_dir / "snapshot.pptx").as_posix()
    Path(flow.state.pdf_file_path).write_text("pdf placeholder", encoding="utf-8")
    Path(flow.state.page_index_file_path).write_text("{}", encoding="utf-8")
    Path(flow.state.document_metadata_file_path).write_text("metadata", encoding="utf-8")
    return flow


def _write_document_metadata_with_periods(
    flow: ResearchReportFlow,
    *,
    current_period: str = "2025H1A",
) -> None:
    """
    目的：为期间表头守卫测试补一份最小可读的 metadata 文件。
    功能：写入 Flow 当前 run 需要的 `periods` 字典，让 `_current_period_label()` 能稳定读取当前期标签。
    实现逻辑：直接覆盖 `state.document_metadata_file_path` 指向的 JSON 文件，只保留测试必需字段。
    可调参数：`flow` 和 `current_period`。
    默认参数及原因：默认当前期使用 `2025H1A`，原因是这正是本轮故障的核心样本。
    """

    Path(flow.state.document_metadata_file_path).write_text(
        json.dumps(
            {
                "company_name": flow.state.company_name,
                "industry": flow.state.industry,
                "periods": {
                    "{FQ0/FY0}": current_period,
                    "{FQ-1}": "2024H1A",
                    "{FY-1}": "2024A",
                    "{FY-2}": "2023A",
                    "{FY-3}": "2022A",
                    "{FY1}": "2026E",
                    "{FY2}": "2027E",
                    "{FY3}": "",
                    "{FY4}": "",
                    "{FY5}": "",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _build_fake_analysis_crew_class(
    *,
    crew_name: str,
    pack_name: str,
    pack_title: str,
    events: list[tuple[str, dict[str, str]]],
):
    """
    目的：为 flow 编排测试生成不依赖真实 LLM 的专题 crew 替身类。
    功能：模拟专题 crew 的 pack 元数据和 `crew().kickoff(...)` 行为，并写出中间文件。
    实现逻辑：在 kickoff 时记录输入，再按输入键判断应该写哪类文件。
    可调参数：crew 名、pack 名、pack 标题和事件记录列表。
    默认参数及原因：输出内容固定包含 crew 名，原因是便于断言顺序和下游输入。
    """

    class FakeAnalysisCrew:
        """
        目的：承接单个专题 crew 的最小替身实现。
        功能：暴露 flow 需要的 pack 元数据，并模拟 source / intermediate / pack 落盘。
        实现逻辑：把元数据挂到实例属性上，再在 kickoff 时根据输入写出文本文件。
        可调参数：由外层工厂函数注入。
        默认参数及原因：日志路径默认 `None`，原因是测试里由 flow 再次注入。
        """

        def __init__(self) -> None:
            """
            目的：初始化 flow 运行时需要读取的 crew 元数据。
            功能：填充专题名、pack 名、标题和日志路径占位。
            实现逻辑：直接把工厂参数写入实例属性。
            可调参数：无。
            默认参数及原因：日志路径默认空，原因是测试不关心日志落盘。
            """

            self.output_log_file_path = None
            self.crew_name = crew_name
            self.pack_name = pack_name
            self.pack_title = pack_title

        def crew(self):
            """
            目的：返回满足 flow 调用方式的最小 runner。
            功能：暴露 `kickoff(inputs=...)` 接口给 flow 使用。
            实现逻辑：使用闭包捕获当前实例，在 kickoff 时记录输入和写文件。
            可调参数：无。
            默认参数及原因：只实现 kickoff，原因是当前测试只覆盖 flow 的编排路径。
            """

            owner = self

            class Runner:
                """
                目的：模拟 CrewAI runner 的 kickoff 接口。
                功能：记录输入，并把专题产物写到目标路径。
                实现逻辑：先追加事件，再按输入字段组合写出不同阶段文件。
                可调参数：无。
                默认参数及原因：输出固定为简短 Markdown，原因是下游只需要能读到文本。
                """

                def kickoff(self, inputs: dict[str, str]) -> None:
                    """
                    目的：模拟真实 crew 的执行入口。
                    功能：记录输入并生成 source、中间产物和 pack 文件。
                    实现逻辑：根据输入键判断是同行专题、财务专题还是普通专题。
                    可调参数：`inputs`。
                    默认参数及原因：输出文本直接包含 crew 名，原因是便于断言下游接线。
                    """

                    events.append((owner.crew_name, inputs))
                    if "peer_list_source_output_path" in inputs and "peer_data_source_output_path" in inputs:
                        Path(inputs["peer_list_source_output_path"]).write_text(
                            f"# {owner.pack_title} Peer List Source\n## ENTRY TEST_{owner.crew_name}_PEER_LIST\n- 输出内容：peer list source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                        Path(inputs["peer_data_source_output_path"]).write_text(
                            f"# {owner.pack_title} Peer Data Source\n## ENTRY TEST_{owner.crew_name}_PEER_DATA\n- 输出内容：peer data source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                    elif "finance_computed_metrics_output_path" in inputs:
                        Path(inputs["file_source_output_path"]).write_text(
                            f"# {owner.pack_title} File Source\n## ENTRY TEST_{owner.crew_name}_FILE\n- 输出内容：file source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                        Path(inputs["search_source_output_path"]).write_text(
                            f"# {owner.pack_title} Computed Metrics\n## ENTRY TEST_{owner.crew_name}_COMPUTE\n- 输出内容：computed metrics by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                        Path(inputs["finance_analysis_output_path"]).write_text(
                            f"# {owner.pack_title} Analysis\n## ENTRY TEST_{owner.crew_name}_ANALYSIS\n- 输出内容：analysis by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                    elif "operating_metrics_analysis_output_path" in inputs:
                        Path(inputs["file_source_output_path"]).write_text(
                            f"# {owner.pack_title} File Source\n## ENTRY TEST_{owner.crew_name}_FILE\n- 输出内容：file source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                        Path(inputs["search_source_output_path"]).write_text(
                            f"# {owner.pack_title} Search Source\n## ENTRY TEST_{owner.crew_name}_SEARCH\n- 输出内容：search source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                        Path(inputs["operating_metrics_analysis_output_path"]).write_text(
                            f"# {owner.pack_title} Analysis\n## ENTRY TEST_{owner.crew_name}_ANALYSIS\n- 输出内容：analysis by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                    elif "file_source_output_path" in inputs and "search_source_output_path" in inputs:
                        Path(inputs["file_source_output_path"]).write_text(
                            f"# {owner.pack_title} File Source\n## ENTRY TEST_{owner.crew_name}_FILE\n- 输出内容：file source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                        Path(inputs["search_source_output_path"]).write_text(
                            f"# {owner.pack_title} Search Source\n## ENTRY TEST_{owner.crew_name}_SEARCH\n- 输出内容：search source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                    else:
                        Path(inputs["file_source_output_path"]).write_text(
                            f"# {owner.pack_title} File Source\n## ENTRY TEST_{owner.crew_name}_FILE\n- 输出内容：file source by {owner.crew_name}\n",
                            encoding="utf-8",
                        )
                    Path(inputs["pack_output_path"]).write_text(
                        f"# {owner.pack_title}\nproduced by {owner.crew_name}\n",
                        encoding="utf-8",
                    )

            return Runner()

    return FakeAnalysisCrew


def test_run_analysis_stage_runs_seven_topic_crews_and_due_diligence(tmp_path, monkeypatch) -> None:
    """
    目的：锁定 analysis 阶段会按顺序运行 7 个专题 crew，并把 source、专题中间产物和 diligence 输入全部接好。
    功能：检查执行顺序、关键路径回写，以及财务和运营指标专题的额外中间产物接线。
    实现逻辑：用假专题 crew 和假 diligence crew 替换真实对象，再直接执行 `_run_analysis_stage()`。
    可调参数：`tmp_path` 和 `monkeypatch`。
    默认参数及原因：不启动真实 LLM，原因是这里关注编排链路。
    """

    flow = _build_flow(tmp_path)
    analysis_events: list[tuple[str, dict[str, str]]] = []
    diligence_inputs: dict[str, str] = {}
    checkpoint_codes: list[str] = []

    fake_specs = [
        ("HistoryBackgroundCrew", "history_background_crew", "history_background_pack", "History"),
        ("IndustryCrew", "industry_crew", "industry_pack", "Industry"),
        ("BusinessCrew", "business_crew", "business_pack", "Business"),
        ("PeerInfoCrew", "peer_info_crew", "peer_info_pack", "Peers"),
        ("FinancialCrew", "financial_crew", "finance_pack", "Finance"),
        ("OperatingMetricsCrew", "operating_metrics_crew", "operating_metrics_pack", "Metrics"),
        ("RiskCrew", "risk_crew", "risk_pack", "Risk"),
    ]
    for class_name, crew_name, pack_name, pack_title in fake_specs:
        monkeypatch.setattr(
            research_flow_module,
            class_name,
            _build_fake_analysis_crew_class(
                crew_name=crew_name,
                pack_name=pack_name,
                pack_title=pack_title,
                events=analysis_events,
            ),
        )

    class FakeDueDiligenceCrew:
        """
        目的：模拟 analysis 阶段末尾的 diligence crew。
        功能：接收上游 pack 和 source 文本，并写出尽调问题文件。
        实现逻辑：在 kickoff 时记录输入并落盘。
        可调参数：无。
        默认参数及原因：输出固定简短文本，原因是这里只验证接线。
        """

        def __init__(self) -> None:
            self.output_log_file_path = None

        def crew(self):
            class Runner:
                def kickoff(self, inputs: dict[str, str]) -> None:
                    diligence_inputs.update(inputs)
                    Path(inputs["diligence_output_path"]).write_text(
                        "# Diligence Questions\n- question\n",
                        encoding="utf-8",
                    )

            return Runner()

    monkeypatch.setattr(research_flow_module, "DueDiligenceCrew", FakeDueDiligenceCrew)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(flow, "_write_manifest_from_state", lambda status: status)
    monkeypatch.setattr(
        flow,
        "_write_checkpoint",
        lambda checkpoint_code, payload: checkpoint_codes.append(checkpoint_code) or "checkpoint.json",
    )
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")

    analysis_dir = Path(flow._run_analysis_stage())
    source_dir = analysis_dir / "sources"

    assert [event[0] for event in analysis_events] == [
        "history_background_crew",
        "industry_crew",
        "business_crew",
        "peer_info_crew",
        "financial_crew",
        "operating_metrics_crew",
        "risk_crew",
    ]
    assert checkpoint_codes == [
        "cp03a_history_background_pack",
        "cp03b_industry_pack",
        "cp03c_business_pack",
        "cp03d_peer_info_pack",
        "cp03e_finance_pack",
        "cp03f_operating_metrics_pack",
        "cp03g_risk_pack",
        "cp03h_diligence_questions",
    ]

    assert flow.state.analysis_source_dir == source_dir.as_posix()
    assert flow.state.peer_info_peer_list_source_path == (source_dir / "04_peer_info_peer_list.md").as_posix()
    assert flow.state.peer_info_peer_data_source_path == (source_dir / "04_peer_info_peer_data.md").as_posix()
    assert flow.state.finance_file_source_path == (source_dir / "05_finance_file_source.md").as_posix()
    assert flow.state.finance_computed_metrics_path == (source_dir / "05_finance_computed_metrics.md").as_posix()
    assert flow.state.finance_analysis_path == (analysis_dir / "05_finance_analysis.md").as_posix()
    assert flow.state.operating_metrics_file_source_path == (source_dir / "06_operating_metrics_file_source.md").as_posix()
    assert flow.state.operating_metrics_search_source_path == (source_dir / "06_operating_metrics_search_source.md").as_posix()
    assert flow.state.operating_metrics_analysis_path == (analysis_dir / "06_operating_metrics_analysis.md").as_posix()
    assert flow.state.risk_file_source_path == (source_dir / "07_risk_file_source.md").as_posix()
    assert flow.state.risk_search_source_path == (source_dir / "07_risk_search_source.md").as_posix()

    assert "ENTRY TEST_peer_info_crew_PEER_LIST" in Path(flow.state.peer_info_peer_list_source_path).read_text(encoding="utf-8")
    assert "ENTRY TEST_peer_info_crew_PEER_DATA" in Path(flow.state.peer_info_peer_data_source_path).read_text(encoding="utf-8")
    assert "ENTRY TEST_financial_crew_FILE" in Path(flow.state.finance_file_source_path).read_text(encoding="utf-8")
    assert "ENTRY TEST_financial_crew_COMPUTE" in Path(flow.state.finance_computed_metrics_path).read_text(encoding="utf-8")
    assert "ENTRY TEST_financial_crew_ANALYSIS" in Path(flow.state.finance_analysis_path).read_text(encoding="utf-8")
    assert "ENTRY TEST_operating_metrics_crew_FILE" in Path(flow.state.operating_metrics_file_source_path).read_text(encoding="utf-8")
    assert "ENTRY TEST_operating_metrics_crew_SEARCH" in Path(flow.state.operating_metrics_search_source_path).read_text(encoding="utf-8")
    assert "ENTRY TEST_operating_metrics_crew_ANALYSIS" in Path(flow.state.operating_metrics_analysis_path).read_text(encoding="utf-8")

    finance_inputs = dict(next(event for event in analysis_events if event[0] == "financial_crew")[1])
    assert "TEST_peer_info_crew_PEER_DATA" in finance_inputs["peer_info_peer_data_source_text"]
    assert "industry_crew" in finance_inputs["industry_pack_text"]
    assert "business_crew" in finance_inputs["business_pack_text"]
    assert finance_inputs["finance_computed_metrics_output_path"].endswith("05_finance_analysis.md") is False
    assert finance_inputs["finance_analysis_output_path"].endswith("05_finance_analysis.md")

    operating_metrics_inputs = dict(
        next(event for event in analysis_events if event[0] == "operating_metrics_crew")[1]
    )
    assert "industry_crew" in operating_metrics_inputs["industry_pack_text"]
    assert "business_crew" in operating_metrics_inputs["business_pack_text"]
    assert "TEST_peer_info_crew_PEER_LIST" in operating_metrics_inputs["peer_info_peer_list_source_text"]
    assert operating_metrics_inputs["operating_metrics_analysis_output_path"].endswith(
        "06_operating_metrics_analysis.md"
    )

    risk_inputs = dict(next(event for event in analysis_events if event[0] == "risk_crew")[1])
    assert "history_background_crew" in risk_inputs["history_background_pack_text"]
    assert "business_crew" in risk_inputs["business_pack_text"]
    assert "industry_crew" in risk_inputs["industry_pack_text"]
    assert "financial_crew" in risk_inputs["finance_pack_text"]
    assert "operating_metrics_crew" in risk_inputs["operating_metrics_pack_text"]

    assert "financial_crew" in diligence_inputs["finance_pack_text"]
    assert "risk_crew" in diligence_inputs["risk_pack_text"]
    assert "TEST_risk_crew_SEARCH" in diligence_inputs["risk_search_source_text"]

    assert "history_background_file_source_text" not in diligence_inputs
    assert "history_background_search_source_text" not in diligence_inputs
    assert "industry_file_source_text" not in diligence_inputs
    assert "industry_search_source_text" not in diligence_inputs
    assert "business_file_source_text" not in diligence_inputs
    assert "business_search_source_text" not in diligence_inputs
    assert "peer_info_peer_list_source_text" not in diligence_inputs
    assert "peer_info_peer_data_source_text" not in diligence_inputs
    assert "finance_file_source_text" not in diligence_inputs
    assert "finance_computed_metrics_text" not in diligence_inputs
    assert "finance_analysis_text" not in diligence_inputs
    assert "operating_metrics_file_source_text" not in diligence_inputs
    assert "operating_metrics_search_source_text" not in diligence_inputs
    assert "risk_file_source_text" not in diligence_inputs


def test_run_valuation_stage_uses_updated_finance_inputs(tmp_path, monkeypatch) -> None:
    """
    目的：锁定 valuation 阶段仍能消费 finance pack 和 finance file source。
    功能：检查估值阶段关键输入文本和输出路径。
    实现逻辑：预写 pack/source 文件，再用假 valuation crew 承接 `_run_valuation_stage()`。
    可调参数：`tmp_path` 和 `monkeypatch`。
    默认参数及原因：不启动真实估值 agent，原因是这里只验证 flow 接口。
    """

    flow = _build_flow(tmp_path)
    research_dir = Path(flow.state.run_cache_dir) / "research" / "iter_01"
    source_dir = research_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)

    file_map = {
        "peer_info_pack_path": research_dir / "04_peer_info_pack.md",
        "finance_pack_path": research_dir / "05_finance_pack.md",
        "operating_metrics_pack_path": research_dir / "06_operating_metrics_pack.md",
        "risk_pack_path": research_dir / "07_risk_pack.md",
        "peer_info_peer_list_source_path": source_dir / "04_peer_info_peer_list.md",
        "peer_info_peer_data_source_path": source_dir / "04_peer_info_peer_data.md",
        "finance_file_source_path": source_dir / "05_finance_file_source.md",
        "operating_metrics_file_source_path": source_dir / "06_operating_metrics_file_source.md",
        "operating_metrics_search_source_path": source_dir / "06_operating_metrics_search_source.md",
        "risk_file_source_path": source_dir / "07_risk_file_source.md",
        "risk_search_source_path": source_dir / "07_risk_search_source.md",
    }
    for attr, path in file_map.items():
        setattr(flow.state, attr, path.as_posix())
        path.write_text(f"{attr} content", encoding="utf-8")

    valuation_inputs: dict[str, str] = {}
    checkpoint_codes: list[str] = []

    class FakeValuationCrew:
        """
        目的：模拟 valuation crew。
        功能：接收 pack 和 source 文本，并写出三份估值产物。
        实现逻辑：记录输入后把结果写入估值目录。
        可调参数：无。
        默认参数及原因：输出固定文本，原因是这里只验证输入边界和路径回写。
        """

        def __init__(self) -> None:
            self.output_log_file_path = None

        def crew(self):
            class Runner:
                def kickoff(self, inputs: dict[str, str]) -> None:
                    valuation_inputs.update(inputs)
                    output_dir = Path(inputs["valuation_output_dir"])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    (output_dir / "01_peers_pack.md").write_text("# peers\n", encoding="utf-8")
                    (output_dir / "02_intrinsic_value_pack.md").write_text("# intrinsic\n", encoding="utf-8")
                    (output_dir / "03_valuation_pack.md").write_text("# valuation\n", encoding="utf-8")

            return Runner()

    monkeypatch.setattr(research_flow_module, "ValuationCrew", FakeValuationCrew)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(flow, "_write_manifest_from_state", lambda status: status)
    monkeypatch.setattr(
        flow,
        "_write_checkpoint",
        lambda checkpoint_code, payload: checkpoint_codes.append(checkpoint_code) or "checkpoint.json",
    )
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")

    flow._run_valuation_stage()

    assert valuation_inputs["peer_info_peer_data_source_text"] == "peer_info_peer_data_source_path content"
    assert valuation_inputs["peer_info_pack_text"] == "peer_info_pack_path content"
    assert valuation_inputs["finance_pack_text"] == "finance_pack_path content"
    assert valuation_inputs["operating_metrics_pack_text"] == "operating_metrics_pack_path content"
    assert valuation_inputs["risk_pack_text"] == "risk_pack_path content"
    assert valuation_inputs["risk_search_source_text"] == "risk_search_source_path content"
    assert "peer_info_peer_list_source_text" not in valuation_inputs
    assert "finance_file_source_text" not in valuation_inputs
    assert "operating_metrics_file_source_text" not in valuation_inputs
    assert "operating_metrics_search_source_text" not in valuation_inputs
    assert "risk_file_source_text" not in valuation_inputs
    assert checkpoint_codes == ["cp04_valuation"]


def test_run_thesis_stage_uses_selected_source_inputs_and_preserves_outputs(tmp_path, monkeypatch) -> None:
    """
    目的：锁定 thesis 阶段会注入选定的 source/supporting texts，而不是回退到只读 pack。
    功能：检查 bull/neutral/bear/synthesizer 共同使用的输入边界、排除项，以及四份 thesis 产物路径回写。
    实现逻辑：预写 pack、估值、尽调和 source 文件，再用假 thesis crew 承接 `_run_thesis_stage()`。
    可调参数：`tmp_path` 和 `monkeypatch`。
    默认参数及原因：不启动真实 LLM，原因是这里关注的是 Flow 接线稳定性。
    """

    flow = _build_flow(tmp_path)
    kickoff_inputs: dict[str, str] = {}
    checkpoint_codes: list[str] = []

    selected_inputs = {
        "history_background_pack_path": "# history pack\n",
        "industry_pack_path": "# industry pack\n",
        "business_pack_path": "# business pack\n",
        "peer_info_pack_path": "# peer pack\n",
        "finance_pack_path": "# finance pack\n",
        "operating_metrics_pack_path": "# metrics pack\n",
        "risk_pack_path": "# risk pack\n",
        "peers_pack_path": "# peers valuation\n",
        "intrinsic_value_pack_path": "# intrinsic valuation\n",
        "valuation_pack_path": "# valuation pack\n",
        "diligence_questions_path": "# diligence\n",
        "history_background_file_source_path": "# history file source\n",
        "industry_file_source_path": "# industry file source\n",
        "industry_search_source_path": "# industry search source\n",
        "business_file_source_path": "# business file source\n",
        "peer_info_peer_data_source_path": "# peer data source\n",
        "finance_file_source_path": "# finance file source\n",
        "finance_computed_metrics_path": "# finance computed metrics\n",
        "operating_metrics_file_source_path": "# metrics file source\n",
        "risk_file_source_path": "# risk file source\n",
        "risk_search_source_path": "# risk search source\n",
    }
    for attr, content in selected_inputs.items():
        path = tmp_path / f"{attr}.md"
        path.write_text(content, encoding="utf-8")
        setattr(flow.state, attr, path.as_posix())

    excluded_inputs = {
        "history_background_search_source_path": "# should stay out\n",
        "business_search_source_path": "# should stay out\n",
        "peer_info_peer_list_source_path": "# should stay out\n",
        "finance_analysis_path": "# should stay out\n",
        "operating_metrics_search_source_path": "# should stay out\n",
    }
    for attr, content in excluded_inputs.items():
        path = tmp_path / f"{attr}.md"
        path.write_text(content, encoding="utf-8")
        setattr(flow.state, attr, path.as_posix())

    class FakeInvestmentThesisCrew:
        """
        目的：模拟 thesis 阶段的最小 crew 接口。
        功能：接收 Flow 注入的 pack/source 文本，并写出四份 thesis 产物。
        实现逻辑：在 kickoff 时记录输入，再按固定文件名落盘占位产物。
        可调参数：无。
        默认参数及原因：输出固定简短文本，原因是这里只验证输入边界与路径回写。
        """

        def __init__(self) -> None:
            self.output_log_file_path = None

        def crew(self):
            class Runner:
                def kickoff(self, inputs: dict[str, str]) -> None:
                    kickoff_inputs.update(inputs)
                    output_dir = Path(inputs["thesis_output_dir"])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    (output_dir / "01_bull_thesis.md").write_text("# bull\n", encoding="utf-8")
                    (output_dir / "02_neutral_thesis.md").write_text("# neutral\n", encoding="utf-8")
                    (output_dir / "03_bear_thesis.md").write_text("# bear\n", encoding="utf-8")
                    (output_dir / "04_investment_thesis.md").write_text("# thesis\n", encoding="utf-8")

            return Runner()

    monkeypatch.setattr(research_flow_module, "InvestmentThesisCrew", FakeInvestmentThesisCrew)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(flow, "_write_manifest_from_state", lambda status: status)
    monkeypatch.setattr(
        flow,
        "_write_checkpoint",
        lambda checkpoint_code, payload: checkpoint_codes.append(checkpoint_code) or "checkpoint.json",
    )
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")

    result = flow._run_thesis_stage()

    assert result.endswith("04_investment_thesis.md")
    assert kickoff_inputs["history_background_pack_text"] == "# history pack\n"
    assert kickoff_inputs["industry_pack_text"] == "# industry pack\n"
    assert kickoff_inputs["business_pack_text"] == "# business pack\n"
    assert kickoff_inputs["peer_info_pack_text"] == "# peer pack\n"
    assert kickoff_inputs["finance_pack_text"] == "# finance pack\n"
    assert kickoff_inputs["operating_metrics_pack_text"] == "# metrics pack\n"
    assert kickoff_inputs["risk_pack_text"] == "# risk pack\n"
    assert kickoff_inputs["peers_pack_text"] == "# peers valuation\n"
    assert kickoff_inputs["intrinsic_value_pack_text"] == "# intrinsic valuation\n"
    assert kickoff_inputs["valuation_pack_text"] == "# valuation pack\n"
    assert kickoff_inputs["diligence_questions_text"] == "# diligence\n"
    assert kickoff_inputs["history_background_file_source_text"] == "# history file source\n"
    assert kickoff_inputs["industry_file_source_text"] == "# industry file source\n"
    assert kickoff_inputs["industry_search_source_text"] == "# industry search source\n"
    assert kickoff_inputs["business_file_source_text"] == "# business file source\n"
    assert kickoff_inputs["peer_info_peer_data_source_text"] == "# peer data source\n"
    assert kickoff_inputs["finance_file_source_text"] == "# finance file source\n"
    assert kickoff_inputs["finance_computed_metrics_text"] == "# finance computed metrics\n"
    assert kickoff_inputs["operating_metrics_file_source_text"] == "# metrics file source\n"
    assert kickoff_inputs["risk_file_source_text"] == "# risk file source\n"
    assert kickoff_inputs["risk_search_source_text"] == "# risk search source\n"

    assert "history_background_search_source_text" not in kickoff_inputs
    assert "business_search_source_text" not in kickoff_inputs
    assert "peer_info_peer_list_source_text" not in kickoff_inputs
    assert "finance_analysis_text" not in kickoff_inputs
    assert "operating_metrics_search_source_text" not in kickoff_inputs
    assert checkpoint_codes == ["cp05_thesis"]
    assert flow.state.bull_thesis_path.endswith("01_bull_thesis.md")
    assert flow.state.neutral_thesis_path.endswith("02_neutral_thesis.md")
    assert flow.state.bear_thesis_path.endswith("03_bear_thesis.md")
    assert flow.state.investment_thesis_path.endswith("04_investment_thesis.md")


def test_final_report_markdown_includes_finance_source_appendix(tmp_path) -> None:
    """
    目的：锁定最终报告附录仍会包含财务 file source 正文。
    功能：检查最终报告能嵌入 finance file source 的 entry_id。
    实现逻辑：手动写入各专题路径后调用 `_build_final_report_markdown()`。
    可调参数：`tmp_path`。
    默认参数及原因：只验证关键财务专题附录，原因是当前改动重点在 financial crew。
    """

    flow = _build_flow(tmp_path)
    flow.state.investment_thesis_path = (tmp_path / "thesis.md").as_posix()
    flow.state.diligence_questions_path = (tmp_path / "diligence.md").as_posix()
    flow.state.history_background_pack_path = (tmp_path / "01_history.md").as_posix()
    flow.state.industry_pack_path = (tmp_path / "02_industry.md").as_posix()
    flow.state.business_pack_path = (tmp_path / "03_business.md").as_posix()
    flow.state.peer_info_pack_path = (tmp_path / "04_peer.md").as_posix()
    flow.state.finance_pack_path = (tmp_path / "05_finance.md").as_posix()
    flow.state.operating_metrics_pack_path = (tmp_path / "06_metrics.md").as_posix()
    flow.state.risk_pack_path = (tmp_path / "07_risk.md").as_posix()
    flow.state.peers_pack_path = (tmp_path / "08_peers.md").as_posix()
    flow.state.intrinsic_value_pack_path = (tmp_path / "09_intrinsic.md").as_posix()
    flow.state.valuation_pack_path = (tmp_path / "10_valuation.md").as_posix()

    for path in [
        flow.state.investment_thesis_path,
        flow.state.diligence_questions_path,
        flow.state.history_background_pack_path,
        flow.state.industry_pack_path,
        flow.state.business_pack_path,
        flow.state.peer_info_pack_path,
        flow.state.finance_pack_path,
        flow.state.operating_metrics_pack_path,
        flow.state.risk_pack_path,
        flow.state.peers_pack_path,
        flow.state.intrinsic_value_pack_path,
        flow.state.valuation_pack_path,
    ]:
        Path(path).write_text(f"# {Path(path).stem}\ncontent\n", encoding="utf-8")

    source_values = {
        "history_background_file_source_path": "# History File Source\n## ENTRY F_HIS_001\n",
        "history_background_search_source_path": "# History Search Source\n## ENTRY F_HIS_002\n",
        "industry_file_source_path": "# Industry File Source\n## ENTRY F_IND_001\n",
        "industry_search_source_path": "# Industry Search Source\n## ENTRY F_IND_002\n",
        "business_file_source_path": "# Business File Source\n## ENTRY F_BUS_001\n",
        "business_search_source_path": "# Business Search Source\n## ENTRY F_BUS_002\n",
        "peer_info_peer_list_source_path": "# Peer Peer List Source\n## ENTRY F_PEER_001\n",
        "peer_info_peer_data_source_path": "# Peer Peer Data Source\n## ENTRY F_PEER_002\n",
        "finance_file_source_path": "# Finance File Source\n## ENTRY F_FIN_001\n",
        "finance_computed_metrics_path": "# Finance Computed Metrics\n## ENTRY F_FIN_COMPUTE_001\n",
        "finance_analysis_path": "# Finance Analysis\n## ENTRY F_FIN_ANALYSIS_001\n",
        "operating_metrics_file_source_path": "# Metrics File Source\n## ENTRY F_OPM_001\n",
        "operating_metrics_search_source_path": "# Metrics Search Source\n## ENTRY F_OPM_002\n",
        "risk_file_source_path": "# Risk File Source\n## ENTRY F_RISK_001\n",
        "risk_search_source_path": "# Risk Search Source\n## ENTRY F_RISK_002\n",
    }
    for attr, content in source_values.items():
        path = tmp_path / f"{attr}.md"
        path.write_text(content, encoding="utf-8")
        setattr(flow.state, attr, path.as_posix())

    markdown = flow._build_final_report_markdown()

    assert "ENTRY F_FIN_001" in markdown
    assert "Finance File Source" in markdown
    assert "registry_snapshot" not in markdown


def test_run_analysis_phase_returns_current_event_name(tmp_path, monkeypatch) -> None:
    """
    目的：锁定 analysis 路由层继续返回当前阶段完成事件。
    功能：确认 `run_analysis_phase()` 仍只负责触发 `_run_analysis_stage()` 并返回事件名。
    实现逻辑：替换 `_run_analysis_stage()` 后直接调用路由方法。
    可调参数：`tmp_path` 和 `monkeypatch`。
    默认参数及原因：只测路由值，原因是具体执行链由其它测试覆盖。
    """

    flow = _build_flow(tmp_path)
    monkeypatch.setattr(flow, "_run_analysis_stage", lambda: "analysis")

    assert flow.run_analysis_phase() == ANALYSIS_STAGE_COMPLETED_EVENT


def test_publish_if_passed_passes_writeup_inputs_and_records_new_artifacts(tmp_path, monkeypatch) -> None:
    """
    目的：锁定 publish 阶段会把 pitch、snapshot 所需文本和路径完整传给 writeup crew。
    功能：检查新增的 pack/thesis 文本输入、两个新产物路径，以及 cp06 checkpoint 记录没有漏项。
    实现逻辑：用假 writeup crew 承接 kickoff，再直接调用 `publish_if_passed()` 做编排断言。
    可调参数：`tmp_path` 与 `monkeypatch`。
    默认参数及原因：不启动真实 LLM 和工具，原因是这里验证的是 Flow 接线而不是内容生成质量。
    """

    flow = _build_flow(tmp_path)
    kickoff_inputs: dict[str, str] = {}
    checkpoint_payloads: dict[str, dict[str, str]] = {}

    text_inputs = {
        "investment_thesis_path": "# thesis\nthesis text\n",
        "history_background_pack_path": "# history\nhistory pack\n",
        "industry_pack_path": "# industry\nindustry pack\n",
        "business_pack_path": "# business\nbusiness pack\n",
        "peer_info_pack_path": "# peer\npeer pack\n",
        "finance_pack_path": "# finance\nfinance pack\n",
        "operating_metrics_pack_path": "# metrics\nmetrics pack\n",
        "risk_pack_path": "# risk\nrisk pack\n",
        "valuation_pack_path": "# valuation\nvaluation pack\n",
    }
    for attr, content in text_inputs.items():
        path = tmp_path / f"{attr}.md"
        path.write_text(content, encoding="utf-8")
        setattr(flow.state, attr, path.as_posix())

    class FakeWriteupCrew:
        """
        目的：模拟 writeup 阶段的最小 crew 接口。
        功能：接收 Flow 拼好的输入，并写出四个阶段产物占位文件。
        实现逻辑：在 kickoff 时记录 inputs，再按约定路径落地假文件。
        可调参数：无。
        默认参数及原因：输出内容使用固定占位文本，原因是这里只验证编排和路径。
        """

        def __init__(self) -> None:
            self.output_log_file_path = None

        def crew(self):
            class Runner:
                def kickoff(self, inputs: dict[str, str]) -> None:
                    kickoff_inputs.update(inputs)
                    Path(inputs["final_report_markdown_path"]).write_text("# report\n", encoding="utf-8")
                    Path(inputs["pitch_material_markdown_path"]).write_text("# pitch\n", encoding="utf-8")
                    Path(inputs["investment_snapshot_ppt_path"]).write_bytes(b"PPT")
                    Path(inputs["final_report_pdf_path"]).write_bytes(b"PDF")

            return Runner()

    monkeypatch.setattr(research_flow_module, "WriteupCrew", FakeWriteupCrew)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(flow, "_configure_crew_log", lambda crew, log_path: crew)
    monkeypatch.setattr(flow, "_extract_writeup_tool_error_message", lambda: "")
    monkeypatch.setattr(flow, "_write_final_report_markdown", lambda: Path(flow.state.final_report_markdown_path).write_text("# report\n", encoding="utf-8"))
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")
    monkeypatch.setattr(
        flow,
        "_write_checkpoint",
        lambda checkpoint_code, payload: checkpoint_payloads.setdefault(checkpoint_code, payload) or "checkpoint.json",
    )

    result = flow.publish_if_passed()

    assert result == flow.state.final_report_pdf_path
    assert kickoff_inputs["pitch_material_markdown_path"] == flow.state.pitch_material_markdown_path
    assert kickoff_inputs["investment_snapshot_ppt_path"] == flow.state.investment_snapshot_ppt_path
    assert kickoff_inputs["investment_thesis_text"] == "# thesis\nthesis text\n"
    assert kickoff_inputs["history_background_pack_text"] == "# history\nhistory pack\n"
    assert kickoff_inputs["industry_pack_text"] == "# industry\nindustry pack\n"
    assert kickoff_inputs["business_pack_text"] == "# business\nbusiness pack\n"
    assert kickoff_inputs["finance_pack_text"] == "# finance\nfinance pack\n"
    assert kickoff_inputs["valuation_pack_text"] == "# valuation\nvaluation pack\n"
    assert kickoff_inputs["risk_pack_text"] == "# risk\nrisk pack\n"
    assert Path(flow.state.pitch_material_markdown_path).exists()
    assert Path(flow.state.investment_snapshot_ppt_path).exists()
    assert checkpoint_payloads["cp06_writeup"]["pitch_material_markdown_path"] == flow.state.pitch_material_markdown_path
    assert checkpoint_payloads["cp06_writeup"]["investment_snapshot_ppt_path"] == flow.state.investment_snapshot_ppt_path


def test_publish_if_passed_fails_when_snapshot_artifact_is_missing(tmp_path, monkeypatch) -> None:
    """
    目的：锁定 writeup 阶段缺少 PPT 实物时会直接失败，而不是继续把 run 标记为 completed。
    功能：检查 `publish_if_passed()` 会把缺失的 snapshot 产物升级成 writeup 阶段异常。
    实现逻辑：让假 writeup crew 只写 Markdown、pitch 和 PDF，不生成 PPT，再断言 flow 失败状态。
    可调参数：`tmp_path` 与 `monkeypatch`。
    默认参数及原因：不启动真实 agent，原因是这里验证的是主流程成功条件收紧逻辑。
    """

    flow = _build_flow(tmp_path)

    text_inputs = {
        "investment_thesis_path": "# thesis\nthesis text\n",
        "history_background_pack_path": "# history\nhistory pack\n",
        "industry_pack_path": "# industry\nindustry pack\n",
        "business_pack_path": "# business\nbusiness pack\n",
        "peer_info_pack_path": "# peer\npeer pack\n",
        "finance_pack_path": "# finance\nfinance pack\n",
        "operating_metrics_pack_path": "# metrics\nmetrics pack\n",
        "risk_pack_path": "# risk\nrisk pack\n",
        "valuation_pack_path": "# valuation\nvaluation pack\n",
    }
    for attr, content in text_inputs.items():
        path = tmp_path / f"{attr}.md"
        path.write_text(content, encoding="utf-8")
        setattr(flow.state, attr, path.as_posix())

    class FakeWriteupCrew:
        """
        目的：模拟缺失 snapshot 产物的 writeup crew。
        功能：只生成其余产物，故意留空 PPT 文件。
        实现逻辑：在 kickoff 时只写 report、pitch 和 PDF。
        可调参数：无。
        默认参数及原因：故意不写 snapshot，原因是要覆盖真实故障路径。
        """

        def __init__(self) -> None:
            self.output_log_file_path = None

        def crew(self):
            class Runner:
                def kickoff(self, inputs: dict[str, str]) -> None:
                    Path(inputs["final_report_markdown_path"]).write_text("# report\n", encoding="utf-8")
                    Path(inputs["pitch_material_markdown_path"]).write_text("# pitch\n", encoding="utf-8")
                    Path(inputs["final_report_pdf_path"]).write_bytes(b"PDF")

            return Runner()

    monkeypatch.setattr(research_flow_module, "WriteupCrew", FakeWriteupCrew)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(flow, "_configure_crew_log", lambda crew, log_path: crew)
    monkeypatch.setattr(
        flow,
        "_write_final_report_markdown",
        lambda: Path(flow.state.final_report_markdown_path).write_text("# report\n", encoding="utf-8"),
    )
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")
    monkeypatch.setattr(flow, "_write_manifest_from_state", lambda status: status)
    monkeypatch.setattr(flow, "_write_checkpoint", lambda checkpoint_code, payload: "checkpoint.json")

    with pytest.raises(RuntimeError) as exc_info:
        flow.publish_if_passed()

    error_message = str(exc_info.value)
    assert "Missing writeup artifacts" in error_message
    assert "investment_snapshot_ppt_path" in error_message
    assert flow.state.failed_stage == "writeup"
    assert flow.state.failed_crew == "writeup_crew"
    assert "Missing writeup artifacts" in flow.state.error_message


def test_publish_if_passed_fails_when_writeup_log_contains_tool_error(tmp_path, monkeypatch) -> None:
    """
    目的：锁定即使产物文件已写出，只要 writeup 日志记录了工具错误，主流程也不能算成功。
    功能：检查 `Error executing tool:` 会被提升为 writeup 阶段异常。
    实现逻辑：让假 writeup crew 同时写出四个产物和一条工具错误日志，再断言 flow 失败状态。
    可调参数：`tmp_path` 与 `monkeypatch`。
    默认参数及原因：保留全部产物文件，原因是要单独证明日志错误本身就足以阻止伪成功。
    """

    flow = _build_flow(tmp_path)

    text_inputs = {
        "investment_thesis_path": "# thesis\nthesis text\n",
        "history_background_pack_path": "# history\nhistory pack\n",
        "industry_pack_path": "# industry\nindustry pack\n",
        "business_pack_path": "# business\nbusiness pack\n",
        "peer_info_pack_path": "# peer\npeer pack\n",
        "finance_pack_path": "# finance\nfinance pack\n",
        "operating_metrics_pack_path": "# metrics\nmetrics pack\n",
        "risk_pack_path": "# risk\nrisk pack\n",
        "valuation_pack_path": "# valuation\nvaluation pack\n",
    }
    for attr, content in text_inputs.items():
        path = tmp_path / f"{attr}.md"
        path.write_text(content, encoding="utf-8")
        setattr(flow.state, attr, path.as_posix())

    class FakeWriteupCrew:
        """
        目的：模拟带有工具错误日志的 writeup crew。
        功能：写出所有产物，同时在 crew 日志里留下 `Error executing tool:`。
        实现逻辑：利用 flow 注入的 `output_log_file_path` 落日志，再正常生成占位产物。
        可调参数：无。
        默认参数及原因：同时生成产物与错误日志，原因是要覆盖“有文件但仍应失败”的分支。
        """

        def __init__(self) -> None:
            self.output_log_file_path = None

        def crew(self):
            owner = self

            class Runner:
                def kickoff(self, inputs: dict[str, str]) -> None:
                    Path(inputs["final_report_markdown_path"]).write_text("# report\n", encoding="utf-8")
                    Path(inputs["pitch_material_markdown_path"]).write_text("# pitch\n", encoding="utf-8")
                    Path(inputs["investment_snapshot_ppt_path"]).write_bytes(b"PPT")
                    Path(inputs["final_report_pdf_path"]).write_bytes(b"PDF")
                    Path(owner.output_log_file_path).write_text(
                        '2026-04-16 17:05:58: output="Error executing tool: snapshot tool failed."\n',
                        encoding="utf-8",
                    )

            return Runner()

    monkeypatch.setattr(research_flow_module, "WriteupCrew", FakeWriteupCrew)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(
        flow,
        "_write_final_report_markdown",
        lambda: Path(flow.state.final_report_markdown_path).write_text("# report\n", encoding="utf-8"),
    )
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")
    monkeypatch.setattr(flow, "_write_manifest_from_state", lambda status: status)
    monkeypatch.setattr(flow, "_write_checkpoint", lambda checkpoint_code, payload: "checkpoint.json")

    with pytest.raises(RuntimeError) as exc_info:
        flow.publish_if_passed()

    error_message = str(exc_info.value)
    assert "Writeup crew reported tool execution error" in error_message
    assert "Error executing tool:" in error_message
    assert flow.state.failed_stage == "writeup"
    assert flow.state.failed_crew == "writeup_crew"
    assert "Error executing tool:" in flow.state.error_message


def test_validate_final_report_period_headers_accepts_valid_current_period_columns(tmp_path) -> None:
    """
    目的：锁住最终 Markdown 在期间列全部合法时不会被守卫误杀。
    功能：验证经营指标和财务章节只要都保留实际当前期列名，就能通过校验。
    实现逻辑：写入带 `2025H1A` 的最小 metadata 和最小报告样例，再直接调用校验函数。
    可调参数：`tmp_path`。
    默认参数及原因：默认用最小两张表，原因是这里关注表头契约而不是正文长度。
    """

    flow = _build_flow(tmp_path)
    _write_document_metadata_with_periods(flow, current_period="2025H1A")

    report_text = """# Report
## 6. 经营指标分析
| 指标名称 | 2022A | 2023A | 2024A | 2025H1A |
| --- | --- | --- | --- | --- |
| 销量 | 1 | 2 | 3 | 4 |

## 7. 财务分析
| 指标名称 | 2022A | 2023A | 2024A | 2025H1A |
| --- | --- | --- | --- | --- |
| 营业收入 | 1 | 2 | 3 | 4 |
"""

    flow._validate_final_report_period_headers(report_text)


@pytest.mark.parametrize(
    ("invalid_header", "expected_fragment"),
    [
        ("| 指标名称 | 2022A | 2023A | 2024A | {FQ0/FY0} |", "{FQ0/FY0}"),
        ("| 指标名称 | FY-2022 | 2023A | 2024A | 2025H1A |", "FY-2022"),
        ("| 指标名称 | 2022A | 2023A | 2024A | FQ0/2025H1 |", "FQ0/2025H1"),
        ("| 指标名称 | 2022A | 2023A | 2024A | FY0 |", "FY0"),
    ],
)
def test_validate_final_report_period_headers_rejects_invalid_labels(
    tmp_path,
    invalid_header: str,
    expected_fragment: str,
) -> None:
    """
    目的：锁住最终 Markdown 中的非法期间列标签会被立即拦截。
    功能：覆盖未替换占位符、FY 年份混合标签、FQ0/实际期间混合标签和孤立 FY0。
    实现逻辑：构造最小报告，并把非法表头同时放进经营指标和财务章节后断言抛错。
    可调参数：临时目录、非法表头和期望命中片段。
    默认参数及原因：默认两节共用同一非法表头，原因是这里关注全文守卫而不是章节差异。
    """

    flow = _build_flow(tmp_path)
    _write_document_metadata_with_periods(flow, current_period="2025H1A")

    report_text = f"""# Report
## 6. 经营指标分析
{invalid_header}
| --- | --- | --- | --- | --- |
| 销量 | 1 | 2 | 3 | 4 |

## 7. 财务分析
{invalid_header}
| --- | --- | --- | --- | --- |
| 营业收入 | 1 | 2 | 3 | 4 |
"""

    with pytest.raises(RuntimeError) as exc_info:
        flow._validate_final_report_period_headers(report_text)

    assert expected_fragment in str(exc_info.value)


def test_validate_final_report_period_headers_rejects_missing_current_period_column(tmp_path) -> None:
    """
    目的：锁住 metadata 已识别出当前期时，最终报告不能静默丢掉当前期列。
    功能：验证经营指标和财务章节都缺少 `2025H1A` 列时会直接失败。
    实现逻辑：使用合法但不含当前期的表头生成最小报告，再断言缺列异常。
    可调参数：`tmp_path`。
    默认参数及原因：默认只保留三年历史列，原因是这正是本轮 finance pack 的真实坏样子。
    """

    flow = _build_flow(tmp_path)
    _write_document_metadata_with_periods(flow, current_period="2025H1A")

    report_text = """# Report
## 6. 经营指标分析
| 指标名称 | 2022A | 2023A | 2024A |
| --- | --- | --- | --- |
| 销量 | 1 | 2 | 3 |

## 7. 财务分析
| 指标名称 | 2022A | 2023A | 2024A |
| --- | --- | --- | --- |
| 营业收入 | 1 | 2 | 3 |
"""

    with pytest.raises(RuntimeError) as exc_info:
        flow._validate_final_report_period_headers(report_text)

    assert "2025H1A" in str(exc_info.value)
    assert "经营指标分析" in str(exc_info.value)
    assert "财务分析" in str(exc_info.value)


def test_write_manifest_from_state_records_pitch_and_snapshot_paths(tmp_path) -> None:
    """
    目的：锁定 run manifest 已记录新增的 pitch markdown 和 snapshot ppt 路径。
    功能：检查 manifest 文件中的新字段名和值都与 state 保持一致。
    实现逻辑：构建最小 flow state，调用真实 `_write_manifest_from_state()` 后读取 JSON 断言。
    可调参数：`tmp_path`。
    默认参数及原因：只关心 manifest 新字段，原因是其他字段已由现有测试覆盖。
    """

    flow = _build_flow(tmp_path)
    flow.state.analysis_source_dir = (tmp_path / "analysis" / "sources").as_posix()
    Path(flow.state.analysis_source_dir).mkdir(parents=True, exist_ok=True)

    manifest_path = flow._write_manifest_from_state("completed")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert manifest["pitch_material_markdown_path"] == flow.state.pitch_material_markdown_path
    assert manifest["investment_snapshot_ppt_path"] == flow.state.investment_snapshot_ppt_path
