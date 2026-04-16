from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import automated_research_report_generator.main as main_module
from automated_research_report_generator.crews.due_diligence_crew.due_diligence_crew import (
    DueDiligenceCrew,
)
from automated_research_report_generator.crews.investment_thesis_crew.investment_thesis_crew import (
    InvestmentThesisCrew,
)
from automated_research_report_generator.crews.valuation_crew.valuation_crew import (
    ValuationCrew,
)
from automated_research_report_generator.crews.writeup_crew.writeup_crew import WriteupCrew
from automated_research_report_generator.flow import common as flow_common
from automated_research_report_generator.flow.common import (
    activate_run_preprocess_log,
    enable_test_fixture_runtime,
    ensure_directory,
    normalize_path,
    utc_timestamp,
)
from automated_research_report_generator.flow.research_flow import ResearchReportFlow
from automated_research_report_generator.testing.live_models import (
    LiveCaseResult,
    LiveCaseSpec,
)
from automated_research_report_generator.testing.live_monitor import (
    _classify_text_root_cause,
    activate_live_event_listener,
)

# 设计目的：集中声明 full suite 的 case matrix，并提供子进程 worker 真正执行各 case 的逻辑。
# 模块功能：负责 fixture 装载、运行目录隔离、Flow/crew 执行、结果校验和子进程级结果落盘。
# 实现逻辑：父进程只负责调度与监控，具体业务阶段调用全部收口到本模块的 runner 函数。
# 可调参数：suite case 列表、fixture 路径映射、各阶段 runner 和输出校验规则。
# 默认参数及原因：默认 full suite 包含预检、组件级和连接级三组 case，原因是需求明确要求每次全量运行。

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = PROJECT_ROOT / "test_src" / "live_fixtures"

ANALYSIS_FIXTURE_STATE_PATHS = {
    "history_background_pack_path": "analysis_packs/01_history_background_pack.md",
    "industry_pack_path": "analysis_packs/02_industry_pack.md",
    "business_pack_path": "analysis_packs/03_business_pack.md",
    "peer_info_pack_path": "analysis_packs/04_peer_info_pack.md",
    "finance_pack_path": "analysis_packs/05_finance_pack.md",
    "operating_metrics_pack_path": "analysis_packs/06_operating_metrics_pack.md",
    "risk_pack_path": "analysis_packs/07_risk_pack.md",
    "history_background_file_source_path": "analysis_sources/01_history_background_file_source.md",
    "history_background_search_source_path": "analysis_sources/01_history_background_search_source.md",
    "industry_file_source_path": "analysis_sources/02_industry_file_source.md",
    "industry_search_source_path": "analysis_sources/02_industry_search_source.md",
    "business_file_source_path": "analysis_sources/03_business_file_source.md",
    "business_search_source_path": "analysis_sources/03_business_search_source.md",
    "peer_info_peer_list_source_path": "analysis_sources/04_peer_info_peer_list.md",
    "peer_info_peer_data_source_path": "analysis_sources/04_peer_info_peer_data.md",
    "finance_file_source_path": "analysis_sources/05_finance_file_source.md",
    "finance_computed_metrics_path": "analysis_sources/05_finance_computed_metrics.md",
    "finance_analysis_path": "analysis_intermediate/05_finance_analysis.md",
    "operating_metrics_file_source_path": "analysis_sources/06_operating_metrics_file_source.md",
    "operating_metrics_search_source_path": "analysis_sources/06_operating_metrics_search_source.md",
    "operating_metrics_analysis_path": "analysis_intermediate/06_operating_metrics_analysis.md",
    "risk_file_source_path": "analysis_sources/07_risk_file_source.md",
    "risk_search_source_path": "analysis_sources/07_risk_search_source.md",
}

DOWNSTREAM_FIXTURE_STATE_PATHS = {
    "peers_pack_path": "valuation_packs/01_peers_pack.md",
    "intrinsic_value_pack_path": "valuation_packs/02_intrinsic_value_pack.md",
    "valuation_pack_path": "valuation_packs/03_valuation_pack.md",
    "diligence_questions_path": "diligence/08_diligence_questions.md",
}

FULL_SUITE_CASES = [
    LiveCaseSpec(
        case_id="offline_contract",
        case_kind="precheck",
        description="运行离线 pytest 基线，失败时阻断 live token 消耗。",
        runner_name="run_offline_contract",
        timeout_seconds=300,
        idle_timeout_seconds=300,
    ),
    LiveCaseSpec(
        case_id="env_preflight",
        case_kind="precheck",
        description="检查环境变量、默认 PDF、缓存可写和 crewai 版本一致性。",
        runner_name="run_env_preflight",
        timeout_seconds=120,
        idle_timeout_seconds=120,
    ),
    LiveCaseSpec(
        case_id="prepare_evidence_live",
        case_kind="component",
        description="真实运行 prepare_evidence 阶段。",
        runner_name="run_prepare_evidence_live",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
    ),
    LiveCaseSpec(
        case_id="history_background_live",
        case_kind="component",
        description="真实运行 history_background 单专题 analysis case。",
        runner_name="run_single_analysis_case",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY"],
        uses_fixtures=True,
        runner_kwargs={"topic_slug": "history_background"},
    ),
    LiveCaseSpec(
        case_id="industry_live",
        case_kind="component",
        description="真实运行 industry 单专题 analysis case。",
        runner_name="run_single_analysis_case",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY"],
        uses_fixtures=True,
        runner_kwargs={"topic_slug": "industry"},
    ),
    LiveCaseSpec(
        case_id="business_live",
        case_kind="component",
        description="真实运行 business 单专题 analysis case。",
        runner_name="run_single_analysis_case",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY"],
        uses_fixtures=True,
        runner_kwargs={"topic_slug": "business"},
    ),
    LiveCaseSpec(
        case_id="peer_info_live",
        case_kind="component",
        description="真实运行 peer_info 单专题 analysis case。",
        runner_name="run_single_analysis_case",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY", "TUSHARE_TOKEN"],
        uses_fixtures=True,
        runner_kwargs={"topic_slug": "peer_info"},
    ),
    LiveCaseSpec(
        case_id="financial_live",
        case_kind="component",
        description="真实运行 financial 单专题 analysis case。",
        runner_name="run_single_analysis_case",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY", "TUSHARE_TOKEN"],
        uses_fixtures=True,
        runner_kwargs={"topic_slug": "finance"},
    ),
    LiveCaseSpec(
        case_id="operating_metrics_live",
        case_kind="component",
        description="真实运行 operating_metrics 单专题 analysis case。",
        runner_name="run_single_analysis_case",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY"],
        uses_fixtures=True,
        runner_kwargs={"topic_slug": "operating_metrics"},
    ),
    LiveCaseSpec(
        case_id="risk_live",
        case_kind="component",
        description="真实运行 risk 单专题 analysis case。",
        runner_name="run_single_analysis_case",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY"],
        uses_fixtures=True,
        runner_kwargs={"topic_slug": "risk"},
    ),
    LiveCaseSpec(
        case_id="due_diligence_live",
        case_kind="component",
        description="真实运行尽调问题生成 component case。",
        runner_name="run_due_diligence_component",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY"],
        uses_fixtures=True,
    ),
    LiveCaseSpec(
        case_id="valuation_live",
        case_kind="component",
        description="真实运行估值 component case。",
        runner_name="run_valuation_component",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY", "TUSHARE_TOKEN"],
        uses_fixtures=True,
    ),
    LiveCaseSpec(
        case_id="investment_thesis_live",
        case_kind="component",
        description="真实运行 thesis component case。",
        runner_name="run_thesis_component",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY"],
        uses_fixtures=True,
    ),
    LiveCaseSpec(
        case_id="writeup_live",
        case_kind="component",
        description="真实运行 writeup component case。",
        runner_name="run_writeup_component",
        timeout_seconds=1200,
        idle_timeout_seconds=480,
        required_env_vars=["OPENROUTER_API_KEY"],
        uses_fixtures=True,
    ),
    LiveCaseSpec(
        case_id="analysis_chain_live",
        case_kind="chain",
        description="从 prepare_evidence 运行到完整 analysis 链路。",
        runner_name="run_analysis_chain_live",
        timeout_seconds=2700,
        idle_timeout_seconds=720,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY", "TUSHARE_TOKEN"],
    ),
    LiveCaseSpec(
        case_id="valuation_chain_live",
        case_kind="chain",
        description="从 prepare_evidence 运行到 valuation 链路。",
        runner_name="run_valuation_chain_live",
        timeout_seconds=2700,
        idle_timeout_seconds=720,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY", "TUSHARE_TOKEN"],
    ),
    LiveCaseSpec(
        case_id="thesis_chain_live",
        case_kind="chain",
        description="从 prepare_evidence 运行到 thesis 链路。",
        runner_name="run_thesis_chain_live",
        timeout_seconds=2700,
        idle_timeout_seconds=720,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY", "TUSHARE_TOKEN"],
    ),
    LiveCaseSpec(
        case_id="publish_chain_live",
        case_kind="chain",
        description="从 prepare_evidence 运行到 publish/writeup 完整链路。",
        runner_name="run_publish_chain_live",
        timeout_seconds=2700,
        idle_timeout_seconds=720,
        required_env_vars=["OPENROUTER_API_KEY", "SERPER_API_KEY", "TUSHARE_TOKEN"],
    ),
]


class LiveCaseRuntime:
    """
    目的：统一承载单个 worker case 运行期需要共享的目录、PDF、fixture 和当前 Flow 上下文。
    功能：为 runner 函数提供稳定的路径和状态访问入口。
    实现逻辑：把 suite/case 级固定路径在构造时解析好，避免每个 runner 重复拼接。
    可调参数：suite id、case 目录、运行根目录和目标 PDF 路径。
    默认参数及原因：默认把 runtime 根目录放在 `case_dir/runtime`，原因是 suite 证据和真实运行产物需要隔离但共处一处。
    """

    def __init__(self, *, suite_id: str, spec: LiveCaseSpec, case_dir: Path, pdf_path: Path) -> None:
        """
        目的：初始化单个 case 的运行期上下文。
        功能：解析 case 目录、runtime 目录、fixture 目录和目标 PDF 路径。
        实现逻辑：只保存最小公共状态，`current_flow` 在 runner 真正创建 Flow 时再赋值。
        可调参数：`suite_id`、`spec`、`case_dir` 和 `pdf_path`。
        默认参数及原因：默认立即创建 `case_dir`，原因是 worker 一启动就要写 case 元数据和事件文件。
        """

        self.suite_id = suite_id
        self.spec = spec
        self.case_dir = ensure_directory(Path(case_dir).expanduser().resolve())
        self.runtime_root_dir = ensure_directory(self.case_dir / "runtime")
        self.monitor_dir = ensure_directory(self.case_dir / "monitor")
        self.pdf_path = Path(pdf_path).expanduser().resolve()
        self.fixture_root = FIXTURE_ROOT
        self.current_flow: ResearchReportFlow | None = None

    def event_log_path(self) -> Path:
        """
        目的：返回当前 case 的事件 JSONL 标准路径。
        功能：供 worker 激活事件监听器并写入运行期事件。
        实现逻辑：固定使用 `case_dir/monitor/events.jsonl`。
        可调参数：当前无显式参数。
        默认参数及原因：默认放在 monitor 目录，原因是它属于监控证据而不是业务产物。
        """

        return self.monitor_dir / "events.jsonl"

    def result_path(self) -> Path:
        """
        目的：返回当前 case 结果 JSON 的标准路径。
        功能：供 worker 在完成后写出结构化结果。
        实现逻辑：固定使用 `case_dir/case_result.json`。
        可调参数：当前无显式参数。
        默认参数及原因：默认放在 case 根目录，原因是父进程读取时最直接。
        """

        return self.case_dir / "case_result.json"


def build_suite_specs(suite_name: str) -> list[LiveCaseSpec]:
    """
    目的：按 suite 名称返回当前仓库支持的完整 case matrix。
    功能：让父进程只通过 suite 名称而不是硬编码 case 列表驱动整轮执行。
    实现逻辑：当前仅支持 `full`，因此直接返回固定列表副本。
    可调参数：`suite_name`。
    默认参数及原因：默认只暴露 `full`，原因是当前需求已经明确每次全量执行。
    """

    if suite_name != "full":
        raise ValueError(f"Unsupported live suite: {suite_name}")
    return [spec.model_copy(deep=True) for spec in FULL_SUITE_CASES]


def get_case_spec(case_id: str) -> LiveCaseSpec:
    """
    目的：按 case id 查找固定的 case 规格。
    功能：供父进程调度和子进程 worker 共享同一份 case 声明。
    实现逻辑：顺序扫描 full suite 列表并返回首个匹配项。
    可调参数：`case_id`。
    默认参数及原因：找不到时直接抛错，原因是 case id 必须是稳定契约，不应静默回退。
    """

    for spec in FULL_SUITE_CASES:
        if spec.case_id == case_id:
            return spec.model_copy(deep=True)
    raise KeyError(f"Unknown live case id: {case_id}")


def _patch_case_cache_root(runtime: LiveCaseRuntime) -> None:
    """
    目的：把当前 worker case 的运行缓存根目录定向到 `case_dir/runtime`。
    功能：确保 prepare_evidence、manifest、日志和最终产物都落在当前 case 隔离目录下。
    实现逻辑：同步覆盖 `flow.common` 与 `main` 模块里引用的 `CACHE_ROOT`，并重置日志状态。
    可调参数：`runtime`。
    默认参数及原因：默认每个 case 单独隔离一份 cache root，原因是 suite 需要可并行复盘和避免相互污染。
    """

    flow_common.CACHE_ROOT = runtime.runtime_root_dir
    main_module.CACHE_ROOT = runtime.runtime_root_dir
    ensure_directory(flow_common.CACHE_ROOT)
    flow_common.reset_runtime_logging_state()


def _fixture_path(relative_path: str) -> str:
    """
    目的：把 fixture 相对路径转换成当前仓库中的绝对 UTF-8 文件路径。
    功能：供 state 装载和结果校验复用。
    实现逻辑：基于模块级 `FIXTURE_ROOT` 拼接后转成标准化路径。
    可调参数：`relative_path`。
    默认参数及原因：默认使用仓库内 fixture 根目录，原因是 suite 需要稳定、可提交的最小上游输入。
    """

    return normalize_path(FIXTURE_ROOT / relative_path)


def _write_json(path: Path, payload: Any) -> str:
    """
    目的：为 worker 元数据、边界记录和错误现场提供统一的 UTF-8 JSON 落盘 helper。
    功能：确保父目录存在并把任意可 JSON 化对象写到目标路径。
    实现逻辑：固定使用 `ensure_ascii=False` 和缩进格式输出。
    可调参数：`path` 和 `payload`。
    默认参数及原因：默认格式化写盘，原因是这些文件主要用于人工排查和后续汇总读取。
    """

    resolved = Path(path).expanduser().resolve()
    ensure_directory(resolved.parent)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return normalize_path(resolved)


def _bootstrap_manual_flow(runtime: LiveCaseRuntime) -> ResearchReportFlow:
    """
    目的：为不需要执行 prepare_evidence 的下游 component case 构造一份最小可运行 Flow 上下文。
    功能：创建独立 run 目录、stub metadata/page index，并回填基础 state 字段。
    实现逻辑：直接实例化 `ResearchReportFlow`，再手动设置 `run_slug`、`run_cache_dir` 和基础路径。
    可调参数：`runtime`。
    默认参数及原因：默认 run slug 固定为 `worker-run`，原因是下游组件 case 不需要真实公司名来命名目录。
    """

    flow = ResearchReportFlow()
    runtime.current_flow = flow
    flow.state.run_slug = "worker-run"
    run_root_dir = ensure_directory(flow_common.CACHE_ROOT / flow.state.run_slug)
    artifact_dir = ensure_directory(run_root_dir / "md")
    ensure_directory(run_root_dir / "logs")
    activate_run_preprocess_log(flow.state.run_slug)

    flow.state.company_name = "Live Fixture Company"
    flow.state.industry = "Live Fixture Industry"
    flow.state.pdf_file_path = runtime.pdf_path.as_posix()
    flow.state.run_cache_dir = artifact_dir.as_posix()
    flow.state.run_output_dir = artifact_dir.as_posix()
    flow.state.analysis_source_dir = _fixture_path("analysis_sources")
    flow.state.document_metadata_file_path = normalize_path(artifact_dir / "document_metadata_stub.json")
    flow.state.page_index_file_path = normalize_path(artifact_dir / "page_index_stub.json")
    flow.state.final_report_markdown_path = normalize_path(artifact_dir / f"{runtime.pdf_path.stem}_v2_report.md")
    flow.state.final_report_pdf_path = normalize_path(artifact_dir / f"{runtime.pdf_path.stem}_v2_report.pdf")
    flow.state.pitch_material_markdown_path = normalize_path(
        artifact_dir / f"{runtime.pdf_path.stem}_pitch_material.md"
    )
    flow.state.investment_snapshot_ppt_path = normalize_path(
        artifact_dir / f"{runtime.pdf_path.stem}_investment_snapshot.pptx"
    )

    Path(flow.state.document_metadata_file_path).write_text(
        json.dumps({"company_name": flow.state.company_name, "industry": flow.state.industry}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(flow.state.page_index_file_path).write_text("[]", encoding="utf-8")
    flow._log_flow("manual live flow initialized")
    flow._write_manifest_from_state("initialized")
    return flow


def _prepare_flow_with_real_evidence(runtime: LiveCaseRuntime) -> ResearchReportFlow:
    """
    目的：为 analysis/component 与 chain case 准备真实 `prepare_evidence` 产出的 Flow 上下文。
    功能：执行真实 prepare_evidence，得到 page index、document metadata、run manifest 和 run 目录。
    实现逻辑：先实例化 Flow 并注入 PDF 路径，再直接调用 `prepare_evidence()`。
    可调参数：`runtime`。
    默认参数及原因：默认每个 case 都重新跑一遍 prepare_evidence，原因是组件级 case 需要互相隔离。
    """

    flow = ResearchReportFlow()
    runtime.current_flow = flow
    flow.state.pdf_file_path = runtime.pdf_path.as_posix()
    flow.prepare_evidence()
    return flow


def _load_fixture_state(flow: ResearchReportFlow) -> None:
    """
    目的：把仓库内最小 fixture 文件集合批量装载到 Flow state，供下游 component case 复用。
    功能：统一回填 analysis packs、analysis sources、valuation packs 和 diligence 文件路径。
    实现逻辑：直接遍历固定映射，把绝对路径写回对应 state 字段。
    可调参数：`flow`。
    默认参数及原因：默认一次性全量装载，原因是这样最简单且不会影响真实输入边界，真正注入哪些键仍由 runner 决定。
    """

    for state_attr, relative_path in ANALYSIS_FIXTURE_STATE_PATHS.items():
        setattr(flow.state, state_attr, _fixture_path(relative_path))
    for state_attr, relative_path in DOWNSTREAM_FIXTURE_STATE_PATHS.items():
        setattr(flow.state, state_attr, _fixture_path(relative_path))
    flow.state.analysis_source_dir = _fixture_path("analysis_sources")


def _find_analysis_spec(flow: ResearchReportFlow, topic_slug: str) -> dict[str, Any]:
    """
    目的：从生产 Flow 的专题元数据定义中定位当前 component case 对应的专题 spec。
    功能：避免测试入口自行维护另一套专题布局。
    实现逻辑：直接遍历 `flow._analysis_pack_specs()` 并按 `topic_slug` 匹配。
    可调参数：`flow` 和 `topic_slug`。
    默认参数及原因：找不到直接抛错，原因是专题 case id 和生产 spec 必须强一致。
    """

    for spec in flow._analysis_pack_specs():
        if str(spec["topic_slug"]) == topic_slug:
            return spec
    raise KeyError(f"Unknown analysis topic slug: {topic_slug}")


def _collect_checkpoint_paths(flow: ResearchReportFlow) -> list[str]:
    """
    目的：统一收集当前 run 目录下已经生成的 checkpoint 路径。
    功能：供 worker 结果校验和父进程 summary 直接引用。
    实现逻辑：递归扫描 `run_cache_dir/checkpoints/*.json` 并标准化路径。
    可调参数：`flow`。
    默认参数及原因：默认按文件系统结果收集，原因是不同 case 的 checkpoint 数量并不固定。
    """

    if not flow.state.run_cache_dir:
        return []
    checkpoint_dir = Path(flow.state.run_cache_dir).expanduser().resolve() / "checkpoints"
    if not checkpoint_dir.exists():
        return []
    return [normalize_path(path) for path in sorted(checkpoint_dir.glob("*.json")) if path.is_file()]


def _write_input_boundary(case_dir: Path, *, allowed_keys: list[str], observed_keys: list[str]) -> str:
    """
    目的：为组件级 case 固定保存一次输入边界记录，便于确认测试入口没有越界注入额外输入。
    功能：写出允许键、实际键和额外键差集。
    实现逻辑：在 case 目录生成 `input_boundary.json`。
    可调参数：`case_dir`、`allowed_keys` 和 `observed_keys`。
    默认参数及原因：默认把 extra 键显式写出，原因是边界错误最怕被静默吞掉。
    """

    allowed_set = sorted(set(allowed_keys))
    observed_set = sorted(set(observed_keys))
    extra_keys = sorted(set(observed_set) - set(allowed_set))
    return _write_json(
        case_dir / "input_boundary.json",
        {
            "allowed_keys": allowed_set,
            "observed_keys": observed_set,
            "extra_keys": extra_keys,
        },
    )


def _record_case_note(case_dir: Path, message: str) -> str:
    """
    目的：给单个 case 追加简短执行笔记，帮助后续人工快速理解当前路径。
    功能：向 `case_notes.txt` 追加一条带时间戳的文本。
    实现逻辑：复用 UTF-8 文本追加写入。
    可调参数：`case_dir` 和 `message`。
    默认参数及原因：默认笔记文件保存在 case 根目录，原因是它属于 case 级元信息而非运行时日志。
    """

    note_path = Path(case_dir).expanduser().resolve() / "case_notes.txt"
    ensure_directory(note_path.parent)
    with note_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_timestamp()} {message}\n")
    return normalize_path(note_path)


def _collect_runtime_summary(flow: ResearchReportFlow, console_log_path: str) -> dict[str, Any]:
    """
    目的：把当前 Flow 运行后的关键路径和日志索引收口成统一摘要。
    功能：提取 run root、manifest、flow/console/crew log 和 checkpoint 列表。
    实现逻辑：优先从 state 和 manifest 读取，缺失时再按 run 目录推导。
    可调参数：`flow` 和 `console_log_path`。
    默认参数及原因：默认尽量保留所有已知路径，原因是 suite 汇总和修复 backlog 需要直接引用它们。
    """

    run_root_dir = ""
    if flow.state.run_slug:
        run_root_dir = normalize_path(flow_common.CACHE_ROOT / flow.state.run_slug)

    manifest_path = flow.state.run_debug_manifest_path
    manifest_payload: dict[str, Any] = {}
    if manifest_path and Path(manifest_path).exists():
        manifest_payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    crew_log_paths = []
    if manifest_payload.get("crew_log_paths"):
        crew_log_paths = [
            value for value in manifest_payload["crew_log_paths"].values() if isinstance(value, str)
        ]

    flow_log_path = ""
    if manifest_payload.get("flow_log_file_path"):
        flow_log_path = str(manifest_payload["flow_log_file_path"])
    elif flow.state.run_slug:
        flow_log_path = normalize_path(flow_common.run_log_dir(flow.state.run_slug) / "flow.txt")

    return {
        "run_root_dir": run_root_dir,
        "run_manifest_path": manifest_path,
        "flow_log_path": flow_log_path,
        "console_log_path": console_log_path,
        "crew_log_paths": crew_log_paths,
        "checkpoint_paths": _collect_checkpoint_paths(flow),
        "manifest_payload": manifest_payload,
    }


def _validate_success_artifacts(
    *,
    flow: ResearchReportFlow,
    runtime_summary: dict[str, Any],
    expected_output_paths: list[str],
) -> tuple[list[str], list[str]]:
    """
    目的：对成功完成的 worker case 执行统一的最小结果验收。
    功能：检查预期输出、manifest、console/flow 日志和 checkpoint 是否存在且非空。
    实现逻辑：把所有必须存在的路径收口后统一扫描，并补充 manifest 成功态一致性校验。
    可调参数：`flow`、`runtime_summary` 和 `expected_output_paths`。
    默认参数及原因：默认把缺失项直接返回给调用方，原因是 result 需要准确记录哪一层证据断了。
    """

    missing_paths: list[str] = []
    required_paths = [
        path
        for path in [
            runtime_summary.get("run_manifest_path", ""),
            runtime_summary.get("console_log_path", ""),
            runtime_summary.get("flow_log_path", ""),
            *runtime_summary.get("checkpoint_paths", []),
            *expected_output_paths,
        ]
        if path
    ]
    for raw_path in required_paths:
        resolved = Path(raw_path).expanduser().resolve()
        if not resolved.exists() or resolved.stat().st_size == 0:
            missing_paths.append(normalize_path(resolved))

    notes: list[str] = []
    manifest_payload = runtime_summary.get("manifest_payload") or {}
    if manifest_payload:
        if any(
            str(manifest_payload.get(field, "")).strip()
            for field in ("failed_stage", "failed_crew", "error_message")
        ):
            notes.append("manifest 在成功态下仍保留失败字段，已视为不一致。")
            missing_paths.append(str(runtime_summary.get("run_manifest_path", "")))
    else:
        notes.append("未读取到 manifest 内容。")

    if not runtime_summary.get("checkpoint_paths"):
        notes.append("当前 case 未生成任何 checkpoint。")
        missing_paths.append("<missing-checkpoint>")

    return missing_paths, notes


def _build_result(
    *,
    runtime: LiveCaseRuntime,
    flow: ResearchReportFlow | None,
    status: str,
    started_at: str,
    console_log_path: str,
    error_message: str = "",
    expected_output_paths: list[str] | None = None,
    allowed_input_keys: list[str] | None = None,
    observed_input_keys: list[str] | None = None,
    notes: list[str] | None = None,
    duration_seconds: float = 0.0,
) -> LiveCaseResult:
    """
    目的：把 worker 内部收集到的运行结果组装成稳定的 `LiveCaseResult`。
    功能：统一填充 case 元信息、路径索引、输入边界和错误说明。
    实现逻辑：先从 Flow 提取运行摘要，再补充调用方传入的 expected/notes 字段。
    可调参数：运行上下文、Flow、状态、错误信息和输入边界列表。
    默认参数及原因：默认所有可选列表回退为空，原因是不同 case 的产物和边界并不完全一致。
    """

    runtime_summary = (
        _collect_runtime_summary(flow, console_log_path) if flow is not None else {
            "run_root_dir": "",
            "run_manifest_path": "",
            "flow_log_path": "",
            "console_log_path": console_log_path,
            "crew_log_paths": [],
            "checkpoint_paths": [],
            "manifest_payload": {},
        }
    )
    allowed_keys = sorted(set(allowed_input_keys or []))
    observed_keys = sorted(set(observed_input_keys or []))
    extra_keys = sorted(set(observed_keys) - set(allowed_keys))
    result = LiveCaseResult(
        case_id=runtime.spec.case_id,
        case_kind=runtime.spec.case_kind,
        description=runtime.spec.description,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        finished_at=utc_timestamp(),
        duration_seconds=duration_seconds,
        error_message=error_message,
        root_cause_category=(
            _classify_text_root_cause(error_message) if error_message else None
        ),
        case_dir=normalize_path(runtime.case_dir),
        runtime_root_dir=normalize_path(runtime.runtime_root_dir),
        run_root_dir=str(runtime_summary.get("run_root_dir", "")),
        run_manifest_path=str(runtime_summary.get("run_manifest_path", "")),
        console_log_path=str(runtime_summary.get("console_log_path", "")),
        flow_log_path=str(runtime_summary.get("flow_log_path", "")),
        checkpoint_paths=list(runtime_summary.get("checkpoint_paths", [])),
        crew_log_paths=list(runtime_summary.get("crew_log_paths", [])),
        expected_output_paths=list(expected_output_paths or []),
        required_env_vars=list(runtime.spec.required_env_vars),
        allowed_input_keys=allowed_keys,
        observed_input_keys=observed_keys,
        extra_input_keys=extra_keys,
        notes=list(notes or []),
    )
    if flow is not None and status == "passed":
        missing_paths, validation_notes = _validate_success_artifacts(
            flow=flow,
            runtime_summary=runtime_summary,
            expected_output_paths=list(expected_output_paths or []),
        )
        result.missing_output_paths = missing_paths
        result.notes.extend(validation_notes)
        if missing_paths or extra_keys:
            result.status = "failed"
            if extra_keys and not result.error_message:
                result.error_message = f"组件输入边界越界：{', '.join(extra_keys)}"
                result.root_cause_category = "artifact_wiring_error"
            elif missing_paths and not result.error_message:
                result.error_message = f"成功态缺少关键产物：{', '.join(missing_paths)}"
                result.root_cause_category = "artifact_wiring_error"
    return result


def _run_single_analysis_pack(
    flow: ResearchReportFlow,
    *,
    topic_slug: str,
    case_dir: Path,
) -> dict[str, Any]:
    """
    目的：为单专题 component case 复用生产 analysis pack 的真实输入拼装与产物回填逻辑。
    功能：执行指定专题 crew，并生成 pack/source 输出、manifest、checkpoint 和输入边界记录。
    实现逻辑：从生产 `spec` 中读取路径布局后，复制 `_run_analysis_stage()` 的当前专题分支。
    可调参数：`flow`、`topic_slug` 和 `case_dir`。
    默认参数及原因：默认只执行一个专题而不串 due diligence，原因是这里要测试组件内行为而非整条 analysis 链路。
    """

    analysis_dir = flow._stage_iteration_dir("analysis")
    source_dir = ensure_directory(analysis_dir / "sources")
    flow.state.analysis_source_dir = source_dir.as_posix()
    spec = _find_analysis_spec(flow, topic_slug)

    output_path = (analysis_dir / str(spec["output_file_name"])).as_posix()
    file_source_output_path = (source_dir / str(spec["file_source_output_file_name"])).as_posix()
    has_search_source = bool(spec["search_source_output_file_name"])
    search_source_output_path = (
        (source_dir / str(spec["search_source_output_file_name"])).as_posix()
        if has_search_source
        else ""
    )
    extra_output_paths = {
        input_key: (analysis_dir / output_file_name).as_posix()
        for input_key, output_file_name in dict(spec.get("extra_output_file_names", {})).items()
    }
    analysis_crew = flow._configure_crew_log(spec["crew_instance"], flow._crew_log_path(str(spec["crew_name"])))

    extra_inputs: dict[str, str] = {}
    if topic_slug == "peer_info":
        extra_inputs = {
            "industry_pack_text": flow._read(flow.state.industry_pack_path),
            "business_pack_text": flow._read(flow.state.business_pack_path),
            "peer_list_source_output_path": file_source_output_path,
            "peer_data_source_output_path": search_source_output_path,
        }
    elif topic_slug == "finance":
        extra_inputs = {
            "peer_info_peer_data_source_text": flow._read(flow.state.peer_info_peer_data_source_path),
            "industry_pack_text": flow._read(flow.state.industry_pack_path),
            "business_pack_text": flow._read(flow.state.business_pack_path),
            "finance_computed_metrics_output_path": extra_output_paths.get(
                "finance_computed_metrics_output_path",
                search_source_output_path,
            ),
            "finance_analysis_output_path": extra_output_paths.get("finance_analysis_output_path", ""),
        }
    elif topic_slug == "operating_metrics":
        extra_inputs = {
            "industry_pack_text": flow._read(flow.state.industry_pack_path),
            "business_pack_text": flow._read(flow.state.business_pack_path),
            "peer_info_peer_list_source_text": flow._read(flow.state.peer_info_peer_list_source_path),
            "operating_metrics_analysis_output_path": extra_output_paths.get(
                "operating_metrics_analysis_output_path",
                "",
            ),
        }
    elif topic_slug == "risk":
        extra_inputs = {
            "history_background_pack_text": flow._read(flow.state.history_background_pack_path),
            "business_pack_text": flow._read(flow.state.business_pack_path),
            "industry_pack_text": flow._read(flow.state.industry_pack_path),
            "finance_pack_text": flow._read(flow.state.finance_pack_path),
            "operating_metrics_pack_text": flow._read(flow.state.operating_metrics_pack_path),
        }

    inputs = flow._base_inputs() | {
        "owner_crew": str(spec["crew_name"]),
        "topic_slug": topic_slug,
        "pack_title": str(spec["pack_title"]),
        "pack_output_path": output_path,
    }
    if topic_slug == "peer_info":
        inputs = inputs | extra_inputs
    elif topic_slug in {"finance", "operating_metrics", "risk"}:
        inputs = inputs | {
            "file_source_output_path": file_source_output_path,
            "search_source_output_path": search_source_output_path,
        } | extra_inputs
    elif has_search_source:
        inputs = inputs | {
            "file_source_output_path": file_source_output_path,
            "search_source_output_path": search_source_output_path,
        }
    else:
        inputs = inputs | {"file_source_output_path": file_source_output_path}

    _write_input_boundary(
        case_dir,
        allowed_keys=sorted(inputs.keys()),
        observed_keys=sorted(inputs.keys()),
    )
    analysis_crew.crew().kickoff(inputs=inputs)

    setattr(flow.state, str(spec["state_attr"]), output_path)
    setattr(flow.state, str(spec["file_source_state_attr"]), file_source_output_path)
    if spec["search_source_state_attr"]:
        setattr(flow.state, str(spec["search_source_state_attr"]), search_source_output_path)
    for input_key, state_attr in dict(spec.get("extra_output_state_attrs", {})).items():
        setattr(flow.state, state_attr, extra_output_paths.get(input_key, ""))

    checkpoint_path = flow._write_checkpoint(
        f"cp_live_{topic_slug}",
        {
            "owner_crew": str(spec["crew_name"]),
            "topic_slug": topic_slug,
            "pack_output_path": output_path,
            "file_source_output_path": file_source_output_path,
            "search_source_output_path": search_source_output_path,
            **extra_output_paths,
        },
    )
    flow._write_manifest_from_state(f"{topic_slug}_completed")
    flow._log_flow(
        f"live component completed | topic_slug={topic_slug} | pack_output_path={output_path}"
    )
    return {
        "flow": flow,
        "expected_output_paths": [
            output_path,
            file_source_output_path,
            *([search_source_output_path] if search_source_output_path else []),
            *list(extra_output_paths.values()),
        ],
        "checkpoint_paths": [checkpoint_path],
        "allowed_input_keys": sorted(inputs.keys()),
        "observed_input_keys": sorted(inputs.keys()),
    }


def run_prepare_evidence_live(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行真实 prepare_evidence component case。
    功能：生成 document metadata、page index、run manifest 和初始 checkpoint。
    实现逻辑：直接调用生产 `prepare_evidence()`，不对阶段逻辑做任何包装。
    可调参数：`runtime`。
    默认参数及原因：默认使用命令行传入 PDF，原因是该阶段完全围绕真实 PDF 运行。
    """

    flow = _prepare_flow_with_real_evidence(runtime)
    return {
        "flow": flow,
        "expected_output_paths": [
            flow.state.document_metadata_file_path,
            flow.state.page_index_file_path,
            flow.state.run_debug_manifest_path,
        ],
        "allowed_input_keys": ["pdf_file_path"],
        "observed_input_keys": ["pdf_file_path"],
    }


def run_single_analysis_case(runtime: LiveCaseRuntime, *, topic_slug: str) -> dict[str, Any]:
    """
    目的：执行单个真实 analysis 专题 component case。
    功能：先准备真实 evidence，再按 fixture 补齐当前专题依赖的上游 pack/source，并运行指定专题 crew。
    实现逻辑：复用生产 spec 布局和专题输入拼装，只去掉整条 analysis 链路的其他专题与 diligence。
    可调参数：`runtime` 和 `topic_slug`。
    默认参数及原因：默认每个专题都独立跑一遍 prepare_evidence，原因是组件 case 之间不应相互依赖。
    """

    flow = _prepare_flow_with_real_evidence(runtime)
    _load_fixture_state(flow)
    return _run_single_analysis_pack(flow, topic_slug=topic_slug, case_dir=runtime.case_dir)


def run_due_diligence_component(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行真实 due_diligence component case。
    功能：使用 fixture pack/source 作为上游输入，运行尽调问题生成 crew。
    实现逻辑：构造最小 manual Flow 上下文后，按生产 `DueDiligenceCrew` 输入边界直接 kickoff。
    可调参数：`runtime`。
    默认参数及原因：默认使用 fixture 上游，原因是该 component case 不应依赖前序专题真的跑完。
    """

    flow = _bootstrap_manual_flow(runtime)
    _load_fixture_state(flow)
    analysis_dir = flow._stage_iteration_dir("analysis")
    diligence_output_path = (analysis_dir / "08_diligence_questions.md").as_posix()
    crew = flow._configure_crew_log(DueDiligenceCrew(), flow._crew_log_path("due_diligence_crew"))
    inputs = flow._base_inputs() | flow._due_diligence_inputs() | {"diligence_output_path": diligence_output_path}
    _write_input_boundary(runtime.case_dir, allowed_keys=sorted(inputs.keys()), observed_keys=sorted(inputs.keys()))
    crew.crew().kickoff(inputs=inputs)
    flow.state.diligence_questions_path = diligence_output_path
    checkpoint_path = flow._write_checkpoint("cp_live_due_diligence", {"diligence_output_path": diligence_output_path})
    flow._write_manifest_from_state("due_diligence_completed")
    return {
        "flow": flow,
        "expected_output_paths": [diligence_output_path],
        "checkpoint_paths": [checkpoint_path],
        "allowed_input_keys": sorted(inputs.keys()),
        "observed_input_keys": sorted(inputs.keys()),
    }


def run_valuation_component(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行真实 valuation component case。
    功能：使用 fixture 的专题 pack/source 输入生成三份估值包。
    实现逻辑：构造 manual Flow 后按生产 `_run_valuation_stage()` 的输入边界直接调用估值 crew。
    可调参数：`runtime`。
    默认参数及原因：默认不复用 analysis_chain 输出，原因是组件级估值测试需要与前序链路解耦。
    """

    flow = _bootstrap_manual_flow(runtime)
    _load_fixture_state(flow)
    valuation_dir = flow._stage_iteration_dir("valuation")
    crew = flow._configure_crew_log(ValuationCrew(), flow._crew_log_path("valuation_crew"))
    inputs = flow._base_inputs() | {
        "valuation_output_dir": valuation_dir.as_posix(),
        "peer_info_pack_text": flow._read(flow.state.peer_info_pack_path),
        "finance_pack_text": flow._read(flow.state.finance_pack_path),
        "operating_metrics_pack_text": flow._read(flow.state.operating_metrics_pack_path),
        "risk_pack_text": flow._read(flow.state.risk_pack_path),
        "peer_info_peer_data_source_text": flow._read(flow.state.peer_info_peer_data_source_path),
        "risk_search_source_text": flow._read(flow.state.risk_search_source_path),
    }
    _write_input_boundary(runtime.case_dir, allowed_keys=sorted(inputs.keys()), observed_keys=sorted(inputs.keys()))
    crew.crew().kickoff(inputs=inputs)
    flow.state.peers_pack_path = (valuation_dir / "01_peers_pack.md").as_posix()
    flow.state.intrinsic_value_pack_path = (valuation_dir / "02_intrinsic_value_pack.md").as_posix()
    flow.state.valuation_pack_path = (valuation_dir / "03_valuation_pack.md").as_posix()
    checkpoint_path = flow._write_checkpoint(
        "cp_live_valuation",
        {
            "peers_pack_path": flow.state.peers_pack_path,
            "intrinsic_value_pack_path": flow.state.intrinsic_value_pack_path,
            "valuation_pack_path": flow.state.valuation_pack_path,
        },
    )
    flow._write_manifest_from_state("valuation_completed")
    return {
        "flow": flow,
        "expected_output_paths": [
            flow.state.peers_pack_path,
            flow.state.intrinsic_value_pack_path,
            flow.state.valuation_pack_path,
        ],
        "checkpoint_paths": [checkpoint_path],
        "allowed_input_keys": sorted(inputs.keys()),
        "observed_input_keys": sorted(inputs.keys()),
    }


def run_thesis_component(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行真实 thesis component case。
    功能：使用 fixture 的 analysis/valuation/diligence 输入生成 bull、neutral、bear 和最终 thesis。
    实现逻辑：构造 manual Flow 后按生产 `_run_thesis_stage()` 的输入边界直接调用 thesis crew。
    可调参数：`runtime`。
    默认参数及原因：默认保留四份输出文件，原因是 thesis 组件验收必须覆盖三份立场稿和最终综合稿。
    """

    flow = _bootstrap_manual_flow(runtime)
    _load_fixture_state(flow)
    thesis_dir = flow._stage_iteration_dir("thesis")
    crew = flow._configure_crew_log(
        InvestmentThesisCrew(),
        flow._crew_log_path("investment_thesis_crew"),
    )
    inputs = flow._base_inputs() | {
        "thesis_output_dir": thesis_dir.as_posix(),
        "history_background_pack_text": flow._read(flow.state.history_background_pack_path),
        "industry_pack_text": flow._read(flow.state.industry_pack_path),
        "business_pack_text": flow._read(flow.state.business_pack_path),
        "peer_info_pack_text": flow._read(flow.state.peer_info_pack_path),
        "finance_pack_text": flow._read(flow.state.finance_pack_path),
        "operating_metrics_pack_text": flow._read(flow.state.operating_metrics_pack_path),
        "risk_pack_text": flow._read(flow.state.risk_pack_path),
        "peers_pack_text": flow._read(flow.state.peers_pack_path),
        "intrinsic_value_pack_text": flow._read(flow.state.intrinsic_value_pack_path),
        "valuation_pack_text": flow._read(flow.state.valuation_pack_path),
        "diligence_questions_text": flow._read(flow.state.diligence_questions_path),
    }
    _write_input_boundary(runtime.case_dir, allowed_keys=sorted(inputs.keys()), observed_keys=sorted(inputs.keys()))
    crew.crew().kickoff(inputs=inputs)
    flow.state.bull_thesis_path = (thesis_dir / "01_bull_thesis.md").as_posix()
    flow.state.neutral_thesis_path = (thesis_dir / "02_neutral_thesis.md").as_posix()
    flow.state.bear_thesis_path = (thesis_dir / "03_bear_thesis.md").as_posix()
    flow.state.investment_thesis_path = (thesis_dir / "04_investment_thesis.md").as_posix()
    checkpoint_path = flow._write_checkpoint(
        "cp_live_thesis",
        {
            "bull_thesis_path": flow.state.bull_thesis_path,
            "neutral_thesis_path": flow.state.neutral_thesis_path,
            "bear_thesis_path": flow.state.bear_thesis_path,
            "investment_thesis_path": flow.state.investment_thesis_path,
        },
    )
    flow._write_manifest_from_state("thesis_completed")
    return {
        "flow": flow,
        "expected_output_paths": [
            flow.state.bull_thesis_path,
            flow.state.neutral_thesis_path,
            flow.state.bear_thesis_path,
            flow.state.investment_thesis_path,
        ],
        "checkpoint_paths": [checkpoint_path],
        "allowed_input_keys": sorted(inputs.keys()),
        "observed_input_keys": sorted(inputs.keys()),
    }


def run_writeup_component(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行真实 writeup component case。
    功能：基于 fixture 上游状态先由 Flow 确定性拼装最终 Markdown，再调用 WriteupCrew 生成 pitch、snapshot 和 PDF。
    实现逻辑：复用生产 `_write_final_report_markdown()` 和 writeup crew 输入边界，但不依赖前序链路真实输出。
    可调参数：`runtime`。
    默认参数及原因：默认让 Flow 现场组装最终 Markdown，原因是 writeup 阶段本来就依赖该确定性拼装逻辑。
    """

    flow = _bootstrap_manual_flow(runtime)
    _load_fixture_state(flow)
    flow._write_final_report_markdown()
    flow._prepare_tool_context()
    crew = flow._configure_crew_log(WriteupCrew(), flow._crew_log_path("writeup_crew"))
    inputs = flow._base_inputs() | flow._writeup_stage_text_inputs() | {
        "final_report_markdown_path": flow.state.final_report_markdown_path,
        "final_report_pdf_path": flow.state.final_report_pdf_path,
        "pitch_material_markdown_path": flow.state.pitch_material_markdown_path,
        "investment_snapshot_ppt_path": flow.state.investment_snapshot_ppt_path,
    }
    _write_input_boundary(runtime.case_dir, allowed_keys=sorted(inputs.keys()), observed_keys=sorted(inputs.keys()))
    crew.crew().kickoff(inputs=inputs)
    checkpoint_path = flow._write_checkpoint(
        "cp_live_writeup",
        {
            "final_report_markdown_path": flow.state.final_report_markdown_path,
            "final_report_pdf_path": flow.state.final_report_pdf_path,
            "pitch_material_markdown_path": flow.state.pitch_material_markdown_path,
            "investment_snapshot_ppt_path": flow.state.investment_snapshot_ppt_path,
        },
    )
    flow._write_manifest_from_state("writeup_completed")
    return {
        "flow": flow,
        "expected_output_paths": [
            flow.state.final_report_markdown_path,
            flow.state.pitch_material_markdown_path,
            flow.state.investment_snapshot_ppt_path,
            flow.state.final_report_pdf_path,
        ],
        "checkpoint_paths": [checkpoint_path],
        "allowed_input_keys": sorted(inputs.keys()),
        "observed_input_keys": sorted(inputs.keys()),
    }


def run_analysis_chain_live(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行 prepare_evidence -> analysis 的真实连接级链路。
    功能：覆盖 evidence 准备、7 个专题 crew、diligence 和链路级 manifest/checkpoint。
    实现逻辑：直接串联生产 `prepare_evidence()` 和 `_run_analysis_stage()`。
    可调参数：`runtime`。
    默认参数及原因：默认使用真实 PDF 和真实 API，原因是连接级 case 的目标就是验证系统之间连通性。
    """

    flow = _prepare_flow_with_real_evidence(runtime)
    flow._run_analysis_stage()
    return {
        "flow": flow,
        "expected_output_paths": [flow.state.diligence_questions_path, *flow._analysis_source_paths().values()],
    }


def run_valuation_chain_live(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行 prepare_evidence -> analysis -> valuation 的真实连接级链路。
    功能：在 analysis 真输出基础上继续验证 valuation 输入接线和产物生成。
    实现逻辑：顺序调用生产 prepare、analysis、valuation 三段。
    可调参数：`runtime`。
    默认参数及原因：默认不跳过 analysis，原因是 valuation 的主要风险就在于跨阶段接线。
    """

    flow = _prepare_flow_with_real_evidence(runtime)
    flow._run_analysis_stage()
    flow._run_valuation_stage()
    return {
        "flow": flow,
        "expected_output_paths": [
            flow.state.diligence_questions_path,
            flow.state.peers_pack_path,
            flow.state.intrinsic_value_pack_path,
            flow.state.valuation_pack_path,
        ],
    }


def run_thesis_chain_live(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行 prepare_evidence -> analysis -> valuation -> thesis 的真实连接级链路。
    功能：覆盖完整 thesis 输入接线和四份 thesis 产物生成。
    实现逻辑：顺序调用生产四段，不引入额外测试专用逻辑。
    可调参数：`runtime`。
    默认参数及原因：默认保留四份 thesis 输出验收，原因是这一步最容易出现上下游包接线错误。
    """

    flow = _prepare_flow_with_real_evidence(runtime)
    flow._run_analysis_stage()
    flow._run_valuation_stage()
    flow._run_thesis_stage()
    return {
        "flow": flow,
        "expected_output_paths": [
            flow.state.diligence_questions_path,
            flow.state.peers_pack_path,
            flow.state.intrinsic_value_pack_path,
            flow.state.valuation_pack_path,
            flow.state.bull_thesis_path,
            flow.state.neutral_thesis_path,
            flow.state.bear_thesis_path,
            flow.state.investment_thesis_path,
        ],
    }


def run_publish_chain_live(runtime: LiveCaseRuntime) -> dict[str, Any]:
    """
    目的：执行 prepare_evidence -> analysis -> valuation -> thesis -> publish 的真实端到端连接级链路。
    功能：覆盖最终 Markdown 拼装、writeup crew 确认和 PDF 导出。
    实现逻辑：顺序调用生产各阶段方法，最后执行 `publish_if_passed()`。
    可调参数：`runtime`。
    默认参数及原因：默认验收最终 Markdown 和 PDF，原因是这是全量 live suite 的最终目标产物。
    """

    flow = _prepare_flow_with_real_evidence(runtime)
    flow._run_analysis_stage()
    flow._run_valuation_stage()
    flow._run_thesis_stage()
    flow.publish_if_passed()
    return {
        "flow": flow,
        "expected_output_paths": [
            flow.state.final_report_markdown_path,
            flow.state.pitch_material_markdown_path,
            flow.state.investment_snapshot_ppt_path,
            flow.state.final_report_pdf_path,
        ],
    }


CASE_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "run_prepare_evidence_live": run_prepare_evidence_live,
    "run_single_analysis_case": run_single_analysis_case,
    "run_due_diligence_component": run_due_diligence_component,
    "run_valuation_component": run_valuation_component,
    "run_thesis_component": run_thesis_component,
    "run_writeup_component": run_writeup_component,
    "run_analysis_chain_live": run_analysis_chain_live,
    "run_valuation_chain_live": run_valuation_chain_live,
    "run_thesis_chain_live": run_thesis_chain_live,
    "run_publish_chain_live": run_publish_chain_live,
}


def run_worker_case(
    *,
    suite_id: str,
    case_id: str,
    case_dir: Path,
    pdf_path: Path,
) -> LiveCaseResult:
    """
    目的：作为子进程 worker 真正执行单个 live case，并写出结构化 `case_result.json`。
    功能：完成 cache root 隔离、事件监听激活、stdout/stderr transcript、runner 调用和结果校验。
    实现逻辑：先初始化运行上下文和 transcript，再调用对应 runner；异常时统一写堆栈和失败结果。
    可调参数：suite id、case id、case 目录和目标 PDF 路径。
    默认参数及原因：默认所有 live case 都走这一入口，原因是父进程监控和结果读取都依赖统一协议。
    """

    enable_test_fixture_runtime()
    spec = get_case_spec(case_id)
    runtime = LiveCaseRuntime(suite_id=suite_id, spec=spec, case_dir=case_dir, pdf_path=pdf_path)
    _patch_case_cache_root(runtime)
    activate_live_event_listener(runtime.event_log_path())

    _write_json(
        runtime.case_dir / "worker_context.json",
        {
            "suite_id": suite_id,
            "case_id": case_id,
            "pdf_file_path": runtime.pdf_path.as_posix(),
            "runtime_root_dir": normalize_path(runtime.runtime_root_dir),
            "runner_name": spec.runner_name,
            "runner_kwargs": spec.runner_kwargs,
        },
    )

    started_at = utc_timestamp()
    started_perf = time.perf_counter()
    transcript = main_module.RunConsoleTranscript(
        run_slug_getter=lambda: getattr(getattr(runtime.current_flow, "state", SimpleNamespace(run_slug="")), "run_slug", ""),
        fallback_label=case_id,
    )
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = main_module.ConsoleStreamTee(original_stdout, transcript)
    sys.stderr = main_module.ConsoleStreamTee(original_stderr, transcript)

    outcome: dict[str, Any] | None = None
    failure_traceback = ""
    failure_message = ""
    try:
        print(f"[LiveWorker] running case={case_id}")
        runner = CASE_RUNNERS[spec.runner_name]
        outcome = runner(runtime, **spec.runner_kwargs)
    except Exception as exc:
        failure_message = str(exc)
        failure_traceback = traceback.format_exc()
        (runtime.case_dir / "worker_exception.txt").write_text(failure_traceback, encoding="utf-8")
    finally:
        console_log_path = transcript.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    duration_seconds = round(time.perf_counter() - started_perf, 3)
    if outcome is None:
        result = _build_result(
            runtime=runtime,
            flow=runtime.current_flow,
            status="failed",
            started_at=started_at,
            console_log_path=console_log_path,
            error_message=failure_message or "worker case failed",
            expected_output_paths=[],
            notes=[normalize_path(runtime.case_dir / "worker_exception.txt")] if failure_traceback else [],
            duration_seconds=duration_seconds,
        )
    else:
        result = _build_result(
            runtime=runtime,
            flow=outcome.get("flow"),
            status="passed",
            started_at=started_at,
            console_log_path=console_log_path,
            expected_output_paths=list(outcome.get("expected_output_paths", [])),
            allowed_input_keys=list(outcome.get("allowed_input_keys", [])),
            observed_input_keys=list(outcome.get("observed_input_keys", [])),
            notes=list(outcome.get("notes", [])),
            duration_seconds=duration_seconds,
        )

    result_path = runtime.result_path()
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    _record_case_note(runtime.case_dir, f"worker finished with status={result.status}")
    return result
