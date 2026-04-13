from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from automated_research_report_generator.flow.common import utc_timestamp

# 设计目的：集中定义 v0.3 Flow 当前主链仍然使用的数据契约。
# 模块功能：统一约束 Flow 状态，以及保留仍可能被当前测试引用的研究阶段检查结果结构。
# 实现逻辑：使用 Pydantic 模型承接跨模块共享的数据，并收口到当前 source-based 主流程的真实字段。
# 可调参数：检查结果枚举值和 Flow 状态字段默认值。
# 默认参数及原因：时间字段统一走 `utc_timestamp()`，列表字段统一走 `default_factory`，原因是多轮运行更稳定。

class ResearchRegistryCheckIssue(BaseModel):
    """
    目的：保留 research 内部检查结果的结构，以兼容仍可能引用该模型的测试与旧输出约束。
    功能：统一承载条目 ID、问题类型和详细说明。
    实现逻辑：使用固定字段和 `Literal` 枚举约束问题类型，减少自由文本漂移。
    可调参数：`issue_type` 的枚举范围和 `detail` 的具体描述内容。
    默认参数及原因：本模型不提供业务默认值，原因是每条问题都必须显式来自真实检查结果。
    """

    entry_id: str = Field(description="未通过检查的条目 ID")
    issue_type: Literal[
        "missing_content",
        "need_revision",
        "source_conflict",
        "incomplete_table",
    ] = Field(description="问题类型")
    detail: str = Field(description="问题描述")


class ResearchRegistryCheckResult(BaseModel):
    """
    目的：保留旧 research 内部检查结果结构，以兼容仍可能引用该模型的测试与旧输出约束。
    功能：统一表达整体就绪状态、未通过条目、返工建议和简要结论。
    实现逻辑：使用固定字段约束结构化输出，避免旧链路引用时报类型错误。
    可调参数：问题列表、修订建议列表和建议回退阶段。
    默认参数及原因：列表字段默认空列表，原因是 ready 场景下本就不应凭空生成问题或建议。
    """

    pack_name: str = Field(description="当前分析包名称")
    overall_status: Literal["ready", "not_ready"] = Field(description="整体就绪状态")
    issues: list[ResearchRegistryCheckIssue] = Field(
        default_factory=list,
        description="未通过的条目列表，ready 时应为空",
    )
    revision_suggestions: list[str] = Field(
        default_factory=list,
        description="修订建议",
    )
    recommended_rework_stage: Literal["extract", "search", "none"] = Field(
        description="建议回退阶段，ready 时应为 none",
    )
    summary: str = Field(description="检查摘要")


class ResearchFlowState(BaseModel):
    """
    目的：统一保存整条 Flow 在各阶段共享的运行状态。
    功能：承接输入 PDF、gathering manifest、各分析包路径、估值路径、thesis 路径和最终报告路径。
    实现逻辑：把跨阶段需要传递的路径、结果和循环计数集中到一个状态模型里。
    可调参数：各路径字段、失败信息和循环计数器。
    默认参数及原因：默认从空值起步，原因是不同阶段会逐步补齐状态，不应预设业务内容。
    """

    model_config = ConfigDict(populate_by_name=True)

    pdf_file_path: str = ""
    company_name: str = ""
    industry: str = ""
    document_metadata_file_path: str = ""
    page_index_file_path: str = ""
    run_debug_manifest_path: str = ""
    run_slug: str = ""
    run_cache_dir: str = ""
    run_output_dir: str = ""
    analysis_source_dir: str = ""

    history_background_file_source_path: str = ""
    history_background_search_source_path: str = ""
    industry_file_source_path: str = ""
    industry_search_source_path: str = ""
    business_file_source_path: str = ""
    business_search_source_path: str = ""
    peer_info_peer_list_source_path: str = ""
    peer_info_peer_data_source_path: str = ""
    finance_file_source_path: str = ""
    finance_computed_metrics_path: str = ""
    finance_analysis_path: str = ""
    operating_metrics_file_source_path: str = ""
    operating_metrics_search_source_path: str = ""
    operating_metrics_analysis_path: str = ""
    risk_file_source_path: str = ""
    risk_search_source_path: str = ""

    history_background_pack_path: str = ""
    industry_pack_path: str = ""
    business_pack_path: str = ""
    peer_info_pack_path: str = ""
    finance_pack_path: str = ""
    operating_metrics_pack_path: str = ""
    risk_pack_path: str = ""

    peers_pack_path: str = ""
    intrinsic_value_pack_path: str = ""
    valuation_pack_path: str = ""

    bull_thesis_path: str = ""
    neutral_thesis_path: str = ""
    bear_thesis_path: str = ""
    investment_thesis_path: str = ""
    diligence_questions_path: str = ""

    final_report_markdown_path: str = ""
    final_report_pdf_path: str = ""
    failed_stage: str = ""
    failed_crew: str = ""
    error_message: str = ""
    blocked_packs: list[str] = Field(default_factory=list)
    block_reason: str = ""

    valuation_loop_count: int = 0
    thesis_loop_count: int = 0
