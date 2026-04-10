from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from automated_research_report_generator.flow.common import utc_timestamp

# 设计目的：集中定义 Flow、registry、QA gate 和 checkpoint 共用的数据模型。
# 模块功能：统一约束 registry entry、证据、planning seed、QA 结果和整条 Flow 状态。
# 实现逻辑：使用 Pydantic 模型承接跨模块共享的数据，统一收口到 entry 命名。
# 可调参数：各类 `Literal` 枚举值和字段默认值。
# 默认参数及原因：时间字段统一走 `utc_timestamp()`，列表字段统一走 `default_factory`，原因是多轮运行更稳定。

QuestionLevel = Literal["L1", "L2", "L3"]
QuestionOrigin = Literal["seeded", "planner", "research", "valuation", "thesis", "qa", "discovered"]
RegistryEntryType = Literal["fact", "data", "judgment"]
QuestionStatus = Literal[
    "open",
    "in_progress",
    "supported",
    "conflicted",
    "gap",
    "confirmed",
    "deferred",
    "closed",
]
QuestionPriority = Literal["high", "medium", "low"]
CrewOwner = Literal[
    "planning_crew",
    "research_crew",
    "valuation_crew",
    "investment_thesis_crew",
    "qa_crew",
    "history_background_crew",
    "industry_crew",
    "business_crew",
    "peer_info_crew",
    "financial_crew",
    "operating_metrics_crew",
    "risk_crew",
]
GateStatus = Literal["pass", "revise", "stop"]
EvidenceStance = Literal["support", "conflict", "context"]
ConflictSeverity = Literal["none", "minor", "major"]


class RegistryEntry(BaseModel):
    """
    目的：定义 registry 中统一的 entry 结构。
    功能：同时表达事实、数据和判断三类条目。
    实现逻辑：统一使用 entry 命名，不再接收旧 `question_* / judgment_*` 字段。
    可调参数：entry 类型、状态、优先级、目标 pack、证据关联和数据口径字段。
    默认参数及原因：默认按 judgment 处理，原因是当前大部分 seed 和 QA 仍围绕 judgment 展开。
    """

    model_config = ConfigDict(populate_by_name=True)

    entry_id: str
    entry_type: RegistryEntryType = "judgment"
    title: str
    content: str
    entry_origin: QuestionOrigin = "seeded"
    target_pack: str
    owner_crew: CrewOwner = "planning_crew"
    priority: QuestionPriority = "high"
    status: QuestionStatus = "open"
    conflict_severity: ConflictSeverity = "none"
    source_ref: str = ""
    gap_note: str = ""
    next_action: str = ""
    last_updated_at: str = Field(default_factory=utc_timestamp)

    value: str = ""
    unit: str = ""
    period: str = ""
    calibration_note: str = ""

    parent_entry_id: str | None = None
    entry_level: QuestionLevel = "L1"
    evidence_needed: str = ""
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    context_evidence_ids: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    """
    目的：定义挂到 registry entry 上的单条证据记录。
    功能：保存证据标题、摘要、来源、立场和关联 entry。
    实现逻辑：统一使用 `entry_ids` 保存关联条目。
    可调参数：各字段都可以按证据来源和使用场景覆盖。
    默认参数及原因：`stance` 默认 `support`，原因是新增证据大多先用于支持判断。
    """

    evidence_id: str
    title: str
    summary: str
    source_type: str
    source_ref: str
    pack_name: str
    entry_ids: list[str] = Field(default_factory=list)
    stance: EvidenceStance = "support"
    captured_at: str = Field(default_factory=utc_timestamp)
    note: str = ""


class EvidenceRegistrySnapshot(BaseModel):
    """
    目的：表达 registry 当前完整快照。
    功能：统一保存公司信息、entry 列表、证据列表和备注。
    实现逻辑：把三类 entry 与证据一起放进同一快照模型，便于一次读写整份账本。
    可调参数：公司信息、entries、evidence、notes。
    默认参数及原因：列表字段默认空列表，原因是初始化时通常先搭框架再逐步补充。
    """

    model_config = ConfigDict(populate_by_name=True)

    company_name: str
    industry: str
    entries: list[RegistryEntry] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_timestamp)


class RegistrySeedEntryPlan(BaseModel):
    """
    目的：承接 planning 阶段输出的单条 registry seed。
    功能：约束 seed 需要的最小字段，并允许输出 fact、data、judgment 三类 entry。
    实现逻辑：统一使用 entry 字段名。
    可调参数：entry 类型、层级、父级、优先级、owner_crew、数据口径和后续动作。
    默认参数及原因：默认按 judgment/high/L1 处理，原因是 planning 输出里 judgment 仍是主体。
    """

    model_config = ConfigDict(populate_by_name=True)

    entry_id: str
    entry_type: RegistryEntryType = "judgment"
    title: str
    content: str
    target_pack: str
    evidence_needed: str = ""
    owner_crew: CrewOwner
    parent_entry_id: str | None = None
    entry_level: QuestionLevel = "L1"
    priority: QuestionPriority = "high"
    source_ref: str = ""
    next_action: str = ""
    value: str = ""
    unit: str = ""
    period: str = ""
    calibration_note: str = ""


class RegistrySeedPlan(BaseModel):
    """
    目的：承接 planning 阶段的整份 registry seed 输出。
    功能：保存规划摘要和一组可直接写入 registry 的 seed entry。
    实现逻辑：统一使用 `entries` 字段。
    可调参数：`summary` 与 `entries`。
    默认参数及原因：entry 列表默认空列表，原因是 planning 异常时不应直接打断整条 Flow。
    """

    model_config = ConfigDict(populate_by_name=True)

    summary: str = ""
    entries: list[RegistrySeedEntryPlan] = Field(default_factory=list)


class GateReviewOutput(BaseModel):
    """
    目的：统一 QA gate 的结构化输出格式。
    功能：保存 gate 状态、摘要、关键缺口、优先动作和受影响的 pack。
    实现逻辑：让 research QA 的结果都落到同一模型上。
    可调参数：各字段由 QA crew 输出时填写。
    默认参数及原因：列表字段默认空列表，原因是通过场景常常不需要额外动作。
    """

    status: GateStatus
    summary: str
    key_gaps: list[str] = Field(default_factory=list)
    priority_actions: list[str] = Field(default_factory=list)
    affected_packs: list[str] = Field(default_factory=list)


class ResearchFlowState(BaseModel):
    """
    目的：统一保存整条 Flow 在各阶段共享的运行状态。
    功能：承接输入 PDF、各 pack 路径、QA 结果、checkpoint 产物和最终报告路径。
    实现逻辑：把跨阶段需要传递的路径、结果和循环计数集中到一个状态模型里。
    可调参数：各路径字段、QA 结果字段、反馈文本和循环计数器。
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
    evidence_registry_path: str = ""
    research_scope_path: str = ""
    question_tree_path: str = ""
    evidence_map_seed_path: str = ""

    history_background_pack_path: str = ""
    history_governance_pack_path: str = ""
    industry_pack_path: str = ""
    business_pack_path: str = ""
    peer_info_pack_path: str = ""
    finance_pack_path: str = ""
    operating_metrics_pack_path: str = ""
    risk_pack_path: str = ""

    peers_pack_path: str = ""
    intrinsic_value_pack_path: str = ""
    valuation_pack_path: str = ""

    investment_thesis_path: str = ""
    diligence_questions_path: str = ""

    last_research_qa_feedback: str = ""
    coverage_report_research: GateReviewOutput | None = None
    qa_report_research: GateReviewOutput | None = None
    coverage_report_valuation: GateReviewOutput | None = None
    qa_report_valuation: GateReviewOutput | None = None
    coverage_report_thesis: GateReviewOutput | None = None
    qa_report_thesis: GateReviewOutput | None = None

    final_report_markdown_path: str = ""
    final_report_pdf_path: str = ""

    research_loop_count: int = 0
    valuation_loop_count: int = 0
    thesis_loop_count: int = 0
