from __future__ import annotations

import json
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from automated_research_report_generator.flow.common import utc_timestamp
from automated_research_report_generator.flow.models import (
    EvidenceRecord,
    EvidenceRegistrySnapshot,
    GateReviewOutput,
    RegistryEntry,
    RegistryEntryType,
    RegistrySeedPlan,
)

_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCKS: dict[str, threading.RLock] = {}

LEGACY_PACK_NAME_MAP = {
    "history_governance_pack": "history_background_pack",
}
RESEARCH_PACK_NAMES = [
    "history_background_pack",
    "industry_pack",
    "business_pack",
    "peer_info_pack",
    "finance_pack",
    "operating_metrics_pack",
    "risk_pack",
]

# 设计目的：维护 Flow 共享的证据账本，作为 planning、research、valuation、QA 和 writeup 的共同真相源。
# 模块功能：初始化 registry、读写统一 entry、登记证据、渲染 Markdown 视图、生成快照和回写 gate 结果。
# 实现逻辑：底层始终存 JSON，同时在每次写盘后自动刷新 Markdown 视图和可选快照文件。
# 可调参数：默认 seed、pack 归一化规则、Markdown 视图格式和 gate 回写策略。
# 默认参数及原因：registry 固定保存为 UTF-8 JSON，原因是便于工具读写、调试和人工复查。


def _normalize_registry_path(registry_path: str | Path) -> str:
    """
    目的：统一 registry 路径的标准化方式。
    功能：把外部传入的路径转换成绝对 POSIX 路径。
    实现逻辑：统一走 `Path(...).expanduser().resolve()`，避免不同入口产生不同锁键。
    可调参数：`registry_path`。
    默认参数及原因：固定返回绝对路径，原因是同一个文件必须命中同一把锁。
    """

    return Path(registry_path).expanduser().resolve().as_posix()


def _normalize_pack_name(pack_name: str) -> str:
    """
    目的：在新旧 pack 命名并存时收口到统一 pack 名。
    功能：把旧 `history_governance_pack` 等名称转换成当前主命名。
    实现逻辑：先去空白，再查映射表，不命中就原样返回。
    可调参数：`pack_name`。
    默认参数及原因：未知 pack 原样返回，原因是设计允许后续继续扩展新 pack。
    """

    normalized_name = pack_name.strip()
    return LEGACY_PACK_NAME_MAP.get(normalized_name, normalized_name)


def _get_registry_lock(registry_path: str | Path) -> threading.RLock:
    """
    目的：为每个 registry 文件提供稳定的可重入锁。
    功能：按标准化后的 registry 路径返回对应的 `RLock`。
    实现逻辑：先标准化路径，再在全局锁表里查找或创建锁实例。
    可调参数：`registry_path`。
    默认参数及原因：使用 `RLock` 而不是普通锁，原因是读改写函数内部会再次调用读写函数。
    """

    normalized_path = _normalize_registry_path(registry_path)
    with _REGISTRY_LOCKS_GUARD:
        existing_lock = _REGISTRY_LOCKS.get(normalized_path)
        if existing_lock is not None:
            return existing_lock
        created_lock = threading.RLock()
        _REGISTRY_LOCKS[normalized_path] = created_lock
        return created_lock


@contextmanager
def _registry_transaction(registry_path: str | Path):
    """
    目的：把同一路径 registry 的读写包进同一段临界区。
    功能：在进入临界区后返回标准化后的 `Path`，供读写函数复用。
    实现逻辑：先获取路径级 `RLock`，再在 `with` 作用域内保持锁，直到本次事务结束。
    可调参数：`registry_path`。
    默认参数及原因：默认返回标准化后的 `Path`，原因是后续读写函数都需要同一份已归一化路径。
    """

    normalized_path = Path(_normalize_registry_path(registry_path))
    registry_lock = _get_registry_lock(normalized_path)
    with registry_lock:
        yield normalized_path


def default_seed_entries(company_name: str, industry: str) -> list[RegistryEntry]:
    """
    目的：为 registry 提供稳定的初始 entry 骨架。
    功能：生成覆盖研究、估值和写作链路的最小事实、数据和判断集合。
    实现逻辑：先给出少量跨 pack 的通用种子，再由 planning 阶段的结构化输出整体替换。
    可调参数：公司名、行业名以及各条 seed 文案。
    默认参数及原因：默认种子量保持小而全，原因是初始化阶段要先保证结构可跑，再由下游细化。
    """

    return [
        RegistryEntry(
            entry_id="fact_company_profile",
            entry_type="fact",
            title="公司基础信息待确认",
            content=f"{company_name} 的设立背景、主营方向和关键里程碑需要先被确认。",
            target_pack="history_background_pack",
            owner_crew="history_background_crew",
            priority="medium",
            status="open",
            source_ref="",
            next_action="先从招股书和年报提取设立背景、核心事件和治理结构。",
        ),
        RegistryEntry(
            entry_id="data_revenue_scale",
            entry_type="data",
            title="收入规模待标准化",
            content=f"{company_name} 最近三个期间的收入规模需要完成标准化。",
            target_pack="finance_pack",
            owner_crew="financial_crew",
            priority="high",
            status="open",
            unit="人民币",
            period="最近三个期间",
            calibration_note="先统一合并口径和单位。",
            next_action="提取最近三个期间收入和利润基础表。",
        ),
        RegistryEntry(
            entry_id="judgment_history_background",
            entry_type="judgment",
            title="治理结构是否稳健",
            content=f"{company_name} 的治理结构和关键股东关系需要被明确验证。",
            target_pack="history_background_pack",
            owner_crew="history_background_crew",
            priority="high",
            status="open",
            evidence_needed="股权结构、董事会结构、核心管理层和重大历史事件。",
            next_action="梳理时间线、控股关系和管理层背景。",
        ),
        RegistryEntry(
            entry_id="judgment_industry",
            entry_type="judgment",
            title="行业位置是否足够有利",
            content=f"{industry} 的增长、竞争和监管结构是否支持 {company_name} 的长期成长。",
            target_pack="industry_pack",
            owner_crew="industry_crew",
            priority="high",
            status="open",
            evidence_needed="行业增速、竞争格局、产业链位置和监管变化。",
            next_action="先确认行业定义、驱动和竞争格局。",
        ),
        RegistryEntry(
            entry_id="judgment_business",
            entry_type="judgment",
            title="商业模式是否具备扩张性",
            content=f"{company_name} 的产品、客户和交付链条是否形成可复制的商业模式。",
            target_pack="business_pack",
            owner_crew="business_crew",
            priority="high",
            status="open",
            evidence_needed="产品矩阵、客户结构、订单兑现、竞争优势和扩张路径。",
            next_action="拆解产品、客户和交付逻辑。",
        ),
        RegistryEntry(
            entry_id="judgment_peer_info",
            entry_type="judgment",
            title="可比公司池是否可靠",
            content=f"{company_name} 的同行集合、同行经营数据和估值倍数需要先建立可靠底稿。",
            target_pack="peer_info_pack",
            owner_crew="peer_info_crew",
            priority="high",
            status="open",
            evidence_needed="同行名单、主营差异、估值倍数和关键财务指标。",
            next_action="先筛出最相关的 3 到 5 家同行。",
        ),
        RegistryEntry(
            entry_id="judgment_finance",
            entry_type="judgment",
            title="利润质量是否站得住",
            content=f"{company_name} 的盈利能力和现金转换质量是否足以支撑后续估值。",
            target_pack="finance_pack",
            owner_crew="financial_crew",
            priority="high",
            status="open",
            evidence_needed="收入结构、毛利率、费用率、CFO、CapEx 和营运资本变化。",
            next_action="统一财务口径并检查现金流转换。",
        ),
        RegistryEntry(
            entry_id="judgment_operating_metrics",
            entry_type="judgment",
            title="关键运营指标是否改善",
            content=f"{company_name} 的关键运营指标是否相对同行改善并支持成长叙事。",
            target_pack="operating_metrics_pack",
            owner_crew="operating_metrics_crew",
            priority="medium",
            status="open",
            evidence_needed="订单、产能、客户数、出货量、利用率、单价等运营指标。",
            next_action="先收集最能改变投资判断的运营指标。",
        ),
        RegistryEntry(
            entry_id="judgment_risk",
            entry_type="judgment",
            title="关键风险是否已聚焦",
            content=f"{company_name} 当前最值得优先跟踪的风险是否已经被聚焦并写清触发条件。",
            target_pack="risk_pack",
            owner_crew="risk_crew",
            priority="high",
            status="open",
            evidence_needed="经营、客户、技术、财务、治理和外部环境风险证据。",
            next_action="列出前 5 到 10 项高影响风险及监控指标。",
        ),
        RegistryEntry(
            entry_id="judgment_peers_valuation",
            entry_type="judgment",
            title="相对估值是否合理",
            content=f"{company_name} 的相对估值区间是否能被同行倍数和口径差异合理解释。",
            target_pack="peers_pack",
            owner_crew="valuation_crew",
            priority="high",
            status="open",
            evidence_needed="同行倍数、可比性限制、估值区间和调整理由。",
            next_action="基于 peer_info_pack 开始构建相对估值框架。",
        ),
        RegistryEntry(
            entry_id="judgment_intrinsic_value",
            entry_type="judgment",
            title="内在价值假设是否可信",
            content=f"{company_name} 的现金流、回报和折现假设是否有足够证据支撑。",
            target_pack="intrinsic_value_pack",
            owner_crew="valuation_crew",
            priority="high",
            status="open",
            evidence_needed="收入增速、利润率、资本开支、折现率和终值假设。",
            next_action="先确认估值核心假设和敏感性变量。",
        ),
    ]


def _render_markdown_from_snapshot(
    snapshot: EvidenceRegistrySnapshot,
    *,
    target_pack: str = "",
    filter_entry_type: RegistryEntryType | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
) -> str:
    """
    目的：把当前 registry 快照渲染成适合 LLM 和人工阅读的 Markdown。
    功能：按类型分组输出 facts、data、judgments，并支持 pack 和状态过滤。
    实现逻辑：先过滤 entry，再按三类分别生成 Markdown 表格。
    可调参数：target_pack、entry 类型和状态过滤条件。
    默认参数及原因：默认输出三类完整视图，原因是 research QA 需要先看整体，再定位缺口。
    """

    normalized_target_pack = _normalize_pack_name(target_pack) if target_pack else ""
    include_set = {status.strip() for status in (include_statuses or []) if status.strip()}
    exclude_set = {status.strip() for status in (exclude_statuses or []) if status.strip()}
    filtered_entries: list[RegistryEntry] = []
    for entry in snapshot.entries:
        if normalized_target_pack and _normalize_pack_name(entry.target_pack) != normalized_target_pack:
            continue
        if filter_entry_type and entry.entry_type != filter_entry_type:
            continue
        if include_set and entry.status not in include_set:
            continue
        if exclude_set and entry.status in exclude_set:
            continue
        filtered_entries.append(entry)

    facts = [entry for entry in filtered_entries if entry.entry_type == "fact"]
    data_entries = [entry for entry in filtered_entries if entry.entry_type == "data"]
    judgment_entries = [entry for entry in filtered_entries if entry.entry_type == "judgment"]
    pending_judgments = [
        entry for entry in judgment_entries if entry.status in {"open", "in_progress", "gap", "conflicted"}
    ]
    supported_judgments = [
        entry for entry in judgment_entries if entry.status in {"supported", "confirmed", "closed"}
    ]

    lines = [
        f"# 证据注册表：{snapshot.company_name} | {snapshot.industry}",
        f"更新时间：{snapshot.updated_at}",
        "",
        f"## 事实 (共 {len(facts)} 条)",
        "| ID | 标题 | 内容 | 来源 | Pack | 状态 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if facts:
        for entry in facts:
            lines.append(
                f"| {entry.entry_id} | {entry.title} | {entry.content} | {entry.source_ref or '-'} | "
                f"{entry.target_pack} | {entry.status} |"
            )
    else:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            f"## 数据 (共 {len(data_entries)} 条)",
            "| ID | 指标 | 值 | 单位 | 期间 | 口径 | Pack | 状态 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if data_entries:
        for entry in data_entries:
            lines.append(
                f"| {entry.entry_id} | {entry.title} | {entry.value or entry.content} | {entry.unit or '-'} | "
                f"{entry.period or '-'} | {entry.calibration_note or '-'} | {entry.target_pack} | {entry.status} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            f"## 判断 (共 {len(judgment_entries)} 条)",
            "### ⚠ 待补证据 (gap/open/conflicted/in_progress)",
            "| ID | 标题 | 判断 | Pack | 状态 | 冲突程度 | 缺口 | 下一步 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if pending_judgments:
        for entry in pending_judgments:
            lines.append(
                f"| {entry.entry_id} | {entry.title} | {entry.content} | {entry.target_pack} | {entry.status} | "
                f"{entry.conflict_severity} | {entry.gap_note or '-'} | {entry.next_action or '-'} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "### ✓ 已支持 (supported/confirmed/closed)",
            "| ID | 标题 | 判断 | Pack | 证据数 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if supported_judgments:
        for entry in supported_judgments:
            evidence_count = (
                len(entry.supporting_evidence_ids)
                + len(entry.conflicting_evidence_ids)
                + len(entry.context_evidence_ids)
            )
            lines.append(
                f"| {entry.entry_id} | {entry.title} | {entry.content} | {entry.target_pack} | {evidence_count} |"
            )
    else:
        lines.append("| - | - | - | - | - |")

    if snapshot.notes:
        lines.extend(["", "## 最近备注", *[f"- {note}" for note in snapshot.notes[-10:]]])

    return "\n".join(lines)


def render_registry_markdown(
    registry_path: str | Path,
    *,
    target_pack: str = "",
    filter_entry_type: RegistryEntryType | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
) -> str:
    """
    目的：给 QA、crew agent 和人工调试提供稳定的 Markdown 视图。
    功能：读取 registry 后输出按类型分组的 Markdown 表格。
    实现逻辑：先加载快照，再调用统一渲染函数。
    可调参数：pack 过滤、entry 类型过滤和状态过滤。
    默认参数及原因：默认输出完整账本视图，原因是大多数审阅场景都需要先看全貌。
    """

    with _registry_transaction(registry_path):
        snapshot = load_registry(registry_path)
        return _render_markdown_from_snapshot(
            snapshot,
            target_pack=target_pack,
            filter_entry_type=filter_entry_type,
            include_statuses=include_statuses,
            exclude_statuses=exclude_statuses,
        )


def _write_markdown_snapshot(snapshot: EvidenceRegistrySnapshot, registry_path: Path) -> None:
    """
    目的：在每次 JSON 保存后同步刷新可读 Markdown 快照。
    功能：把 registry 当前内容写到 `registry_snapshot.md`。
    实现逻辑：复用统一的 Markdown 渲染器，并把结果写到 registry 所在目录。
    可调参数：快照对象和 JSON 路径。
    默认参数及原因：文件名固定为 `registry_snapshot.md`，原因是人工排查时需要一个稳定入口。
    """

    markdown_path = registry_path.with_name("registry_snapshot.md")
    markdown_path.write_text(_render_markdown_from_snapshot(snapshot), encoding="utf-8")


def save_registry(snapshot: EvidenceRegistrySnapshot, registry_path: str | Path) -> str:
    """
    目的：把 registry 快照安全落盘。
    功能：更新 `updated_at`，确保目录存在，并同时写 JSON 与 Markdown 视图。
    实现逻辑：在同一把路径锁里完成写盘，避免并发时出现半截 JSON 或视图错位。
    可调参数：`snapshot` 和 `registry_path`。
    默认参数及原因：写盘前总是刷新时间戳，原因是便于判断最后一次修改时点。
    """

    with _registry_transaction(registry_path) as path:
        path.parent.mkdir(parents=True, exist_ok=True)
        snapshot.updated_at = utc_timestamp()
        path.write_text(snapshot.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
        _write_markdown_snapshot(snapshot, path)
        return path.as_posix()


def load_registry(registry_path: str | Path) -> EvidenceRegistrySnapshot:
    """
    目的：提供统一的 registry 读取入口。
    功能：读取 JSON 并还原成 `EvidenceRegistrySnapshot`。
    实现逻辑：固定按 UTF-8 读取，并由 Pydantic 还原统一 entry 结构。
    可调参数：`registry_path`。
    默认参数及原因：固定按 UTF-8 读取，原因是项目产物统一使用 UTF-8。
    """

    with _registry_transaction(registry_path) as path:
        return EvidenceRegistrySnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def initialize_registry(company_name: str, industry: str, registry_path: str | Path) -> str:
    """
    目的：为 Flow 初始化一份干净的 evidence registry。
    功能：写入公司信息、默认 seed entry 和初始说明。
    实现逻辑：先生成默认 seed，再调用统一保存入口落盘。
    可调参数：公司名、行业名和 registry 路径。
    默认参数及原因：初始化时只写骨架，不预放证据，原因是证据必须随研究逐步进入账本。
    """

    snapshot = EvidenceRegistrySnapshot(
        company_name=company_name,
        industry=industry,
        entries=default_seed_entries(company_name, industry),
        notes=["Seeded registry from v0.3 unified entry template."],
    )
    return save_registry(snapshot, registry_path)


def entry_ids_for_packs(
    registry_path: str | Path,
    pack_names: list[str],
    *,
    entry_types: list[RegistryEntryType] | None = None,
) -> list[str]:
    """
    目的：按当前 registry 里的真实 entry 集合动态查找 pack 对应的 entry ID。
    功能：读取账本后返回指定 pack 列表下的 entry ID，且自动去重保序。
    实现逻辑：先读取快照，再按 pack 和类型筛选。
    可调参数：`pack_names` 和可选的 `entry_types` 过滤条件。
    默认参数及原因：找不到时返回空列表，原因是 planning 可能主动裁掉某些 pack。
    """

    with _registry_transaction(registry_path) as path:
        if not path.exists():
            return []
        snapshot = load_registry(path)
        pack_name_set = {_normalize_pack_name(pack_name) for pack_name in pack_names if pack_name.strip()}
        entry_type_set = set(entry_types or [])
        ordered_entry_ids: list[str] = []
        seen_entry_ids: set[str] = set()

        for entry in snapshot.entries:
            if _normalize_pack_name(entry.target_pack) not in pack_name_set:
                continue
            if entry_type_set and entry.entry_type not in entry_type_set:
                continue
            if entry.entry_id in seen_entry_ids:
                continue
            seen_entry_ids.add(entry.entry_id)
            ordered_entry_ids.append(entry.entry_id)

        return ordered_entry_ids


def replace_registry_entries(registry_path: str | Path, seed_plan: RegistrySeedPlan) -> list[str]:
    """
    目的：让 planning 阶段可以用结构化 seed 覆盖默认骨架。
    功能：把 planning 输出的 seed entry 转换成 `RegistryEntry`，整体替换当前 registry 的 entries。
    实现逻辑：只在收到非空 seed 集时执行覆盖，并追加备注保留追踪信息。
    可调参数：`registry_path` 和 `seed_plan`。
    默认参数及原因：当 `seed_plan.entries` 为空时直接跳过，原因是空结果不应抹掉默认骨架。
    """

    if not seed_plan.entries:
        return []

    with _registry_transaction(registry_path):
        snapshot = load_registry(registry_path)
        snapshot.entries = [
            RegistryEntry(
                entry_id=item.entry_id,
                entry_type=item.entry_type,
                title=item.title,
                content=item.content,
                entry_origin="planner",
                owner_crew=item.owner_crew,
                target_pack=_normalize_pack_name(item.target_pack),
                priority=item.priority,
                status="open",
                source_ref=item.source_ref,
                next_action=item.next_action,
                value=item.value,
                unit=item.unit,
                period=item.period,
                calibration_note=item.calibration_note,
                parent_entry_id=item.parent_entry_id,
                entry_level=item.entry_level,
                evidence_needed=item.evidence_needed,
            )
            for item in seed_plan.entries
        ]
        summary = seed_plan.summary.strip()
        if summary:
            snapshot.notes.append(f"planner_seed: replaced entries | {summary[:500]}")
        else:
            snapshot.notes.append(f"planner_seed: replaced entries | entry_count={len(snapshot.entries)}")
        save_registry(snapshot, registry_path)
        return [entry.entry_id for entry in snapshot.entries]
def find_entry(snapshot: EvidenceRegistrySnapshot, entry_id: str) -> RegistryEntry | None:
    """
    目的：避免多个写操作各自手写 entry 查找逻辑。
    功能：按 `entry_id` 在快照里返回对应 entry。
    实现逻辑：线性扫描当前 entry 列表并返回首个命中项。
    可调参数：`snapshot` 和 `entry_id`。
    默认参数及原因：找不到时返回 `None`，原因是调用方通常需要自己决定跳过还是补救。
    """

    for entry in snapshot.entries:
        if entry.entry_id == entry_id:
            return entry
    return None
def register_evidence(
    registry_path: str | Path,
    *,
    title: str,
    summary: str,
    source_type: str,
    source_ref: str,
    pack_name: str,
    entry_ids: list[str],
    stance: str = "support",
    note: str = "",
) -> str:
    """
    目的：把 pack、QA 或人工整理出的新证据挂到 registry 上。
    功能：创建 `EvidenceRecord`，并把证据关联到对应 entry。
    实现逻辑：在同一事务里追加证据、更新 entry 状态和更新时间，再统一保存。
    可调参数：证据标题、摘要、来源、pack、关联 entry 列表和 stance。
    默认参数及原因：`stance` 默认 `support`，因为大多数新增证据首先用于支持判断。
    """

    normalized_pack_name = _normalize_pack_name(pack_name)
    with _registry_transaction(registry_path):
        snapshot = load_registry(registry_path)
        evidence_id = f"ev_{uuid.uuid4().hex[:12]}"
        record = EvidenceRecord(
            evidence_id=evidence_id,
            title=title,
            summary=summary,
            source_type=source_type,
            source_ref=source_ref,
            pack_name=normalized_pack_name,
            entry_ids=entry_ids,
            stance=stance,  # type: ignore[arg-type]
            note=note,
        )
        snapshot.evidence.append(record)

        for entry_id in entry_ids:
            entry = find_entry(snapshot, entry_id)
            if not entry:
                continue
            if stance == "conflict":
                target_list = entry.conflicting_evidence_ids
                entry.status = "conflicted"
                entry.conflict_severity = "major" if entry.conflict_severity == "none" else entry.conflict_severity
            elif stance == "context":
                target_list = entry.context_evidence_ids
            else:
                target_list = entry.supporting_evidence_ids
                if entry.status in {"open", "in_progress", "gap"}:
                    entry.status = "supported"
            if evidence_id not in target_list:
                target_list.append(evidence_id)
            entry.last_updated_at = utc_timestamp()

        save_registry(snapshot, registry_path)
        return evidence_id


def add_discovered_entry(registry_path: str | Path, entry: RegistryEntry) -> None:
    """
    目的：把研究过程中新增的 entry 安全追加到 registry。
    功能：先检查是否重号，再把新 entry 写回快照。
    实现逻辑：命中重复 `entry_id` 时直接跳过，避免重复创建同一问题。
    可调参数：`registry_path` 和 `entry`。
    默认参数及原因：重复时直接跳过，原因是运行期发现问题宁可保守也不要制造重复记录。
    """

    with _registry_transaction(registry_path):
        snapshot = load_registry(registry_path)
        if find_entry(snapshot, entry.entry_id):
            return
        entry.target_pack = _normalize_pack_name(entry.target_pack)
        snapshot.entries.append(entry)
        save_registry(snapshot, registry_path)
def update_entry_status(
    registry_path: str | Path,
    entry_ids: list[str],
    *,
    status: str,
    gap_note: str = "",
    next_action: str = "",
) -> None:
    """
    目的：统一更新 entry 状态，避免不同模块各自写状态回写逻辑。
    功能：批量更新状态、缺口说明、下一步动作和更新时间。
    实现逻辑：在同一事务里逐条更新命中的 entry 后统一保存。
    可调参数：entry 列表、目标状态、gap_note 和 next_action。
    默认参数及原因：缺口说明和下一步动作默认空串，原因是并不是每次状态变化都需要补文字。
    """

    with _registry_transaction(registry_path):
        snapshot = load_registry(registry_path)
        for entry_id in entry_ids:
            entry = find_entry(snapshot, entry_id)
            if not entry:
                continue
            entry.status = status  # type: ignore[assignment]
            if gap_note:
                entry.gap_note = gap_note
            if next_action:
                entry.next_action = next_action
            entry.last_updated_at = utc_timestamp()
        save_registry(snapshot, registry_path)


def append_registry_note(registry_path: str | Path, note: str) -> None:
    """
    目的：给 registry 提供最轻量的追加备注入口。
    功能：把新的说明文字追加到 `notes` 列表并立即落盘。
    实现逻辑：在同一事务里读取、追加并保存。
    可调参数：`registry_path` 和 `note`。
    默认参数及原因：追加后立即保存，原因是备注通常用于人工追踪，不适合只留在内存。
    """

    with _registry_transaction(registry_path):
        snapshot = load_registry(registry_path)
        snapshot.notes.append(note)
        save_registry(snapshot, registry_path)


def record_registry_review(
    registry_path: str | Path,
    *,
    reviewer: str,
    pack_name: str,
    summary: str,
    has_changes: bool,
    new_entry_ids: list[str] | None = None,
    touched_entry_ids: list[str] | None = None,
    next_action: str = "",
) -> None:
    """
    目的：强制把各 agent 对 registry 的审阅动作留下可追踪记录。
    功能：无论本轮有没有实际改动，都向 registry 追加一条结构化 review 备注。
    实现逻辑：把 reviewer、pack、变更状态、entry 列表和摘要压成单行备注。
    可调参数：reviewer、pack_name、summary、变更标记、entry 列表和 next_action。
    默认参数及原因：entry 列表默认空列表，原因是有些审阅确实只是确认“当前无需改动”。
    """

    normalized_new_ids = [entry_id for entry_id in (new_entry_ids or []) if entry_id]
    normalized_touched_ids = [entry_id for entry_id in (touched_entry_ids or []) if entry_id]
    review_payload = {
        "reviewer": reviewer,
        "pack_name": _normalize_pack_name(pack_name),
        "status": "updated" if has_changes else "no_change",
        "new_entry_ids": normalized_new_ids,
        "touched_entry_ids": normalized_touched_ids,
        "summary": summary[:500],
        "next_action": next_action[:300],
    }
    append_registry_note(
        registry_path,
        f"registry_review: {json.dumps(review_payload, ensure_ascii=False, sort_keys=True)}",
    )


def summarize_registry(registry_path: str | Path) -> str:
    """
    目的：给 agent 和调试流程提供一份足够短但可用的 registry 摘要。
    功能：提取公司信息、entry 列表、证据数量和最近备注。
    实现逻辑：读取快照后压成精简 JSON 文本返回。
    可调参数：registry 路径。
    默认参数及原因：最近备注只保留最后 10 条，原因是避免 prompt 过长。
    """

    with _registry_transaction(registry_path):
        snapshot = load_registry(registry_path)
        payload = {
            "company_name": snapshot.company_name,
            "industry": snapshot.industry,
            "entry_count": len(snapshot.entries),
            "entries": [
                {
                    "entry_id": entry.entry_id,
                    "entry_type": entry.entry_type,
                    "title": entry.title,
                    "content": entry.content,
                    "target_pack": entry.target_pack,
                    "status": entry.status,
                    "priority": entry.priority,
                    "gap_note": entry.gap_note,
                    "next_action": entry.next_action,
                }
                for entry in snapshot.entries
            ],
            "evidence_count": len(snapshot.evidence),
            "notes": snapshot.notes[-10:],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def save_registry_snapshot(registry_path: str | Path, snapshot_path: str | Path) -> str:
    """
    目的：把当前 registry 固化成一个可回溯的阶段快照。
    功能：复制当前 JSON 到指定 snapshot 路径。
    实现逻辑：在读取锁内拿到最新路径内容，再复制到目标位置。
    可调参数：当前 registry 路径和目标 snapshot 路径。
    默认参数及原因：直接复制原始 JSON，原因是后续 diff 更适合基于结构化原文。
    """

    target_path = Path(snapshot_path).expanduser().resolve()
    with _registry_transaction(registry_path) as source_path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
    return target_path.as_posix()


def build_registry_diff_summary(
    previous_snapshot_path: str | Path | None,
    current_snapshot_path: str | Path,
) -> str:
    """
    目的：给 research QA 提供一份短小可用的增量变化摘要。
    功能：比较两份 registry 快照的 entry 和证据变化，并输出文本摘要。
    实现逻辑：读取两份快照后比较新增 ID、状态变化和证据数量变化。
    可调参数：上一轮快照路径和当前快照路径。
    默认参数及原因：没有上一轮时返回“首次审查”说明，原因是第一轮不存在可比较基准。
    """

    current_snapshot = load_registry(current_snapshot_path)
    if previous_snapshot_path is None or not Path(previous_snapshot_path).exists():
        return "首次审查，没有上一轮 registry 快照可供比较。"

    previous_snapshot = load_registry(previous_snapshot_path)
    previous_entry_map = {entry.entry_id: entry for entry in previous_snapshot.entries}
    current_entry_map = {entry.entry_id: entry for entry in current_snapshot.entries}
    new_entry_ids = [entry_id for entry_id in current_entry_map if entry_id not in previous_entry_map]
    changed_status_ids = [
        entry_id
        for entry_id, entry in current_entry_map.items()
        if entry_id in previous_entry_map and entry.status != previous_entry_map[entry_id].status
    ]
    previous_evidence_ids = {evidence.evidence_id for evidence in previous_snapshot.evidence}
    current_evidence_ids = {evidence.evidence_id for evidence in current_snapshot.evidence}
    new_evidence_count = len(current_evidence_ids - previous_evidence_ids)

    lines = [
        "本轮 registry 变化摘要：",
        f"- 新增 entry 数：{len(new_entry_ids)}",
        f"- 状态变化 entry 数：{len(changed_status_ids)}",
        f"- 新增证据数：{new_evidence_count}",
    ]
    if new_entry_ids:
        lines.append(f"- 新增 entry ID：{', '.join(new_entry_ids[:20])}")
    if changed_status_ids:
        lines.append(f"- 状态变化 entry ID：{', '.join(changed_status_ids[:20])}")
    return "\n".join(lines)


def apply_gate_review(
    registry_path: str | Path,
    *,
    stage_name: str,
    entry_ids: list[str],
    review: GateReviewOutput,
) -> None:
    """
    目的：把 QA gate 的结果统一回写到 registry。
    功能：根据 `pass`、`revise`、`stop` 三种状态更新 entry 状态并追加备注。
    实现逻辑：先决定要回写的 entry 集合，再按状态批量更新并落备注。
    可调参数：阶段名、entry 列表和 QA 结果。
    默认参数及原因：`revise` 回写为 `gap`，`stop` 回写为 `deferred`，方便 Flow 下一步识别分支。
    """

    affected_entry_ids = list(entry_ids)
    if not affected_entry_ids and review.affected_packs:
        affected_entry_ids = entry_ids_for_packs(registry_path, review.affected_packs)

    if review.status == "pass":
        update_entry_status(
            registry_path,
            affected_entry_ids,
            status="supported",
            next_action=f"{stage_name} gate passed.",
        )
    elif review.status == "revise":
        update_entry_status(
            registry_path,
            affected_entry_ids,
            status="gap",
            gap_note="; ".join(review.key_gaps)[:500],
            next_action="; ".join(review.priority_actions)[:500],
        )
    else:
        update_entry_status(
            registry_path,
            affected_entry_ids,
            status="deferred",
            gap_note=f"{stage_name} gate requested stop.",
            next_action="Stop the workflow and review manually.",
        )

    note_payload = {
        "stage_name": stage_name,
        "status": review.status,
        "summary": review.summary[:500],
        "affected_packs": review.affected_packs,
        "key_gaps": review.key_gaps[:10],
        "priority_actions": review.priority_actions[:10],
    }
    append_registry_note(
        registry_path,
        f"gate_review: {json.dumps(note_payload, ensure_ascii=False, sort_keys=True)}",
    )
