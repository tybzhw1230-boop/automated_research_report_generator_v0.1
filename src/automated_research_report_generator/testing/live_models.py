from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from automated_research_report_generator.flow.common import utc_timestamp

# 设计目的：集中定义 live harness 需要长期稳定读写的结构化结果模型。
# 模块功能：统一描述 case 规格、单 case 结果、循环告警和整轮 suite 汇总。
# 实现逻辑：使用 Pydantic 模型约束 JSON 结构，避免父子进程和汇总脚本之间字段漂移。
# 可调参数：状态枚举、根因枚举和 case 元数据字段都可以按后续演进扩展。
# 默认参数及原因：时间字段统一使用 `utc_timestamp()`，原因是 suite 汇总、日志和故障现场需要稳定对齐。

CaseKind = Literal["precheck", "component", "chain"]
CaseStatus = Literal[
    "passed",
    "failed",
    "skipped_missing_env",
    "aborted_loop_guard",
    "blocked_precheck",
]
RootCauseCategory = Literal[
    "env_missing",
    "provider_context_overflow",
    "provider_rate_limit",
    "tool_loop",
    "prompt_loop",
    "artifact_wiring_error",
    "timeout_no_progress",
    "unexpected_exception",
]


class LoopAlert(BaseModel):
    """
    目的：记录一次 live 运行被监控器截停时的最小可复盘告警信息。
    功能：承载触发规则、触发原因、根因分类和关联证据文件路径。
    实现逻辑：由父进程监控器在命中规则后统一填充，再写入 case 目录中的 `loop_alert.json`。
    可调参数：`rule_id`、`reason`、`matched_value`、`root_cause_category` 和 `evidence_files`。
    默认参数及原因：`triggered_at` 默认取当前时间，原因是截停发生时需要立刻固定证据时间点。
    """

    rule_id: str
    reason: str
    root_cause_category: RootCauseCategory
    triggered_at: str = Field(default_factory=utc_timestamp)
    matched_value: str = ""
    evidence_files: list[str] = Field(default_factory=list)


class LiveCaseSpec(BaseModel):
    """
    目的：声明单个 live/precheck case 的固定规格，避免运行时临时拼装参数。
    功能：描述 case 类型、执行函数、环境依赖、超时和是否使用 fixture。
    实现逻辑：由 `live_cases.py` 集中构造完整 case matrix，父子进程都只读取此模型。
    可调参数：`required_env_vars`、`timeout_seconds`、`idle_timeout_seconds`、`runner_name` 和 `runner_kwargs`。
    默认参数及原因：`uses_fixtures` 默认关闭，原因是只有组件级隔离 case 才需要明确声明 fixture 依赖。
    """

    case_id: str
    case_kind: CaseKind
    description: str
    runner_name: str
    timeout_seconds: int
    idle_timeout_seconds: int
    required_env_vars: list[str] = Field(default_factory=list)
    uses_fixtures: bool = False
    runner_kwargs: dict[str, str] = Field(default_factory=dict)


class LiveCaseResult(BaseModel):
    """
    目的：统一描述单个 case 的最终执行结果，供父子进程、汇总和 backlog 生成复用。
    功能：记录状态、耗时、错误、证据路径、输入边界和监控告警。
    实现逻辑：子进程在正常完成或异常失败时写出，父进程在截停或跳过时补写。
    可调参数：状态字段、证据路径集合、输入边界字段和 `alerts` 列表。
    默认参数及原因：多数路径和说明字段默认空值，原因是不同 case 的产物边界不同，不能强行预设。
    """

    case_id: str
    case_kind: CaseKind
    description: str
    status: CaseStatus
    started_at: str = Field(default_factory=utc_timestamp)
    finished_at: str = Field(default_factory=utc_timestamp)
    duration_seconds: float = 0.0
    exit_code: int | None = None
    error_message: str = ""
    root_cause_category: RootCauseCategory | None = None
    case_dir: str = ""
    runtime_root_dir: str = ""
    run_root_dir: str = ""
    run_manifest_path: str = ""
    console_log_path: str = ""
    flow_log_path: str = ""
    checkpoint_paths: list[str] = Field(default_factory=list)
    crew_log_paths: list[str] = Field(default_factory=list)
    expected_output_paths: list[str] = Field(default_factory=list)
    missing_output_paths: list[str] = Field(default_factory=list)
    required_env_vars: list[str] = Field(default_factory=list)
    missing_env_vars: list[str] = Field(default_factory=list)
    allowed_input_keys: list[str] = Field(default_factory=list)
    observed_input_keys: list[str] = Field(default_factory=list)
    extra_input_keys: list[str] = Field(default_factory=list)
    alerts: list[LoopAlert] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SuiteSummary(BaseModel):
    """
    目的：承载整轮 suite 的聚合结果，作为 JSON/Markdown 汇总和 backlog 生成的统一输入。
    功能：记录 suite 元信息、所有 case 结果和状态计数。
    实现逻辑：父进程在所有 case 结束后统一计算并写出 `suite_summary.json`。
    可调参数：suite 名称、PDF 路径、结果列表和状态统计字典。
    默认参数及原因：`started_at`/`finished_at` 默认取当前时间，原因是即使中途失败也要保证 summary 可落盘。
    """

    suite_id: str
    suite_name: str
    pdf_file_path: str
    started_at: str = Field(default_factory=utc_timestamp)
    finished_at: str = Field(default_factory=utc_timestamp)
    case_results: list[LiveCaseResult] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)
