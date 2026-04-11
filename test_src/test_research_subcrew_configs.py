from __future__ import annotations

import inspect
from pathlib import Path

from crewai import Process
import yaml

from automated_research_report_generator.crews.business_crew.business_crew import BusinessCrew
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
from automated_research_report_generator.flow.common import PROJECT_ROOT
from automated_research_report_generator.flow.research_flow import ResearchReportFlow
from automated_research_report_generator.flow.registry import initialize_registry, load_registry_template


def _research_subcrew_instances() -> list[object]:
    """
    目的：集中返回当前 research flow 使用的 7 个 sub-crew 实例。
    功能：避免每个测试重复手写同一组 crew 列表。
    实现逻辑：直接实例化 7 个独立 crew，并交给调用方做后续断言。
    可调参数：当前无。
    默认参数及原因：固定覆盖 flow 主路径中的 7 个 research sub-crew，原因是这正是本轮重构的边界。
    """

    return [
        HistoryBackgroundCrew(),
        IndustryCrew(),
        BusinessCrew(),
        PeerInfoCrew(),
        FinancialCrew(),
        OperatingMetricsCrew(),
        RiskCrew(),
    ]


def test_research_subcrews_have_yaml_agent_and_task_configs():
    """
    目的：锁住 7 个 research sub-crew 的 YAML 配置没有缺口。
    功能：检查每个 sub-crew 都带有独立的 `agents.yaml` 和 `tasks.yaml`。
    实现逻辑：遍历 7 个 crew，按类文件目录定位配置文件并校验关键键位。
    可调参数：当前无。
    默认参数及原因：只检查最关键的 agent 和最终输出 task，原因是这能最快证明配置已接线。
    """

    for crew_instance in _research_subcrew_instances():
        module_dir = Path(inspect.getfile(crew_instance.__class__)).resolve().parent
        agent_config_path = module_dir / "config" / "agents.yaml"
        task_config_path = module_dir / "config" / "tasks.yaml"

        assert agent_config_path.exists()
        assert task_config_path.exists()

        agent_config = yaml.safe_load(agent_config_path.read_text(encoding="utf-8"))
        task_config = yaml.safe_load(task_config_path.read_text(encoding="utf-8"))

        assert str(agent_config["search_fact_agent"]["role"]).strip() == "{pack_title}外部搜索分析师"
        assert task_config["synthesize_and_output"]["output_file"] == "{pack_output_path}"


def test_research_subcrew_can_build_runtime_crew_from_yaml_configs():
    """
    目的：确认独立 crew 文件可以直接构建 CrewAI runtime 对象。
    功能：验证 `BusinessCrew` 在新结构下能正常实例化 agents、tasks 和 crew。
    实现逻辑：构建一个代表性 sub-crew，并断言 process 类型与 agent/task 数量。
    可调参数：当前无。
    默认参数及原因：默认选 `BusinessCrew`，原因是它依赖链较短，足够覆盖主路径。
    """

    runtime_crew = BusinessCrew().crew()

    assert runtime_crew.process in {Process.hierarchical, "hierarchical"}
    assert len(runtime_crew.agents) == 4
    assert len(runtime_crew.tasks) == 4


def test_research_registry_template_is_deterministic_and_covers_all_subcrews():
    """
    目的：锁住 research registry 已从 planner 输出切换为固定模板初始化。
    功能：检查模板条目覆盖全部 7 个 research sub-crew，且不再出现 `planning_crew`。
    实现逻辑：直接加载模板条目，再按 owner_crew 和条目 ID 做集合断言。
    可调参数：当前无。
    默认参数及原因：默认使用测试公司名和行业名，原因是模板里包含占位符替换逻辑。
    """

    entries = load_registry_template("Test Co", "Automation")
    owner_crews = {entry.owner_crew for entry in entries}
    entry_ids = [entry.entry_id for entry in entries]

    assert len(entry_ids) == len(set(entry_ids))
    assert "planning_crew" not in owner_crews
    assert {
        "history_background_crew",
        "industry_crew",
        "business_crew",
        "peer_info_crew",
        "financial_crew",
        "operating_metrics_crew",
        "risk_crew",
    }.issubset(owner_crews)
    assert "D_OPS_001" in entry_ids
    assert any("Test Co" in entry.title for entry in entries)


def test_research_subcrew_synthesize_prompts_enforce_registry_backfill_rules():
    """
    目的：锁定 7 个 research sub-crew 的综合任务都带有统一的 registry 回填约束。
    功能：检查 `synthesize_and_output` prompt 要求按 owner_crew 读取、使用 `update_entry` 回填，并避免重复建条。
    实现逻辑：遍历 7 个 sub-crew 的 `tasks.yaml`，逐个断言关键提示词存在。
    可调参数：当前无。
    默认参数及原因：默认只检验共用核心短语，原因是这些短语正是本次重构想稳定下来的行为边界。
    """

    for crew_instance in _research_subcrew_instances():
        module_dir = Path(inspect.getfile(crew_instance.__class__)).resolve().parent
        task_config_path = module_dir / "config" / "tasks.yaml"
        task_config = yaml.safe_load(task_config_path.read_text(encoding="utf-8"))
        description = task_config["synthesize_and_output"]["description"]

        assert 'owner_crew="{owner_crew}"' in description
        assert 'view="entry_detail"' in description
        assert "update_entry" in description
        assert "F_HIS_001" in description
        assert "不得新建与已有 entry 内容重复的条目" in description


def test_writeup_compile_prompt_requires_full_verbatim_section_insertion():
    """
    目的：锁定 writeup 阶段的 compile_report prompt 必须按章节完整插入上游产物，而不是再次摘要改写。
    功能：检查 `writeup_crew/config/tasks.yaml` 中的描述与 expected_output 同时声明“完整插入”与“不得改写”。
    实现逻辑：直接读取 writeup crew 的 `tasks.yaml`，再断言 research、valuation、thesis 和 QA 占位符都以完整插入方式出现。
    可调参数：当前无。
    默认参数及原因：默认只检查最关键的固定短语，原因是这能稳定覆盖行为边界，同时避免测试对整段 prompt 过度脆弱。
    """

    task_config_path = (
        PROJECT_ROOT
        / "src"
        / "automated_research_report_generator"
        / "crews"
        / "writeup_crew"
        / "config"
        / "tasks.yaml"
    )
    task_config = yaml.safe_load(task_config_path.read_text(encoding="utf-8"))
    description = task_config["compile_report"]["description"]
    expected_output = task_config["compile_report"]["expected_output"]

    assert "每个章节都必须完整插入对应占位符的全部输出内容" in description
    assert "不得总结、压缩、重写或改写任何正文内容" in description
    assert "完整插入 {history_background_pack_text} 的全部输出内容" in expected_output
    assert "完整插入 {valuation_pack_text} 的全部输出内容" in expected_output
    assert "完整插入 {investment_thesis_text} 的全部输出内容" in expected_output
    assert "完整插入 {final_qa_summary} 的全部输出内容" in expected_output


def test_research_subcrew_inputs_include_pack_metadata_and_upstream_pack_text(tmp_path):
    """
    目的：验证 flow 会把 crew YAML 占位符需要的输入补齐。
    功能：检查 peer_info sub-crew 输入同时包含 pack 元信息、责任 crew 和上游 pack 文本。
    实现逻辑：构造最小 flow 状态与临时 pack 文件后，直接调用 `_research_subcrew_inputs()` 断言结果。
    可调参数：`tmp_path`。
    默认参数及原因：默认选择 `peer_info_crew`，原因是它同时依赖 pack 元信息和两个上游 pack。
    """

    flow = ResearchReportFlow()
    flow.state.company_name = "Test Co"
    flow.state.industry = "Automation"
    flow.state.pdf_file_path = (tmp_path / "sample.pdf").as_posix()
    flow.state.page_index_file_path = (tmp_path / "page_index.json").as_posix()
    flow.state.document_metadata_file_path = (tmp_path / "document_metadata.md").as_posix()
    flow.state.run_cache_dir = (tmp_path / ".cache" / "test-run").as_posix()
    flow.state.run_output_dir = (tmp_path / ".cache" / "test-run").as_posix()
    flow.state.evidence_registry_path = (tmp_path / "registry.json").as_posix()
    flow.state.industry_pack_path = (tmp_path / "industry_pack.md").as_posix()
    flow.state.business_pack_path = (tmp_path / "business_pack.md").as_posix()

    for path, text in [
        (flow.state.pdf_file_path, "pdf placeholder"),
        (flow.state.page_index_file_path, "{}"),
        (flow.state.document_metadata_file_path, "metadata"),
        (flow.state.industry_pack_path, "industry pack body"),
        (flow.state.business_pack_path, "business pack body"),
    ]:
        Path(path).write_text(text, encoding="utf-8")

    initialize_registry("Test Co", "Automation", flow.state.evidence_registry_path)
    peer_info_crew = PeerInfoCrew()

    inputs = flow._research_subcrew_inputs(
        crew_instance=peer_info_crew,
        pack_name=peer_info_crew.pack_name,
        pack_title=peer_info_crew.pack_title,
        output_path=(tmp_path / "peer_info_pack.md").as_posix(),
        loop_reason="initial",
        qa_feedback="补同行可比性限制。",
    )

    assert inputs["pack_title"] == "同行信息分析包"
    assert inputs["output_title"] == "同行信息分析包"
    assert inputs["owner_crew"] == peer_info_crew.crew_name
    assert inputs["search_guidance"] == peer_info_crew.search_guidance
    assert inputs["extract_guidance"] == peer_info_crew.extract_guidance
    assert inputs["qa_guidance"] == peer_info_crew.qa_guidance
    assert inputs["synthesize_guidance"] == peer_info_crew.synthesize_guidance
    assert inputs["industry_pack_text"] == "industry pack body"
    assert inputs["business_pack_text"] == "business pack body"


def test_research_subcrew_base_module_is_removed():
    """
    目的：锁住共享 research sub-crew 基类文件已经被移除。
    功能：防止后续回退到共享 `research_subcrew_base.py` 设计。
    实现逻辑：直接断言旧文件路径不存在。
    可调参数：当前无。
    默认参数及原因：固定检查旧文件路径，原因是用户明确要求彻底删除该设计。
    """

    legacy_base_file = PROJECT_ROOT / "src" / "automated_research_report_generator" / "crews" / "research_subcrew_base.py"

    assert not legacy_base_file.exists()
