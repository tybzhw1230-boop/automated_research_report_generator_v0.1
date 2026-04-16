from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from automated_research_report_generator.testing import live_cases
from automated_research_report_generator.testing.live_cases import (
    ANALYSIS_FIXTURE_STATE_PATHS,
    CASE_RUNNERS,
    DOWNSTREAM_FIXTURE_STATE_PATHS,
    FIXTURE_ROOT,
    build_suite_specs,
    run_worker_case,
)
from automated_research_report_generator.testing.live_models import (
    LiveCaseResult,
    LiveCaseSpec,
    SuiteSummary,
)
from automated_research_report_generator.testing.live_monitor import (
    LiveExecutionEventListener,
    LoopGuardMonitor,
    _classify_text_root_cause,
)
from automated_research_report_generator.testing.live_runner import (
    _write_repair_backlog,
    _write_suite_summary_markdown,
)


def test_full_live_suite_specs_cover_expected_case_matrix() -> None:
    """
    目的：锁定 full suite 的 case matrix，避免后续改动漏掉组件级或链路级 live case。
    功能：校验 case id 集合、总数和 runner 绑定关系。
    实现逻辑：读取 `build_suite_specs("full")` 的结果后，对照固定清单和 `CASE_RUNNERS` 断言。
    可调参数：当前无。
    默认参数及原因：固定检查 `full`，因为当前方案明确要求每次都跑全量 live suite。
    """

    specs = build_suite_specs("full")
    case_ids = {spec.case_id for spec in specs}

    assert len(specs) == 18
    assert case_ids == {
        "offline_contract",
        "env_preflight",
        "prepare_evidence_live",
        "history_background_live",
        "industry_live",
        "business_live",
        "peer_info_live",
        "financial_live",
        "operating_metrics_live",
        "risk_live",
        "due_diligence_live",
        "valuation_live",
        "investment_thesis_live",
        "writeup_live",
        "analysis_chain_live",
        "valuation_chain_live",
        "thesis_chain_live",
        "publish_chain_live",
    }
    assert {
        spec.runner_name
        for spec in specs
        if spec.case_kind != "precheck"
    }.issubset(CASE_RUNNERS.keys())


def test_live_fixture_inventory_matches_declared_paths() -> None:
    """
    目的：锁定 live fixture 声明和磁盘实际文件的一致性。
    功能：校验所有声明的 fixture 路径都存在、可按 UTF-8 读取且内容非空。
    实现逻辑：遍历 analysis 与 downstream 两组 fixture 映射，逐一检查文件。
    可调参数：当前无。
    默认参数及原因：直接读取仓库内 fixture 根目录，因为组件级 case 的隔离依赖这些最小上游输入。
    """

    assert FIXTURE_ROOT.exists()
    for relative_path in [
        *ANALYSIS_FIXTURE_STATE_PATHS.values(),
        *DOWNSTREAM_FIXTURE_STATE_PATHS.values(),
    ]:
        fixture_path = FIXTURE_ROOT / relative_path
        assert fixture_path.exists(), relative_path
        assert fixture_path.read_text(encoding="utf-8").strip(), relative_path


def test_live_event_listener_writes_structured_jsonl(tmp_path: Path) -> None:
    """
    目的：验证事件监听器会把 CrewAI 事件落成稳定 JSONL。
    功能：检查 tool 签名、输出哈希、事件键和预览内容是否被写入。
    实现逻辑：构造一个最小假事件对象，直接调用内部落盘方法后读取结果文件断言。
    可调参数：`tmp_path` 由 pytest 提供临时目录。
    默认参数及原因：使用最小假事件而不接入真实事件总线，因为这里要锁的是序列化协议而不是 CrewAI 本体。
    """

    event_log_path = tmp_path / "events.jsonl"
    listener = LiveExecutionEventListener(event_log_path=event_log_path)
    fake_event = SimpleNamespace(
        tool_name="SerperDevTool",
        tool_args={"query": "宁德时代 行业格局"},
        output={"summary": "行业集中度维持高位"},
        task_name="search_public_sources",
        agent_role="researcher",
    )

    listener._append_event("ToolUsageFinishedEvent", fake_event)

    payload = json.loads(event_log_path.read_text(encoding="utf-8").strip())
    assert payload["tool_name"] == "SerperDevTool"
    assert payload["tool_signature"].startswith("SerperDevTool:")
    assert payload["output_hash"]
    assert payload["event_key"].startswith("ToolUsageFinishedEvent:SerperDevTool:")
    assert "行业集中度" in payload["preview"]


def test_loop_guard_monitor_flags_repeated_tool_signature(tmp_path: Path) -> None:
    """
    目的：锁定 loop guard 的重复工具调用截停规则。
    功能：验证同一工具签名连续出现三次且期间没有任务完成时会触发告警。
    实现逻辑：构造一个最小 `LoopGuardMonitor`，连续喂入三条相同的 `ToolUsageStartedEvent`。
    可调参数：`tmp_path` 由 pytest 提供临时目录。
    默认参数及原因：使用组件级 case 规格，因为工具循环主要发生在组件执行阶段，验证成本也最低。
    """

    spec = LiveCaseSpec(
        case_id="demo_component",
        case_kind="component",
        description="demo",
        runner_name="noop",
        timeout_seconds=60,
        idle_timeout_seconds=60,
    )
    monitor = LoopGuardMonitor(spec=spec, case_dir=tmp_path, runtime_root_dir=tmp_path / "runtime")
    monitor.last_task_completion_at = time.time() - 10

    entry = {
        "event_name": "ToolUsageStartedEvent",
        "tool_signature": "SerperDevTool:abc123",
        "preview": "",
        "event_key": "ToolUsageStartedEvent:SerperDevTool:abc123",
    }

    assert monitor._evaluate_event_entry(entry) is None
    assert monitor._evaluate_event_entry(entry) is None
    alert = monitor._evaluate_event_entry(entry)

    assert alert is not None
    assert alert.rule_id == "repeated_tool_signature"
    assert alert.root_cause_category == "tool_loop"


def test_root_cause_classifier_covers_context_rate_limit_and_timeout() -> None:
    """
    目的：锁定最小根因分类逻辑，避免 summary/backlog 回归到无区分状态。
    功能：校验 context、限流和无进展文本会落到预期分类。
    实现逻辑：直接调用 `_classify_text_root_cause()` 做纯函数断言。
    可调参数：当前无。
    默认参数及原因：只覆盖三类最高频信号，因为这正是第一版 harness 最核心的自动诊断范围。
    """

    assert _classify_text_root_cause("maximum context length exceeded") == "provider_context_overflow"
    assert _classify_text_root_cause("429 rate limit reached") == "provider_rate_limit"
    assert _classify_text_root_cause("worker timeout with no progress") == "timeout_no_progress"


def test_suite_summary_and_repair_backlog_render_expected_sections(tmp_path: Path) -> None:
    """
    目的：锁定 suite 汇总和 repair backlog 的输出骨架。
    功能：检查状态分组、case 记录和修复建议是否按约定落盘。
    实现逻辑：构造四条不同状态的 `LiveCaseResult`，生成 Markdown 后断言关键区块和 case id。
    可调参数：`tmp_path` 由 pytest 提供临时目录。
    默认参数及原因：使用人工构造结果而不跑真实 suite，因为这里验证的是汇总协议而不是执行流程。
    """

    results = [
        LiveCaseResult(
            case_id="a_abort",
            case_kind="chain",
            description="abort",
            status="aborted_loop_guard",
            case_dir=str(tmp_path / "a_abort"),
            root_cause_category="prompt_loop",
            error_message="loop detected",
        ),
        LiveCaseResult(
            case_id="b_fail",
            case_kind="component",
            description="fail",
            status="failed",
            case_dir=str(tmp_path / "b_fail"),
            root_cause_category="artifact_wiring_error",
            error_message="missing artifact",
        ),
        LiveCaseResult(
            case_id="c_skip",
            case_kind="component",
            description="skip",
            status="skipped_missing_env",
            case_dir=str(tmp_path / "c_skip"),
            root_cause_category="env_missing",
            error_message="missing env vars: OPENROUTER_API_KEY",
        ),
        LiveCaseResult(
            case_id="d_pass",
            case_kind="precheck",
            description="pass",
            status="passed",
            case_dir=str(tmp_path / "d_pass"),
        ),
    ]
    summary = SuiteSummary(
        suite_id="suite_demo",
        suite_name="full",
        pdf_file_path="pdf/demo.pdf",
        case_results=results,
        status_counts={
            "aborted_loop_guard": 1,
            "failed": 1,
            "skipped_missing_env": 1,
            "passed": 1,
        },
    )

    summary_path = tmp_path / "suite_summary.md"
    backlog_path = tmp_path / "repair_backlog.md"
    _write_suite_summary_markdown(summary_path, summary)
    _write_repair_backlog(backlog_path, results)

    summary_text = summary_path.read_text(encoding="utf-8")
    backlog_text = backlog_path.read_text(encoding="utf-8")

    assert "## Aborted Loop Guard" in summary_text
    assert "## Failed" in summary_text
    assert "## Skipped Missing Env" in summary_text
    assert "## Passed" in summary_text
    assert summary_text.index("a_abort") < summary_text.index("b_fail") < summary_text.index("c_skip")
    assert "# Repair Backlog" in backlog_text
    assert "a_abort" in backlog_text
    assert "b_fail" in backlog_text
    assert "publish_chain_live" in backlog_text


def test_run_worker_case_writes_case_result_with_stub_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    目的：给 worker 主入口补一条离线烟测，避免子进程执行路径出现基础回归。
    功能：验证 stub runner 也能完整写出 `case_result.json` 和 console transcript。
    实现逻辑：把 `prepare_evidence_live` 的 runner 临时替换成假实现，再直接调用 `run_worker_case()`。
    可调参数：`tmp_path` 与 `monkeypatch` 由 pytest 提供。
    默认参数及原因：复用已有 `prepare_evidence_live` case id，因为它不要求环境变量，最适合做最小 worker 烟测。
    """

    monkeypatch.setattr(live_cases.flow_common, "CACHE_ROOT", live_cases.flow_common.CACHE_ROOT)
    monkeypatch.setattr(live_cases.main_module, "CACHE_ROOT", live_cases.main_module.CACHE_ROOT)

    def _fake_runner(runtime: live_cases.LiveCaseRuntime) -> dict[str, object]:
        """
        目的：提供一个不触发真实 API 的最小 worker runner。
        功能：返回最小通过结果，供 `run_worker_case()` 完成后续落盘。
        实现逻辑：只回填期望输出、输入边界和说明，不创建真实 Flow。
        可调参数：`runtime` 为当前 case 运行上下文。
        默认参数及原因：`flow` 返回 `None`，因为这里要验证 worker 框架而不是业务阶段执行。
        """

        return {
            "flow": None,
            "expected_output_paths": [],
            "allowed_input_keys": ["pdf_file_path"],
            "observed_input_keys": ["pdf_file_path"],
            "notes": ["stub runner"],
        }

    monkeypatch.setitem(CASE_RUNNERS, "run_prepare_evidence_live", _fake_runner)

    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n")
    case_dir = tmp_path / "prepare_case"

    result = run_worker_case(
        suite_id="suite_stub",
        case_id="prepare_evidence_live",
        case_dir=case_dir,
        pdf_path=pdf_path,
    )

    case_result = json.loads((case_dir / "case_result.json").read_text(encoding="utf-8"))
    console_logs = list(case_dir.rglob("console.txt"))

    assert result.status == "passed"
    assert case_result["status"] == "passed"
    assert case_result["notes"] == ["stub runner"]
    assert console_logs
