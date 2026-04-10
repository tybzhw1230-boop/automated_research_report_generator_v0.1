from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Literal

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from automated_research_report_generator.flow.models import (
    CrewOwner,
    EvidenceRegistrySnapshot,
    EvidenceStance,
    QuestionLevel,
    QuestionOrigin,
    QuestionPriority,
    QuestionStatus,
    RegistryEntry,
    RegistryEntryType,
)
from automated_research_report_generator.flow.registry import (
    add_discovered_entry,
    initialize_registry,
    load_registry,
    record_registry_review,
    register_evidence,
    render_registry_markdown,
    update_entry_status,
)

# 设计目的：把 registry 工具从“只围绕 judgment JSON”扩展成“统一 entry + Markdown 视图”的单职责工具集合。
# 模块功能：提供 add_entry、add_evidence、status_update、read_registry 和 registry_review 等工具。
# 实现逻辑：路径上下文仍保存在线程本地；读操作默认返回 Markdown，写操作统一落到 JSON registry。
# 可调参数：各工具的 args_schema、registry 上下文路径以及读取视图类型。
# 默认参数及原因：registry 路径继续保存在线程本地，原因是 Flow 当前的接线方式已经稳定且线程安全。

_reg_ctx = threading.local()
QUESTION_STATUS_PRIORITY = {
    "conflicted": 0,
    "gap": 1,
    "open": 2,
    "in_progress": 3,
    "deferred": 4,
    "supported": 5,
    "confirmed": 6,
    "closed": 7,
}
QUESTION_PRIORITY_ORDER = {
    "high": 0,
    "medium": 1,
    "low": 2,
}
ReadRegistryView = Literal[
    "markdown",
    "full",
    "entry_detail",
    "evidence_detail",
]


def _normalize_registry_path(registry_path: str) -> str:
    """
    目的：统一 registry 路径的标准化方式。
    功能：把外部传入的 registry 路径展开并转换成绝对 POSIX 路径。
    实现逻辑：先去掉首尾空白，再用 `Path(...).expanduser().resolve()` 归一化。
    可调参数：`registry_path`。
    默认参数及原因：空字符串直接返回空字符串，原因是有些调用场景会先判断是否已配置上下文。
    """

    normalized_path = registry_path.strip()
    if not normalized_path:
        return ""
    return Path(normalized_path).expanduser().resolve().as_posix()


def set_evidence_registry_context(registry_path: str) -> None:
    """
    目的：给 Flow 和 crew 提供统一的 registry 上下文入口。
    功能：把当前运行应使用的 evidence registry 路径写到线程本地存储里。
    实现逻辑：接收外部路径后直接覆盖当前线程上下文，后续工具统一从这里取默认路径。
    可调参数：`registry_path`。
    默认参数及原因：没有内置默认路径，原因是每一轮运行的账本位置都应由外层流程显式指定。
    """

    _reg_ctx.path = _normalize_registry_path(registry_path)


class AddEntryInput(BaseModel):
    """
    目的：为新增 entry 工具提供明确、稳定的结构化输入。
    功能：同时支持 fact、data、judgment 三类 entry 的写入。
    实现逻辑：统一使用 entry 字段作为主接口。
    可调参数：entry 类型、层级、归属 crew、优先级、数据口径和补充说明字段。
    默认参数及原因：默认写 judgment/open/medium，原因是运行中新增条目最常见的是待验证判断。
    """

    entry_id: str = Field(..., description="唯一 entry ID。")
    entry_type: RegistryEntryType = Field(default="judgment", description="entry 类型。")
    title: str = Field(..., description="entry 标题。")
    content: str = Field(..., description="entry 正文。")
    target_pack: str = Field(..., description="该 entry 后续应归入的分析 pack。")
    evidence_needed: str = Field(default="", description="如果是 judgment，这里写后续还需要的证据。")
    parent_entry_id: str | None = Field(default=None, description="父级 entry ID。")
    entry_level: QuestionLevel = Field(default="L2", description="entry 层级。")
    entry_origin: QuestionOrigin = Field(default="discovered", description="entry 来源。")
    owner_crew: CrewOwner = Field(default="research_crew", description="默认负责该 entry 的 crew。")
    priority: QuestionPriority = Field(default="medium", description="entry 优先级。")
    status: QuestionStatus = Field(default="open", description="entry 状态。")
    source_ref: str = Field(default="", description="来源引用。")
    gap_note: str = Field(default="", description="当前缺口说明。")
    next_action: str = Field(default="", description="下一步动作。")
    value: str = Field(default="", description="数据类 entry 的数值。")
    unit: str = Field(default="", description="数据类 entry 的单位。")
    period: str = Field(default="", description="数据类 entry 的期间。")
    calibration_note: str = Field(default="", description="数据类 entry 的口径说明。")


class RegistrySeedInput(BaseModel):
    """
    目的：给 planner 一个安全的 registry 初始化入口。
    功能：支持根据公司名、行业和目标路径创建初始 registry，并同步设置当前上下文路径。
    实现逻辑：按当前定义的输入、处理和返回顺序执行。
    可调参数：company_name、industry、registry_path 和 force_reset。
    默认参数及原因：force_reset 默认 `False`，原因是初始化工具默认应幂等，避免误调用覆盖已有账本。
    """

    company_name: str = Field(..., description="公司名称。")
    industry: str = Field(..., description="行业名称。")
    registry_path: str = Field(default="", description="可选 registry 路径；留空时使用当前上下文路径。")
    force_reset: bool = Field(default=False, description="是否强制重建已有 registry。")


class AddEvidenceInput(BaseModel):
    """
    目的：把“追加新证据”做成 append-only 工具，避免和状态更新或 entry 新增混在一起。
    功能：接收证据标题、摘要、来源、关联 entry 和立场，并写入 registry。
    实现逻辑：按当前定义的输入、处理和返回顺序执行。
    可调参数：source_type、source_ref、pack_name、entry_ids、stance 和 note。
    默认参数及原因：source_type 默认 `agent_output`、stance 默认 `support`，原因是最常见场景是 agent 产出支持性证据。
    """

    title: str = Field(..., description="证据标题，要求短而直接。")
    summary: str = Field(..., description="证据摘要，要求只写关键事实。")
    pack_name: str = Field(..., description="该证据归属的 pack 或阶段名称。")
    entry_ids: list[str] = Field(..., description="该证据支撑或冲突了哪些 entry。")
    source_type: str = Field(default="agent_output", description="证据来源类型。")
    source_ref: str = Field(default="", description="证据来源引用。")
    stance: EvidenceStance = Field(default="support", description="证据立场。")
    note: str = Field(default="", description="补充备注。")


class StatusUpdateInput(BaseModel):
    """
    目的：把“更新 entry 状态”从通用修改动作里拆出来，只允许改受控字段。
    功能：批量更新 entry 状态，并按需补充 gap_note 与 next_action。
    实现逻辑：按当前定义的输入、处理和返回顺序执行。
    可调参数：entry_ids、status、gap_note 和 next_action。
    默认参数及原因：gap_note 和 next_action 默认空字符串，原因是并不是每次状态变化都需要补充说明。
    """

    entry_ids: list[str] = Field(..., description="需要更新的 entry_id 列表。")
    status: QuestionStatus = Field(..., description="目标状态。")
    gap_note: str = Field(default="", description="缺口说明。")
    next_action: str = Field(default="", description="下一步动作。")


class RegistryReviewInput(BaseModel):
    """
    目的：给每个 agent 一个“即使没有改动也要留痕”的 registry 审阅输入结构。
    功能：记录本轮审阅人、对应 pack、是否有改动、涉及 entry 和后续动作。
    实现逻辑：把最小审阅结果约束成结构化字段，便于后续检索和审计。
    可调参数：reviewer、pack_name、summary、has_changes、new_entry_ids、touched_entry_ids 和 next_action。
    默认参数及原因：entry 列表默认空列表，原因是有些审阅确实只是确认当前无需改动。
    """

    reviewer: str = Field(..., description="执行本轮 registry 审阅的角色名。")
    pack_name: str = Field(..., description="本轮审阅对应的 pack 或阶段。")
    summary: str = Field(..., description="本轮审阅结论，要求简短直接。")
    has_changes: bool = Field(default=False, description="本轮是否对 entry 或 evidence 做了实际改动。")
    new_entry_ids: list[str] = Field(default_factory=list, description="本轮新增的 entry_id 列表。")
    touched_entry_ids: list[str] = Field(default_factory=list, description="本轮确认或更新过的 entry_id 列表。")
    next_action: str = Field(default="", description="如果仍需继续跟进，这里写下一步动作。")


class ReadRegistryInput(BaseModel):
    """
    目的：把读取 registry 的常见查询模式收口到一个只读工具里。
    功能：支持 Markdown 视图、完整 JSON、entry 详情和证据详情。
    实现逻辑：默认输出 Markdown，需要下钻时再按 ID 读取详情。
    可调参数：view、状态过滤、pack 过滤、entry 类型过滤和详情 ID 列表。
    默认参数及原因：view 默认 `markdown`，原因是新版 agent 更适合先读 Markdown 视图。
    """

    view: ReadRegistryView = Field(default="markdown", description="读取视图。")
    include_statuses: list[QuestionStatus] = Field(default_factory=list, description="只返回这些状态的 entry。")
    exclude_statuses: list[QuestionStatus] = Field(default_factory=list, description="排除这些状态的 entry。")
    target_pack: str = Field(default="", description="只返回指定 target_pack 的 entry。")
    filter_entry_type: RegistryEntryType | None = Field(default=None, description="按 entry 类型过滤。")
    entry_ids: list[str] = Field(default_factory=list, description="按 ID 读取 entry 详情时使用。")
    evidence_ids: list[str] = Field(default_factory=list, description="按 ID 读取证据详情时使用。")


class _RegistryToolBase(BaseTool):
    """
    目的：把 registry 路径校验和公共序列化逻辑集中起来，避免多个工具重复实现。
    功能：提供统一的上下文检查、快照读取、entry 排序和详情序列化帮助函数。
    实现逻辑：优先使用显式 registry_path，其次回退到线程上下文路径。
    可调参数：继承类可复用这些公共方法，不需要单独传运行时参数。
    默认参数及原因：没有额外实例默认参数，原因是权限边界和输入结构应由子类显式声明。
    """

    registry_path: str = Field(default="", exclude=True)

    def model_post_init(self, __context: Any) -> None:
        """
        目的：在工具实例创建时尽早固化本次运行的 registry 路径。
        功能：优先保留显式传入的 `registry_path`，否则尝试从当前线程上下文捕获路径。
        实现逻辑：先调用父类初始化收尾，再把实例路径或线程上下文路径标准化后写回实例字段。
        可调参数：`__context` 由 Pydantic 生命周期传入。
        默认参数及原因：实例字段默认留空，原因是部分测试会先创建工具、后注入上下文。
        """

        super().model_post_init(__context)
        current_path = self.registry_path or getattr(_reg_ctx, "path", "")
        if current_path:
            self.registry_path = _normalize_registry_path(current_path)

    def _remember_registry_path(self, registry_path: str) -> str:
        """
        目的：统一维护工具实例已经绑定的 registry 路径。
        功能：把传入路径标准化后保存到实例字段，并返回可直接读写的路径。
        实现逻辑：复用统一路径标准化函数，避免不同入口各自手写路径处理。
        可调参数：`registry_path`。
        默认参数及原因：每次写入都做标准化，原因是线程上下文和显式入参都可能带相对路径。
        """

        self.registry_path = _normalize_registry_path(registry_path)
        return self.registry_path

    def _require_registry_path(self) -> str:
        """
        目的：避免工具在没有上下文路径时误读或误写 registry。
        功能：返回当前线程的 registry 路径；如果未设置则立即报错。
        实现逻辑：先检查线程本地上下文，再决定返回路径还是抛出异常。
        可调参数：无。
        默认参数及原因：未设置路径时直接抛错，原因是账本路径缺失不能静默降级。
        """

        path = getattr(_reg_ctx, "path", "")
        if path:
            return self._remember_registry_path(path)
        if self.registry_path:
            return self.registry_path
        raise ValueError("Evidence registry context is not set. Call set_evidence_registry_context() first.")

    def _resolve_registry_path(self, registry_path: str) -> str:
        """
        目的：统一处理“显式路径优先，缺省时回退上下文”的规则。
        功能：返回可直接读写的标准化 registry 路径。
        实现逻辑：先清洗传入路径；有值就直接标准化，没值就回退当前上下文路径。
        可调参数：`registry_path`。
        默认参数及原因：默认回退到当前上下文路径，原因是大多数工具调用都不需要重复传路径。
        """

        normalized_path = registry_path.strip()
        if normalized_path:
            return self._remember_registry_path(normalized_path)
        return self._require_registry_path()

    def _load_snapshot(self) -> EvidenceRegistrySnapshot:
        """
        目的：把读取当前 registry 快照的动作集中复用。
        功能：按当前上下文路径加载完整账本快照。
        实现逻辑：先取当前路径，再直接调用 `load_registry()` 返回结果。
        可调参数：无。
        默认参数及原因：默认总是读取最新文件内容，原因是工具调用之间可能已经更新了账本。
        """

        return load_registry(self._require_registry_path())

    def _infer_owner_from_pack(self, target_pack: str, owner_crew: CrewOwner) -> CrewOwner:
        """
        目的：尽量根据 target pack 自动推断 entry 的责任 crew，减少 agent 漏填时的错配。
        功能：在 owner 仍是默认值时，根据 pack 名回填更合理的 owner_crew。
        实现逻辑：优先识别新 research sub-crew，再识别 valuation 和 thesis 相关 pack。
        可调参数：`target_pack` 和 `owner_crew`。
        默认参数及原因：未知 pack 默认归 research_crew，原因是大多数新增 entry 发生在 research 阶段。
        """

        normalized_target_pack = target_pack.strip()
        pack_to_owner: dict[str, CrewOwner] = {
            "history_background_pack": "history_background_crew",
            "industry_pack": "industry_crew",
            "business_pack": "business_crew",
            "peer_info_pack": "peer_info_crew",
            "finance_pack": "financial_crew",
            "operating_metrics_pack": "operating_metrics_crew",
            "risk_pack": "risk_crew",
            "peers_pack": "valuation_crew",
            "intrinsic_value_pack": "valuation_crew",
            "valuation_pack": "valuation_crew",
            "investment_thesis": "investment_thesis_crew",
            "diligence_questions": "investment_thesis_crew",
        }
        return pack_to_owner.get(normalized_target_pack, owner_crew)

    def _infer_origin_from_owner(self, owner_crew: CrewOwner, entry_origin: QuestionOrigin) -> QuestionOrigin:
        """
        目的：让 entry 的来源字段尽量落到具体阶段，而不是停在模糊的 `discovered`。
        功能：在 origin 仍是默认值时，根据 owner_crew 推断更具体的来源。
        实现逻辑：按 crew 映射到对应来源枚举；若 origin 已被显式传入则原样保留。
        可调参数：`owner_crew` 和 `entry_origin`。
        默认参数及原因：只有在 origin 为 `discovered` 时才自动推断，原因是显式传值优先级更高。
        """
        if entry_origin != "discovered":
            return entry_origin
        if owner_crew == "planning_crew":
            return "planner"
        if owner_crew == "valuation_crew":
            return "valuation"
        if owner_crew == "investment_thesis_crew":
            return "thesis"
        if owner_crew == "qa_crew":
            return "qa"
        return "research"

    def _serialize_entry(self, entry: RegistryEntry) -> dict[str, object]:
        """
        目的：把 RegistryEntry 的输出结构固定下来，避免不同读取模式各自拼字段。
        功能：把单条 entry 对象转成可直接写 JSON 的普通字典。
        实现逻辑：按账本里已有字段顺序逐项抄出，保持读取结果稳定。
        可调参数：`entry`。
        默认参数及原因：默认输出完整核心字段，原因是 entry 详情通常需要一次看全。
        """
        return {
            "entry_id": entry.entry_id,
            "entry_type": entry.entry_type,
            "title": entry.title,
            "content": entry.content,
            "entry_origin": entry.entry_origin,
            "owner_crew": entry.owner_crew,
            "target_pack": entry.target_pack,
            "priority": entry.priority,
            "status": entry.status,
            "conflict_severity": entry.conflict_severity,
            "source_ref": entry.source_ref,
            "gap_note": entry.gap_note,
            "next_action": entry.next_action,
            "last_updated_at": entry.last_updated_at,
            "value": entry.value,
            "unit": entry.unit,
            "period": entry.period,
            "calibration_note": entry.calibration_note,
            "parent_entry_id": entry.parent_entry_id,
            "entry_level": entry.entry_level,
            "evidence_needed": entry.evidence_needed,
            "supporting_evidence_ids": entry.supporting_evidence_ids,
            "conflicting_evidence_ids": entry.conflicting_evidence_ids,
            "context_evidence_ids": entry.context_evidence_ids,
        }

    def _serialize_evidence(self, evidence) -> dict[str, object]:
        """
        目的：把 EvidenceRecord 的输出结构固定下来。
        功能：把单条 evidence 对象转成可直接写 JSON 的普通字典。
        实现逻辑：按账本里已有字段顺序逐项抄出，保持读取结果稳定。
        可调参数：`evidence`。
        默认参数及原因：默认输出完整核心字段，原因是证据详情通常需要一次看全。
        """
        return {
            "evidence_id": evidence.evidence_id,
            "title": evidence.title,
            "summary": evidence.summary,
            "source_type": evidence.source_type,
            "source_ref": evidence.source_ref,
            "pack_name": evidence.pack_name,
            "entry_ids": evidence.entry_ids,
            "stance": evidence.stance,
            "captured_at": evidence.captured_at,
            "note": evidence.note,
        }

    def _sort_entries(self, entries: list[RegistryEntry]) -> list[RegistryEntry]:
        """
        目的：让 entry 列表输出顺序稳定，减少 agent 和测试看到的随机抖动。
        功能：按状态、优先级、pack 和 entry_id 对 entry 排序。
        实现逻辑：复用固定优先级字典构造排序键。
        可调参数：`entries`。
        默认参数及原因：状态优先把最需要处理的 conflicted/gap/open 提前，原因是这最符合实际阅读顺序。
        """
        return sorted(
            entries,
            key=lambda entry: (
                QUESTION_STATUS_PRIORITY.get(entry.status, 99),
                QUESTION_PRIORITY_ORDER.get(entry.priority, 99),
                entry.target_pack,
                entry.entry_id,
            ),
        )

    def _filtered_entries(
        self,
        *,
        include_statuses: list[QuestionStatus],
        exclude_statuses: list[QuestionStatus],
        target_pack: str,
        filter_entry_type: RegistryEntryType | None,
    ) -> list[RegistryEntry]:
        """
        目的：把 entry 过滤逻辑集中复用。
        功能：按状态、pack 和类型返回筛选后的 entry 列表。
        实现逻辑：先读取快照，再逐条应用过滤条件并排序。
        可调参数：状态过滤、target_pack 和 entry 类型。
        默认参数及原因：默认返回全部 entry，原因是多数读取动作需要先看全量再决定下钻。
        """

        snapshot = self._load_snapshot()
        include_status_set = set(include_statuses)
        exclude_status_set = set(exclude_statuses)
        normalized_target_pack = target_pack.strip()
        entries = [
            entry
            for entry in snapshot.entries
            if (not include_status_set or entry.status in include_status_set)
            and (not exclude_status_set or entry.status not in exclude_status_set)
            and (not normalized_target_pack or entry.target_pack == normalized_target_pack)
            and (filter_entry_type is None or entry.entry_type == filter_entry_type)
        ]
        return self._sort_entries(entries)

    def _read_full_snapshot(self) -> str:
        """
        目的：给需要完整复核账本的场景提供一次性读取全量 registry 的稳定入口。
        功能：返回包含公司信息、entries、evidence、备注和更新时间的完整 registry JSON。
        实现逻辑：先读取当前 registry 快照，再把整个快照对象按 JSON 形式序列化输出。
        可调参数：无。
        默认参数及原因：默认直接返回完整快照，原因是 QA 和调试场景需要先看全貌。
        """

        snapshot = self._load_snapshot()
        return json.dumps(snapshot.model_dump(by_alias=True), ensure_ascii=False, indent=2)

    def _read_entry_detail(self, entry_ids: list[str]) -> str:
        """
        目的：给只读工具提供按 ID 钻取 entry 详情的入口。
        功能：返回指定 entry ID 的完整详情 JSON。
        实现逻辑：先校验 ID 列表非空，再读取快照并只挑出命中的 entry。
        可调参数：`entry_ids`。
        默认参数及原因：默认要求显式传入 ID，原因是详情查询不能模糊读取。
        """

        if not entry_ids:
            raise ValueError("entry_ids is required when view=entry_detail.")

        snapshot = self._load_snapshot()
        entry_id_set = set(entry_ids)
        payload_out = {
            "company_name": snapshot.company_name,
            "industry": snapshot.industry,
            "entry_count": len(entry_id_set),
            "entries": [
                self._serialize_entry(entry)
                for entry in snapshot.entries
                if entry.entry_id in entry_id_set
            ],
        }
        return json.dumps(payload_out, ensure_ascii=False, indent=2)

    def _read_evidence_detail(self, evidence_ids: list[str]) -> str:
        """
        目的：给只读工具提供按 ID 钻取证据详情的入口。
        功能：返回指定证据 ID 的完整详情 JSON。
        实现逻辑：先校验 ID 列表非空，再读取快照并只挑出命中的证据。
        可调参数：`evidence_ids`。
        默认参数及原因：默认要求显式传入 ID，原因是证据详情也不应做模糊读取。
        """

        if not evidence_ids:
            raise ValueError("evidence_ids is required when view=evidence_detail.")

        snapshot = self._load_snapshot()
        evidence_id_set = set(evidence_ids)
        payload_out = {
            "company_name": snapshot.company_name,
            "industry": snapshot.industry,
            "evidence_count": len(evidence_id_set),
            "evidence": [
                self._serialize_evidence(evidence)
                for evidence in snapshot.evidence
                if evidence.evidence_id in evidence_id_set
            ],
        }
        return json.dumps(payload_out, ensure_ascii=False, indent=2)


class AddEntryTool(_RegistryToolBase):
    """
    目的：给 agent 一个只能“追加新 entry”的工具，不允许顺手修改已有 entry。
    功能：把结构化输入转成 `RegistryEntry` 并安全追加到 registry。
    实现逻辑：先根据 pack 自动推断 owner 和 origin，再调用统一 entry 追加逻辑。
    可调参数：由 AddEntryInput 控制 entry 主键、类型、层级、归属和说明字段。
    默认参数及原因：工具名固定为 `add_entry`，原因是让 agent 一眼看出这是统一 entry 入口。
    """

    name: str = "add_entry"
    description: str = (
        "Append a newly discovered entry to the evidence registry. "
        "Use this for facts, data, or judgments that do not already exist."
    )
    args_schema: type[BaseModel] = AddEntryInput

    def _run(
        self,
        entry_id: str,
        entry_type: RegistryEntryType = "judgment",
        title: str = "",
        content: str = "",
        target_pack: str = "",
        evidence_needed: str = "",
        parent_entry_id: str | None = None,
        entry_level: QuestionLevel = "L2",
        entry_origin: QuestionOrigin = "discovered",
        owner_crew: CrewOwner = "research_crew",
        priority: QuestionPriority = "medium",
        status: QuestionStatus = "open",
        source_ref: str = "",
        gap_note: str = "",
        next_action: str = "",
        value: str = "",
        unit: str = "",
        period: str = "",
        calibration_note: str = "",
    ) -> str:
        """
        目的：把新增 entry 的写入动作收口在单一工具方法里。
        功能：把输入参数组装成 `RegistryEntry`，然后追加到 registry。
        实现逻辑：先按输入创建 entry 对象，再调用 `add_discovered_entry()` 落到账本。
        可调参数：entry 类型、主键、标题、正文、层级、归属、优先级和补充说明。
        默认参数及原因：默认来源是 `discovered`、状态是 `open`、优先级是 `medium`，原因是这最符合运行中发现新条目的常见场景。
        """

        normalized_owner_crew = self._infer_owner_from_pack(target_pack, owner_crew)
        normalized_entry_origin = self._infer_origin_from_owner(normalized_owner_crew, entry_origin)
        entry = RegistryEntry(
            entry_id=entry_id,
            entry_type=entry_type,
            title=title,
            content=content,
            entry_origin=normalized_entry_origin,
            owner_crew=normalized_owner_crew,
            target_pack=target_pack,
            priority=priority,
            status=status,
            source_ref=source_ref,
            gap_note=gap_note,
            next_action=next_action,
            value=value,
            unit=unit,
            period=period,
            calibration_note=calibration_note,
            parent_entry_id=parent_entry_id,
            entry_level=entry_level,
            evidence_needed=evidence_needed,
        )
        add_discovered_entry(self._require_registry_path(), entry)
        return json.dumps({"status": "ok", "entry_id": entry.entry_id}, ensure_ascii=False)


class RegistrySeedTool(_RegistryToolBase):
    """
    目的：把 registry 启动动作变成单独工具，避免 planner 依赖外部隐式初始化。
    功能：创建初始 registry，必要时跳过已有账本，并在成功后刷新当前上下文路径。
    实现逻辑：先解析目标路径；已有文件且未强制重建时直接返回 skipped，否则创建新账本并刷新上下文。
    可调参数：由 RegistrySeedInput 控制公司名、行业、目标路径和是否强制重建。
    默认参数及原因：工具名固定为 `registry_seed`，原因是让 agent 明确知道这是初始化账本。
    """

    name: str = "registry_seed"
    description: str = (
        "Initialize the evidence registry for a run. "
        "By default it is idempotent and will skip if the target registry already exists."
    )
    args_schema: type[BaseModel] = RegistrySeedInput

    def _run(
        self,
        company_name: str,
        industry: str,
        registry_path: str = "",
        force_reset: bool = False,
    ) -> str:
        """
        目的：把 registry 初始化动作做成显式、可控的一次工具调用。
        功能：创建新账本，或在已有账本存在时按规则跳过。
        实现逻辑：先解析目标路径，再决定跳过还是创建，并同步刷新上下文。
        可调参数：公司名、行业、目标路径和 `force_reset`。
        默认参数及原因：默认不重建已有账本，原因是初始化动作应优先保证安全。
        """

        resolved_path = self._resolve_registry_path(registry_path)
        path = Path(resolved_path)
        if path.exists() and not force_reset:
            set_evidence_registry_context(path.as_posix())
            return json.dumps(
                {"status": "skipped", "registry_path": path.as_posix(), "reason": "registry already exists"},
                ensure_ascii=False,
            )

        created_path = initialize_registry(company_name, industry, path)
        set_evidence_registry_context(created_path)
        return json.dumps({"status": "ok", "registry_path": created_path}, ensure_ascii=False)


class AddEvidenceTool(_RegistryToolBase):
    """
    目的：把新增证据做成独立 append-only 工具，限制 agent 只能追加，不能覆盖旧证据。
    功能：把结构化证据写入 registry，并自动挂接到关联 entry。
    实现逻辑：按当前定义的输入、处理和返回顺序执行。
    可调参数：由 AddEvidenceInput 控制标题、摘要、来源、关联 entry 和证据立场。
    默认参数及原因：工具名固定为 `add_evidence`，原因是减少 agent 把它当成通用编辑器的概率。
    """

    name: str = "add_evidence"
    description: str = (
        "Append a new evidence record to the evidence registry and link it to one or more existing entries. "
        "Keep the summary short and factual, and always include a precise source_ref."
    )
    args_schema: type[BaseModel] = AddEvidenceInput

    def _run(
        self,
        title: str,
        summary: str,
        pack_name: str,
        entry_ids: list[str],
        source_type: str = "agent_output",
        source_ref: str = "",
        stance: EvidenceStance = "support",
        note: str = "",
    ) -> str:
        """
        目的：把新增证据的写入动作保持为 append-only。
        功能：把一条新证据写入 registry，并自动挂到关联 entry 下面。
        实现逻辑：直接调用 `register_evidence()` 写账本，再把返回的证据 ID 作为成功结果返回。
        可调参数：证据标题、摘要、来源、所属 pack、关联 entry、立场和备注。
        默认参数及原因：默认来源类型是 `agent_output`、立场是 `support`，原因是这就是最常见的写入场景。
        """

        evidence_id = register_evidence(
            self._require_registry_path(),
            title=title,
            summary=summary,
            source_type=source_type,
            source_ref=source_ref,
            pack_name=pack_name,
            entry_ids=entry_ids,
            stance=stance,
            note=note,
        )
        return json.dumps({"status": "ok", "evidence_id": evidence_id}, ensure_ascii=False)


class StatusUpdateTool(_RegistryToolBase):
    """
    目的：把可修改范围限制在 entry 状态相关字段，避免 agent 修改核心事实。
    功能：批量更新 entry 状态，并可附带 gap_note 与 next_action。
    实现逻辑：把输入直接交给 `update_entry_status()`，然后返回统一成功结果。
    可调参数：由 StatusUpdateInput 控制目标 entry 列表、状态和值班说明字段。
    默认参数及原因：工具名固定为 `status_update`，原因是明确传达该工具只负责状态推进。
    """

    name: str = "status_update"
    description: str = (
        "Update the status of existing entries in the evidence registry. "
        "This tool cannot delete entries or rewrite the core entry content."
    )
    args_schema: type[BaseModel] = StatusUpdateInput

    def _run(
        self,
        entry_ids: list[str],
        status: QuestionStatus,
        gap_note: str = "",
        next_action: str = "",
    ) -> str:
        """
        目的：把 entry 状态推进限定在一组受控字段上。
        功能：批量更新 entry 状态，并可选补充缺口说明和下一步动作。
        实现逻辑：把输入直接交给 `update_entry_status()`，然后返回统一成功结果。
        可调参数：entry ID 列表、目标状态、`gap_note` 和 `next_action`。
        默认参数及原因：`gap_note` 和 `next_action` 默认空字符串，原因是并不是每次状态变化都需要补充说明。
        """

        update_entry_status(
            self._require_registry_path(),
            entry_ids,
            status=status,
            gap_note=gap_note,
            next_action=next_action,
        )
        return json.dumps({"status": "ok"}, ensure_ascii=False)


class RegistryReviewTool(_RegistryToolBase):
    """
    目的：强制每个相关 agent 在本轮任务结束时留下 registry 审阅记录。
    功能：无论是否有改动，都把审阅结果写入 registry notes，形成可追踪审计线索。
    实现逻辑：把结构化输入直接交给 `record_registry_review()`，再返回统一成功结果。
    可调参数：由 RegistryReviewInput 控制 reviewer、pack、summary、是否改动和涉及 entry。
    默认参数及原因：工具名固定为 `registry_review`，原因是让 agent 明确知道这是收尾必做动作。
    """

    name: str = "registry_review"
    description: str = (
        "Record a registry review note for the current task. "
        "Call this once before finishing the task, even when no entry or evidence changed."
    )
    args_schema: type[BaseModel] = RegistryReviewInput

    def _run(
        self,
        reviewer: str,
        pack_name: str,
        summary: str,
        has_changes: bool = False,
        new_entry_ids: list[str] | None = None,
        touched_entry_ids: list[str] | None = None,
        next_action: str = "",
    ) -> str:
        """
        目的：把 registry 审阅留痕收口到单一工具调用。
        功能：向 registry notes 追加一条结构化审阅记录。
        实现逻辑：把输入标准化后直接写入 notes，再返回最小成功结果。
        可调参数：reviewer、pack_name、summary、has_changes、entry 列表和 next_action。
        默认参数及原因：entry 列表默认空列表，原因是无改动场景也必须允许留痕。
        """

        record_registry_review(
            self._require_registry_path(),
            reviewer=reviewer,
            pack_name=pack_name,
            summary=summary,
            has_changes=has_changes,
            new_entry_ids=new_entry_ids or [],
            touched_entry_ids=touched_entry_ids or [],
            next_action=next_action,
        )
        return json.dumps({"status": "ok"}, ensure_ascii=False)


class ReadRegistryTool(_RegistryToolBase):
    """
    目的：给需要看账本内容的 agent 提供统一只读入口。
    功能：支持 Markdown、entry 详情、证据详情和完整快照几种视图。
    实现逻辑：默认走 Markdown，需要细节时再按 ID 下钻。
    可调参数：由 ReadRegistryInput 控制视图类型、状态过滤、pack 过滤和详情 ID 列表。
    默认参数及原因：工具名固定为 `read_registry`，原因是让所有只读角色都用同一套稳定语义入口。
    """

    name: str = "read_registry"
    description: str = (
        "Read the evidence registry. "
        "Use view=markdown for the grouped Markdown view, view=full for the complete JSON snapshot, "
        "view=entry_detail for specific entries, and view=evidence_detail for specific evidence rows."
    )
    args_schema: type[BaseModel] = ReadRegistryInput

    def _run(
        self,
        view: ReadRegistryView = "markdown",
        include_statuses: list[QuestionStatus] | None = None,
        exclude_statuses: list[QuestionStatus] | None = None,
        target_pack: str = "",
        filter_entry_type: RegistryEntryType | None = None,
        entry_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> str:
        """
        目的：把 registry 的几种常见读取模式统一到同一个只读入口。
        功能：根据 `view` 返回 Markdown、entry 列表、entry 详情、证据详情或完整快照。
        实现逻辑：先判断视图类型，再分别调用对应的读取帮助函数。
        可调参数：`view`、状态过滤、目标 pack、类型过滤、entry ID 和证据 ID。
        默认参数及原因：默认 `view=markdown`，原因是新版 agent 更容易遵循 Markdown 账本视图。
        """

        if view == "markdown":
            return render_registry_markdown(
                self._require_registry_path(),
                target_pack=target_pack,
                filter_entry_type=filter_entry_type,
                include_statuses=include_statuses or [],
                exclude_statuses=exclude_statuses or [],
            )
        if view == "full":
            return self._read_full_snapshot()
        if view == "entry_detail":
            return self._read_entry_detail(entry_ids or [])
        if view == "evidence_detail":
            return self._read_evidence_detail(evidence_ids or [])
        raise ValueError(f"Unsupported registry read view: {view}")

    model_config = ConfigDict(populate_by_name=True)
