from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from automated_research_report_generator.flow.common import (
    CACHE_ROOT,
    DEFAULT_PDF_PATH,
    ensure_directory,
    normalize_path,
    utc_timestamp,
)
from automated_research_report_generator.testing.live_cases import (
    build_suite_specs,
    get_case_spec,
    run_worker_case,
)
from automated_research_report_generator.testing.live_models import (
    LiveCaseResult,
    SuiteSummary,
)
from automated_research_report_generator.testing.live_monitor import LoopGuardMonitor

# 设计目的：作为 live harness 的唯一 CLI 入口，负责父进程调度、子进程 worker 拉起与 suite 汇总。
# 模块功能：运行 precheck、调度全部 live case、执行失控监控、生成 summary 和 repair backlog。
# 实现逻辑：默认父进程模式按 suite 顺序执行；带 `--worker-case` 时切换为单 case 子进程模式。
# 可调参数：suite 名称、PDF 路径、worker case id、suite id 和 case 目录。
# 默认参数及原因：默认 suite 为 `full` 且 PDF 为仓库默认 PDF，原因是本轮需求明确要求每次全量执行。

LIVE_TEST_ROOT = CACHE_ROOT / "live_tests"


def _parse_args() -> argparse.Namespace:
    """
    目的：统一解析 live harness 的父进程和子进程 CLI 参数。
    功能：区分 suite 模式和 worker 模式，并收集 PDF、suite id、case 目录等路径参数。
    实现逻辑：在同一个 parser 中声明所有参数，再由主入口判断是否进入 worker 分支。
    可调参数：`--suite`、`--pdf`、`--worker-case`、`--suite-id` 和 `--case-dir`。
    默认参数及原因：默认 suite 为 `full` 且 PDF 为 `DEFAULT_PDF_PATH`，原因是这符合当前全量执行预期。
    """

    parser = argparse.ArgumentParser(description="Run the live API suite with loop guards and summaries.")
    parser.add_argument("--suite", default="full", help="Live suite name. Default: full")
    parser.add_argument("--pdf", default=DEFAULT_PDF_PATH.as_posix(), help="PDF path to analyze.")
    parser.add_argument("--worker-case", default="", help="Internal worker mode case id.")
    parser.add_argument("--suite-id", default="", help="Internal worker mode suite id.")
    parser.add_argument("--case-dir", default="", help="Internal worker mode case directory.")
    return parser.parse_args()


def _status_sort_key(status: str) -> tuple[int, str]:
    """
    目的：为 summary 和 backlog 生成稳定的状态排序顺序。
    功能：把不同状态映射到固定严重度级别。
    实现逻辑：先返回严重度整数，再返回状态名本身以保证同级稳定排序。
    可调参数：`status`。
    默认参数及原因：默认把 `aborted_loop_guard` 排到最高优先级，原因是它代表最需要先止血的运行失控。
    """

    order = {
        "aborted_loop_guard": 0,
        "failed": 1,
        "blocked_precheck": 1,
        "skipped_missing_env": 2,
        "passed": 3,
    }
    return order.get(status, 9), status


def _make_result(
    *,
    spec_case_id: str,
    spec_kind: str,
    description: str,
    status: str,
    case_dir: Path,
    started_at: str,
    error_message: str = "",
    duration_seconds: float = 0.0,
    missing_env_vars: list[str] | None = None,
    notes: list[str] | None = None,
) -> LiveCaseResult:
    """
    目的：为父进程本地产生的 precheck、skip 和 guard 截停结果提供统一构造入口。
    功能：填充最小 `LiveCaseResult` 字段并返回模型对象。
    实现逻辑：由调用方提供状态和错误文本，其余路径与列表字段按最小值初始化。
    可调参数：case 元信息、状态、目录、耗时、缺失环境变量和说明列表。
    默认参数及原因：默认把非 passed 状态的根因交给上层补充，原因是不同场景的诊断证据来源不同。
    """

    return LiveCaseResult(
        case_id=spec_case_id,
        case_kind=spec_kind,  # type: ignore[arg-type]
        description=description,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        finished_at=utc_timestamp(),
        duration_seconds=duration_seconds,
        case_dir=normalize_path(case_dir),
        runtime_root_dir=normalize_path(case_dir / "runtime"),
        error_message=error_message,
        missing_env_vars=list(missing_env_vars or []),
        notes=list(notes or []),
    )


def _fetch_pypi_crewai_version() -> str:
    """
    目的：从 PyPI 读取当前公开的 crewai 最新版本号，供 env_preflight 对照。
    功能：访问官方 JSON API 并返回 `info.version`。
    实现逻辑：使用标准库 `urllib.request` 发起最小 GET 请求，避免引入额外依赖。
    可调参数：当前无显式参数。
    默认参数及原因：默认直接请求 PyPI 官方接口，原因是它是版本检查的公开事实源。
    """

    with urllib.request.urlopen("https://pypi.org/pypi/crewai/json", timeout=15) as response:
        payload = json.load(response)
    return str(payload["info"]["version"])


def _run_offline_contract_case(case_dir: Path) -> LiveCaseResult:
    """
    目的：运行离线 pytest 基线，决定后续 live case 是否允许继续消耗 token。
    功能：执行 `python -m pytest -q test_src` 并把输出写到 case 目录。
    实现逻辑：使用当前 Python 解释器拉起 pytest，避免环境不一致。
    可调参数：`case_dir`。
    默认参数及原因：默认先跑整套 `test_src`，原因是这正是仓库当前离线基线契约。
    """

    started_at = utc_timestamp()
    started_perf = time.perf_counter()
    ensure_directory(case_dir)
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_src"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output_path = case_dir / "pytest_output.txt"
    output_path.write_text(process.stdout + "\n" + process.stderr, encoding="utf-8")
    status = "passed" if process.returncode == 0 else "failed"
    result = _make_result(
        spec_case_id="offline_contract",
        spec_kind="precheck",
        description="运行离线 pytest 基线，失败时阻断 live token 消耗。",
        status=status,
        case_dir=case_dir,
        started_at=started_at,
        error_message="" if process.returncode == 0 else "offline pytest baseline failed",
        duration_seconds=round(time.perf_counter() - started_perf, 3),
        notes=[normalize_path(output_path)],
    )
    result.exit_code = process.returncode
    if process.returncode != 0:
        result.root_cause_category = "unexpected_exception"
    return result


def _run_env_preflight_case(case_dir: Path, pdf_path: Path) -> LiveCaseResult:
    """
    目的：运行本地环境预检，检查 API key、默认 PDF、缓存可写和 crewai 版本一致性。
    功能：把检查结果落盘到 `env_preflight.json`，同时返回结构化结果。
    实现逻辑：收集本地环境信息并对 PyPI 版本进行联网核对。
    可调参数：`case_dir` 和 `pdf_path`。
    默认参数及原因：默认检查三类 API key，原因是 full suite 中的各 live case 会分别依赖它们。
    """

    started_at = utc_timestamp()
    started_perf = time.perf_counter()
    ensure_directory(case_dir)

    missing_env_vars = [
        env_name
        for env_name in ("OPENROUTER_API_KEY", "SERPER_API_KEY", "TUSHARE_TOKEN")
        if not os.getenv(env_name, "").strip()
    ]
    local_pdf_exists = pdf_path.exists()

    cache_probe_path = ensure_directory(case_dir / "runtime") / "writable_probe.txt"
    cache_probe_path.write_text("ok", encoding="utf-8")
    cache_writable = cache_probe_path.exists()

    local_version_process = subprocess.run(
        [sys.executable, "-c", "import crewai; print(crewai.__version__)"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    local_version = local_version_process.stdout.strip()

    pypi_version = ""
    pypi_error = ""
    try:
        pypi_version = _fetch_pypi_crewai_version()
    except Exception as exc:
        pypi_error = str(exc)

    payload = {
        "checked_at": utc_timestamp(),
        "pdf_file_path": normalize_path(pdf_path),
        "pdf_exists": local_pdf_exists,
        "cache_writable": cache_writable,
        "missing_env_vars": missing_env_vars,
        "local_crewai_version": local_version,
        "pypi_crewai_version": pypi_version,
        "pypi_error": pypi_error,
    }
    payload_path = case_dir / "env_preflight.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    notes = [normalize_path(payload_path)]
    errors = []
    if missing_env_vars:
        errors.append(f"missing env vars: {', '.join(missing_env_vars)}")
    if not local_pdf_exists:
        errors.append("default/live PDF path does not exist")
    if not cache_writable:
        errors.append(".cache live runtime root is not writable")
    if pypi_error:
        errors.append(f"failed to fetch PyPI version: {pypi_error}")
    if local_version != "1.14.1":
        errors.append(f"local crewai version is {local_version}, expected 1.14.1")
    if pypi_version and pypi_version != "1.14.1":
        errors.append(f"PyPI latest crewai version is {pypi_version}, expected 1.14.1")

    result = _make_result(
        spec_case_id="env_preflight",
        spec_kind="precheck",
        description="检查环境变量、默认 PDF、缓存可写和 crewai 版本一致性。",
        status="passed" if not errors else "failed",
        case_dir=case_dir,
        started_at=started_at,
        error_message="; ".join(errors),
        duration_seconds=round(time.perf_counter() - started_perf, 3),
        missing_env_vars=missing_env_vars,
        notes=notes,
    )
    if missing_env_vars:
        result.root_cause_category = "env_missing"
    elif errors:
        result.root_cause_category = "unexpected_exception"
    return result


def _scan_runtime_paths(case_dir: Path) -> dict[str, Any]:
    """
    目的：在父进程无法拿到子进程 Flow 对象时，直接从 case runtime 目录提取最小路径摘要。
    功能：收集 manifest、console/flow log、crew logs 和 checkpoint 列表。
    实现逻辑：递归扫描 `case_dir/runtime` 中的标准文件名模式。
    可调参数：`case_dir`。
    默认参数及原因：默认只依赖文件系统结果，原因是 guard 截停时不一定拿得到子进程 result json。
    """

    runtime_root = Path(case_dir).expanduser().resolve() / "runtime"
    manifests = sorted(runtime_root.rglob("run_manifest.json"))
    consoles = sorted(runtime_root.rglob("console.txt"))
    flows = sorted(runtime_root.rglob("flow.txt"))
    checkpoints = sorted(runtime_root.rglob("checkpoints/*.json"))
    crew_logs = [
        normalize_path(path)
        for path in sorted(runtime_root.rglob("logs/*.txt"))
        if path.name not in {"console.txt", "flow.txt", "preprocess.txt"}
    ]
    return {
        "runtime_root_dir": normalize_path(runtime_root),
        "run_manifest_path": normalize_path(manifests[0]) if manifests else "",
        "console_log_path": normalize_path(consoles[0]) if consoles else "",
        "flow_log_path": normalize_path(flows[0]) if flows else "",
        "checkpoint_paths": [normalize_path(path) for path in checkpoints],
        "crew_log_paths": crew_logs,
    }


def _run_live_case_parent(spec: Any, suite_id: str, case_dir: Path, pdf_path: Path) -> LiveCaseResult:
    """
    目的：在父进程中调度单个 live 子进程，并结合监控结果返回最终 case 结果。
    功能：拉起 worker 子进程、运行 LoopGuardMonitor、读取 child result 或补写 guard 结果。
    实现逻辑：子进程正常退出时以 `case_result.json` 为准；被截停或结果缺失时由父进程兜底构造。
    可调参数：case 规格、suite id、case 目录和 PDF 路径。
    默认参数及原因：默认使用当前解释器再执行本模块 worker 模式，原因是这样环境最一致、最简单。
    """

    started_at = utc_timestamp()
    started_perf = time.perf_counter()
    ensure_directory(case_dir)
    runtime_root = ensure_directory(case_dir / "runtime")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "automated_research_report_generator.testing.live_runner",
            "--worker-case",
            spec.case_id,
            "--suite-id",
            suite_id,
            "--case-dir",
            normalize_path(case_dir),
            "--pdf",
            normalize_path(pdf_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parents[3],
        text=True,
    )

    monitor = LoopGuardMonitor(spec=spec, case_dir=case_dir, runtime_root_dir=runtime_root)
    alert = monitor.watch_process(process)
    duration_seconds = round(time.perf_counter() - started_perf, 3)

    if alert is not None:
        runtime_summary = _scan_runtime_paths(case_dir)
        result = _make_result(
            spec_case_id=spec.case_id,
            spec_kind=spec.case_kind,
            description=spec.description,
            status="aborted_loop_guard",
            case_dir=case_dir,
            started_at=started_at,
            error_message=alert.reason,
            duration_seconds=duration_seconds,
            notes=list(alert.evidence_files),
        )
        result.root_cause_category = alert.root_cause_category
        result.runtime_root_dir = runtime_summary["runtime_root_dir"]
        result.run_manifest_path = runtime_summary["run_manifest_path"]
        result.console_log_path = runtime_summary["console_log_path"]
        result.flow_log_path = runtime_summary["flow_log_path"]
        result.checkpoint_paths = runtime_summary["checkpoint_paths"]
        result.crew_log_paths = runtime_summary["crew_log_paths"]
        result.alerts = [alert]
        result.exit_code = process.returncode
        return result

    process.wait(timeout=10)
    result_path = case_dir / "case_result.json"
    if result_path.exists():
        result = LiveCaseResult.model_validate_json(result_path.read_text(encoding="utf-8"))
        result.exit_code = process.returncode
        result.duration_seconds = duration_seconds
        result.finished_at = utc_timestamp()
        result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result

    runtime_summary = _scan_runtime_paths(case_dir)
    result = _make_result(
        spec_case_id=spec.case_id,
        spec_kind=spec.case_kind,
        description=spec.description,
        status="failed",
        case_dir=case_dir,
        started_at=started_at,
        error_message="worker exited without producing case_result.json",
        duration_seconds=duration_seconds,
        notes=[],
    )
    result.exit_code = process.returncode
    result.runtime_root_dir = runtime_summary["runtime_root_dir"]
    result.run_manifest_path = runtime_summary["run_manifest_path"]
    result.console_log_path = runtime_summary["console_log_path"]
    result.flow_log_path = runtime_summary["flow_log_path"]
    result.checkpoint_paths = runtime_summary["checkpoint_paths"]
    result.crew_log_paths = runtime_summary["crew_log_paths"]
    result.root_cause_category = "unexpected_exception"
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def _write_suite_summary_markdown(summary_path: Path, summary: SuiteSummary) -> str:
    """
    目的：把 suite 汇总结果转成便于人工快速扫描的 Markdown 摘要。
    功能：按固定严重度顺序列出各 case 的状态、耗时、错误和证据目录。
    实现逻辑：先写总览，再按状态分组渲染各 case。
    可调参数：`summary_path` 和 `summary`。
    默认参数及原因：默认把 `blocked_precheck` 并入 failed 分组展示，原因是用户要求的摘要顺序里没有单独这一栏。
    """

    ordered_groups = [
        ("aborted_loop_guard", "Aborted Loop Guard"),
        ("failed", "Failed"),
        ("skipped_missing_env", "Skipped Missing Env"),
        ("passed", "Passed"),
    ]
    lines = [
        f"# Live Suite Summary: {summary.suite_id}",
        "",
        f"- suite: {summary.suite_name}",
        f"- pdf: {summary.pdf_file_path}",
        f"- started_at: {summary.started_at}",
        f"- finished_at: {summary.finished_at}",
        f"- status_counts: {json.dumps(summary.status_counts, ensure_ascii=False)}",
    ]
    for status, title in ordered_groups:
        lines.append("")
        lines.append(f"## {title}")
        grouped = [
            result
            for result in summary.case_results
            if result.status == status or (status == "failed" and result.status == "blocked_precheck")
        ]
        if not grouped:
            lines.append("")
            lines.append("- 无")
            continue
        for result in sorted(grouped, key=lambda item: item.case_id):
            lines.append("")
            lines.append(f"- case_id: {result.case_id}")
            lines.append(f"- status: {result.status}")
            lines.append(f"- duration_seconds: {result.duration_seconds}")
            lines.append(f"- error_message: {result.error_message or '无'}")
            lines.append(f"- case_dir: {result.case_dir}")
    summary_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return normalize_path(summary_path)


def _suggest_fix(result: LiveCaseResult) -> tuple[str, str]:
    """
    目的：按结果状态和根因分类生成最小修复路径与建议补测。
    功能：为 repair backlog 提供统一的“最小修复点 + 预期补测”模板。
    实现逻辑：先按根因分类映射，再对未知情况回退到通用诊断建议。
    可调参数：`result`。
    默认参数及原因：默认建议只改最小接线点或 guard 配置，原因是当前策略明确要求避免过度修改业务 prompt。
    """

    category = result.root_cause_category or "unexpected_exception"
    if category == "env_missing":
        return ("补齐缺失环境变量后重跑受影响 case。", f"重跑 {result.case_id}，再补跑 publish_chain_live。")
    if category == "provider_context_overflow":
        return ("收窄阶段输入、减少重复 source 注入，必要时给目标 task 增加更强输出约束。", f"重跑 {result.case_id}，再补跑 publish_chain_live。")
    if category == "provider_rate_limit":
        return ("给相关 crew/agent 增加更稳妥的速率和重试控制。", f"重跑 {result.case_id}，再补跑 publish_chain_live。")
    if category == "tool_loop":
        return ("检查该 case 对应 crew 的工具调用边界，优先收紧工具输入和 `max_iter`。", f"重跑 {result.case_id}，再补跑 publish_chain_live。")
    if category == "prompt_loop":
        return ("检查任务描述是否诱发重复总结或反复重述，优先缩窄 prompt 与输出格式。", f"重跑 {result.case_id}，再补跑 publish_chain_live。")
    if category == "artifact_wiring_error":
        return ("检查 Flow state、fixture 路径和下游输入键名是否与生产链路一致。", f"补一条对应 contract test 后重跑 {result.case_id}。")
    if category == "timeout_no_progress":
        return ("优先检查挂住阶段的 flow/crew 日志，补更早的心跳和必要的超时保护。", f"重跑 {result.case_id}，再补跑 publish_chain_live。")
    return ("检查异常栈和 case 目录证据，优先修复最早失败点。", f"重跑 {result.case_id}。")


def _write_repair_backlog(backlog_path: Path, results: list[LiveCaseResult]) -> str:
    """
    目的：基于本轮 suite 的失败、截停和跳过结果生成一份最小修复 backlog。
    功能：按优先级列出 case、根因、证据路径、最小修复点和预期补测动作。
    实现逻辑：先按状态严重度排序，再逐条套用修复建议模板。
    可调参数：`backlog_path` 和 `results`。
    默认参数及原因：默认只为非 passed case 生成 backlog，原因是当前目标是面向修复而非复述成功项。
    """

    actionable_results = [result for result in results if result.status != "passed"]
    lines = ["# Repair Backlog", ""]
    if not actionable_results:
        lines.append("- 当前无待修复项。")
    else:
        for index, result in enumerate(
            sorted(actionable_results, key=lambda item: (_status_sort_key(item.status), item.case_id)),
            start=1,
        ):
            minimal_fix, rerun_plan = _suggest_fix(result)
            lines.append(f"## {index}. {result.case_id}")
            lines.append("")
            lines.append(f"- 当前状态：{result.status}")
            lines.append(f"- 最可能根因：{result.root_cause_category or 'unexpected_exception'}")
            lines.append(f"- 证据路径：{result.case_dir}")
            lines.append(f"- 最小修复点：{minimal_fix}")
            lines.append(f"- 预期新增或修改测试：{rerun_plan}")
            lines.append("")
    backlog_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return normalize_path(backlog_path)


def _run_parent_suite(suite_name: str, pdf_path: Path) -> SuiteSummary:
    """
    目的：在父进程中按顺序执行整轮 live suite，并生成最终汇总。
    功能：运行 precheck、跳过条件判断、live 子进程调度、summary 和 backlog 生成。
    实现逻辑：先准备 suite 目录与 case 列表，再按顺序执行并累计 `LiveCaseResult`。
    可调参数：`suite_name` 和 `pdf_path`。
    默认参数及原因：默认每次都运行 full suite，原因是当前需求已经明确取消轻量档。
    """

    suite_specs = build_suite_specs(suite_name)
    suite_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{suite_name}"
    suite_root = ensure_directory(LIVE_TEST_ROOT / suite_id)
    case_results: list[LiveCaseResult] = []
    summary_started_at = utc_timestamp()

    offline_spec = get_case_spec("offline_contract")
    offline_result = _run_offline_contract_case(suite_root / "cases" / offline_spec.case_id)
    case_results.append(offline_result)

    env_spec = get_case_spec("env_preflight")
    env_result = _run_env_preflight_case(suite_root / "cases" / env_spec.case_id, pdf_path)
    case_results.append(env_result)

    live_allowed = offline_result.status == "passed"
    for spec in suite_specs:
        if spec.case_kind == "precheck":
            continue
        case_dir = suite_root / "cases" / spec.case_id
        if not live_allowed:
            result = _make_result(
                spec_case_id=spec.case_id,
                spec_kind=spec.case_kind,
                description=spec.description,
                status="blocked_precheck",
                case_dir=case_dir,
                started_at=utc_timestamp(),
                error_message="offline_contract failed; live token consumption blocked",
                notes=[offline_result.case_dir],
            )
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "case_result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
            case_results.append(result)
            continue

        missing_env_vars = [env_name for env_name in spec.required_env_vars if not os.getenv(env_name, "").strip()]
        if missing_env_vars:
            result = _make_result(
                spec_case_id=spec.case_id,
                spec_kind=spec.case_kind,
                description=spec.description,
                status="skipped_missing_env",
                case_dir=case_dir,
                started_at=utc_timestamp(),
                error_message=f"missing env vars: {', '.join(missing_env_vars)}",
                missing_env_vars=missing_env_vars,
            )
            result.root_cause_category = "env_missing"
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "case_result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
            case_results.append(result)
            continue

        case_results.append(_run_live_case_parent(spec, suite_id, case_dir, pdf_path))

    status_counts: dict[str, int] = {}
    for result in case_results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    summary = SuiteSummary(
        suite_id=suite_id,
        suite_name=suite_name,
        pdf_file_path=normalize_path(pdf_path),
        started_at=summary_started_at,
        finished_at=utc_timestamp(),
        case_results=case_results,
        status_counts=status_counts,
    )
    (suite_root / "suite_summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    _write_suite_summary_markdown(suite_root / "suite_summary.md", summary)
    _write_repair_backlog(suite_root / "repair_backlog.md", case_results)
    return summary


def main() -> None:
    """
    目的：作为模块入口在父进程模式和 worker 模式之间做分流。
    功能：父进程模式输出 suite 目录；worker 模式只执行单 case 并落盘结果。
    实现逻辑：先解析参数，再根据 `--worker-case` 是否为空决定分支。
    可调参数：命令行参数由 `_parse_args()` 统一定义。
    默认参数及原因：默认进入父进程 suite 模式，原因是用户显式调用的是整轮 live harness。
    """

    args = _parse_args()
    pdf_path = Path(args.pdf).expanduser().resolve()
    if args.worker_case:
        run_worker_case(
            suite_id=args.suite_id,
            case_id=args.worker_case,
            case_dir=Path(args.case_dir),
            pdf_path=pdf_path,
        )
        return

    summary = _run_parent_suite(args.suite, pdf_path)
    print(normalize_path(LIVE_TEST_ROOT / summary.suite_id))


if __name__ == "__main__":
    main()
