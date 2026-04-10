from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import automated_research_report_generator.flow.research_flow as research_flow_module
from automated_research_report_generator.flow.document_metadata import PdfDocumentMetadataPayload
from automated_research_report_generator.flow.models import GateReviewOutput
from automated_research_report_generator.flow.registry import initialize_registry
from automated_research_report_generator.flow.research_flow import (
    RESEARCH_GATE_FORCE_PASS_EVENT,
    RESEARCH_GATE_RETRY_EVENT,
    ResearchReportFlow,
    THESIS_STAGE_COMPLETED_NO_GATE_EVENT,
    VALUATION_STAGE_COMPLETED_NO_GATE_EVENT,
)


def _build_flow(tmp_path) -> ResearchReportFlow:
    """
    目的：给 flow 测试提供一份最小可运行的 Flow 状态。
    功能：填充 run slug、输入路径、缓存路径和 registry 路径，避免测试依赖真实运行目录。
    实现逻辑：直接实例化 `ResearchReportFlow`，再把测试需要的状态字段写入临时目录路径。
    可调参数：`tmp_path` 由 pytest 提供，用于隔离测试过程中的临时路径。
    默认参数及原因：默认会初始化一份最小 registry，原因是新 flow 的 checkpoint 和 QA 都依赖真实账本。
    """

    flow = ResearchReportFlow()
    flow.state.run_slug = "test-run"
    flow.state.company_name = "Test Co"
    flow.state.industry = "Automation"
    flow.state.pdf_file_path = (tmp_path / "sample.pdf").as_posix()
    flow.state.page_index_file_path = (tmp_path / "page_index.json").as_posix()
    flow.state.document_metadata_file_path = (tmp_path / "document_metadata.md").as_posix()
    flow.state.research_scope_path = (tmp_path / "research_scope.md").as_posix()
    flow.state.question_tree_path = (tmp_path / "question_tree.md").as_posix()
    flow.state.run_cache_dir = (tmp_path / ".cache" / "test-run").as_posix()
    flow.state.run_output_dir = (tmp_path / ".cache" / "test-run").as_posix()
    flow.state.final_report_markdown_path = (tmp_path / ".cache" / "test-run" / "report.md").as_posix()
    flow.state.final_report_pdf_path = (tmp_path / ".cache" / "test-run" / "report.pdf").as_posix()
    Path(flow.state.pdf_file_path).write_text("pdf placeholder", encoding="utf-8")
    Path(flow.state.page_index_file_path).write_text("{}", encoding="utf-8")
    Path(flow.state.document_metadata_file_path).write_text("metadata", encoding="utf-8")
    Path(flow.state.research_scope_path).write_text("scope", encoding="utf-8")
    Path(flow.state.question_tree_path).write_text("questions", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)
    flow.state.evidence_registry_path = registry_path.as_posix()
    return flow


def test_prepare_evidence_generates_document_metadata_directly_in_run_indexing(tmp_path, monkeypatch):
    """
    目的：锁住 document metadata 首次落盘就写进当前 run 的 `indexing/` 目录。
    功能：验证 `prepare_evidence()` 会先拿到内存中的 metadata payload，再直接把 JSON 写入当前 run 的 `indexing/`。
    实现逻辑：替换 metadata payload 解析函数和其他预处理依赖后执行 `prepare_evidence()`，再断言 run 内生成结果和路径回写。
    可调参数：`tmp_path` 用于隔离临时路径，`monkeypatch` 用于替换真实预处理依赖。
    默认参数及原因：默认不创建公共 metadata 缓存，原因是这个修复的目标就是避免先写到 run 外路径。
    """

    flow = ResearchReportFlow()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_text("pdf placeholder", encoding="utf-8")
    flow.state.pdf_file_path = pdf_path.as_posix()

    run_root_dir = tmp_path / ".cache" / "20260409_测试公司"
    cache_dir = run_root_dir / "md"
    log_dir = run_root_dir / "logs"
    captured_manifest: dict[str, str] = {}

    def fake_resolve_pdf_document_metadata_payload(pdf_file_path: str) -> PdfDocumentMetadataPayload:
        """
        目的：替换真实 metadata 识别，避免测试触发模型调用和 run 外落盘。
        功能：返回一份尚未落盘的固定 metadata payload。
        实现逻辑：直接构造 `PdfDocumentMetadataPayload` 并返回。
        可调参数：`pdf_file_path`。
        默认参数及原因：默认忽略 PDF 内容，原因是这个测试只关心 metadata 的落盘位置。
        """

        return PdfDocumentMetadataPayload(
            pdf_file_path=pdf_file_path,
            generated_at="2026-04-09T13:11:28+08:00",
            fingerprint="fake-fingerprint",
            company_name="测试公司",
            industry="电气设备",
            source_pages=[1, 2, 3],
        )

    def fake_build_run_directories(company_name: str) -> dict[str, Path]:
        """
        目的：给 `prepare_evidence()` 提供稳定的临时 run 目录。
        功能：返回测试专用的 run 根目录、产物目录和日志目录。
        实现逻辑：先创建所需目录，再按真实接口结构返回路径映射。
        可调参数：`company_name`。
        默认参数及原因：默认 run slug 固定，原因是测试需要稳定断言最终路径。
        """

        cache_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        return {
            "run_slug": Path("20260409_测试公司"),
            "run_root_dir": run_root_dir,
            "cache_dir": cache_dir,
            "log_dir": log_dir,
        }

    def fake_ensure_pdf_page_index(pdf_file_path: str, company_name: str) -> str:
        """
        目的：替换真实页索引生成逻辑，避免测试触发大模型和并发预处理。
        功能：在 run 内 `indexing/` 写入一份最小页索引文件，并返回其路径。
        实现逻辑：直接创建固定文件名的 JSON 文件。
        可调参数：`pdf_file_path` 和 `company_name`。
        默认参数及原因：默认写空 JSON，原因是这里不关心页索引内容本身。
        """

        index_path = (run_root_dir / "indexing" / "sample_page_index.json").resolve()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("{}", encoding="utf-8")
        return index_path.as_posix()

    def fake_write_run_debug_manifest(**kwargs) -> str:
        """
        目的：拦截 manifest 落盘参数，避免测试写入项目真实缓存目录。
        功能：记录 `prepare_evidence()` 传给 manifest 的关键路径。
        实现逻辑：把传入参数更新到外层捕获字典，再返回一个临时 manifest 路径。
        可调参数：`kwargs`。
        默认参数及原因：默认只记录参数不生成真实 manifest 内容，原因是这里关注的是路径值是否正确。
        """

        for key, value in kwargs.items():
            captured_manifest[key] = value
        manifest_path = (cache_dir / "run_manifest.json").resolve()
        return manifest_path.as_posix()

    monkeypatch.setattr(
        research_flow_module,
        "resolve_pdf_document_metadata_payload",
        fake_resolve_pdf_document_metadata_payload,
    )
    monkeypatch.setattr(research_flow_module, "build_run_directories", fake_build_run_directories)
    monkeypatch.setattr(research_flow_module, "activate_run_preprocess_log", lambda run_slug: None)
    monkeypatch.setattr(research_flow_module, "ensure_pdf_page_index", fake_ensure_pdf_page_index)
    monkeypatch.setattr(research_flow_module, "set_pdf_context", lambda pdf_file_path, page_index_path: None)
    monkeypatch.setattr(
        research_flow_module,
        "initialize_registry",
        lambda company_name, industry, registry_path: None,
    )
    monkeypatch.setattr(
        research_flow_module,
        "set_evidence_registry_context",
        lambda registry_path: None,
    )
    monkeypatch.setattr(research_flow_module, "write_run_debug_manifest", fake_write_run_debug_manifest)
    monkeypatch.setattr(flow, "_write_checkpoint", lambda checkpoint_code, payload: "checkpoint.json")
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")

    flow.prepare_evidence()

    run_metadata_path = (run_root_dir / "indexing" / "sample_document_metadata.json").resolve()
    public_metadata_path = tmp_path / ".cache" / "pdf_page_indexes" / "sample_document_metadata.json"
    saved_payload = json.loads(run_metadata_path.read_text(encoding="utf-8"))

    assert run_metadata_path.exists()
    assert not public_metadata_path.exists()
    assert saved_payload["company_name"] == "测试公司"
    assert saved_payload["industry"] == "电气设备"
    assert saved_payload["source_pages"] == [1, 2, 3]
    assert flow.state.document_metadata_file_path == run_metadata_path.as_posix()
    assert captured_manifest["document_metadata_file_path"] == run_metadata_path.as_posix()


def test_run_research_stage_runs_all_subcrews_then_targeted_rerun_only_updates_affected_pack(tmp_path, monkeypatch):
    """
    目的：验证 research 阶段首轮会跑 7 个子 crew，而返工只会定向重跑受影响 pack。
    功能：检查首轮输出写入 `iter_01/`，返工只重跑 `business_pack` 并把该 pack 路径切到 `iter_02/`。
    实现逻辑：替换 `RESEARCH_SUB_CREW_SPECS` 为假 crew 列表后分别执行首轮和返工轮次，再断言输入目录与 state 路径。
    可调参数：`tmp_path` 用于隔离临时目录，`monkeypatch` 用于替换真实 crew。
    默认参数及原因：默认不生成真实 Markdown 文件，原因是本测试只关注编排与状态切换。
    """

    flow = _build_flow(tmp_path)
    captured_runs: list[dict[str, str]] = []

    class FakeSubCrew:
        """
        目的：替换真实 sub-crew，避免测试触发模型调用。
        功能：拦截 `kickoff()` 输入，供测试断言。
        实现逻辑：提供与真实 crew 兼容的 `crew().kickoff()` 接口，并把输入写入外层列表。
        可调参数：无。
        默认参数及原因：默认只记录输入不生成产物，原因是本测试只验证 flow 调度。
        """

        output_log_file_path = None

        def crew(self):
            class FakeRunner:
                def kickoff(self, inputs):
                    captured_runs.append(inputs.copy())

            return FakeRunner()

    fake_specs = [
        {
            "pack_name": "history_background_pack",
            "legacy_pack_name": "history_governance_pack",
            "crew_name": "history_background_crew",
            "crew_cls": FakeSubCrew,
            "output_file_name": "01_history_background_pack.md",
            "state_attr": "history_background_pack_path",
            "legacy_state_attr": "history_governance_pack_path",
            "title": "历史与背景分析包",
            "checkpoint_code": "cp02a_history_background_pack",
        },
        {
            "pack_name": "industry_pack",
            "legacy_pack_name": "",
            "crew_name": "industry_crew",
            "crew_cls": FakeSubCrew,
            "output_file_name": "02_industry_pack.md",
            "state_attr": "industry_pack_path",
            "legacy_state_attr": "",
            "title": "行业分析包",
            "checkpoint_code": "cp02b_industry_pack",
        },
        {
            "pack_name": "business_pack",
            "legacy_pack_name": "",
            "crew_name": "business_crew",
            "crew_cls": FakeSubCrew,
            "output_file_name": "03_business_pack.md",
            "state_attr": "business_pack_path",
            "legacy_state_attr": "",
            "title": "业务分析包",
            "checkpoint_code": "cp02c_business_pack",
        },
    ]

    monkeypatch.setattr(research_flow_module, "RESEARCH_SUB_CREW_SPECS", fake_specs)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(flow, "_configure_crew_log", lambda crew_instance, log_path: crew_instance)
    monkeypatch.setattr(flow, "_write_checkpoint", lambda checkpoint_code, payload: "checkpoint.json")
    monkeypatch.setattr(flow, "_register_pack_output", lambda path, pack_name, title: None)
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")

    flow._run_research_stage("initial")
    flow.state.research_loop_count = 1
    flow._run_research_stage("qa_revision", targeted_packs=["business_pack"], qa_feedback="补客户结构缺口")

    iter_01_dir = (Path(flow.state.run_cache_dir) / "research" / "iter_01").resolve().as_posix()
    iter_02_dir = (Path(flow.state.run_cache_dir) / "research" / "iter_02").resolve().as_posix()

    assert [run["pack_name"] for run in captured_runs[:3]] == [
        "history_background_pack",
        "industry_pack",
        "business_pack",
    ]
    assert captured_runs[0]["pack_output_path"] == f"{iter_01_dir}/01_history_background_pack.md"
    assert captured_runs[2]["pack_output_path"] == f"{iter_01_dir}/03_business_pack.md"
    assert captured_runs[3]["pack_name"] == "business_pack"
    assert captured_runs[3]["pack_output_path"] == f"{iter_02_dir}/03_business_pack.md"
    assert captured_runs[3]["qa_feedback"] == "补客户结构缺口"
    assert flow.state.history_background_pack_path == f"{iter_01_dir}/01_history_background_pack.md"
    assert flow.state.history_governance_pack_path == f"{iter_01_dir}/01_history_background_pack.md"
    assert flow.state.industry_pack_path == f"{iter_01_dir}/02_industry_pack.md"
    assert flow.state.business_pack_path == f"{iter_02_dir}/03_business_pack.md"


def test_review_research_gate_uses_affected_packs_and_stores_feedback(tmp_path, monkeypatch):
    """
    目的：验证 research gate 会根据 `affected_packs` 定向回写 registry，并缓存可直接给下轮 crew 的反馈文本。
    功能：检查 judgment entry 查询只查询受影响 pack，`apply_gate_review()` 只回写一次，且反馈文本被存入 state。
    实现逻辑：替换 `_run_qa_stage()`、`entry_ids_for_packs()` 和 `apply_gate_review()`，再调用 `review_research_gate()` 断言参数。
    可调参数：`tmp_path` 用于构造最小 Flow 状态，`monkeypatch` 用于替换依赖。
    默认参数及原因：默认不执行真实 QA，原因是这个测试只关心 gate 编排和回写边界。
    """

    flow = _build_flow(tmp_path)
    flow.state.history_background_pack_path = "history.md"
    flow.state.industry_pack_path = "industry.md"
    flow.state.business_pack_path = "business.md"
    flow.state.peer_info_pack_path = "peer_info.md"
    flow.state.finance_pack_path = "finance.md"
    flow.state.operating_metrics_pack_path = "metrics.md"
    flow.state.risk_pack_path = "risk.md"

    captured_pack_names: list[tuple[str, ...]] = []
    applied_stage_names: list[str] = []
    coverage = GateReviewOutput(
        status="revise",
        summary="业务包仍缺客户集中度证据。",
        key_gaps=["客户集中度证据不足"],
        priority_actions=["补充前五大客户收入占比与来源页码"],
        affected_packs=["business_pack"],
    )
    deferred_consistency = GateReviewOutput(status="pass", summary="research consistency deferred")

    monkeypatch.setattr(flow, "_compose_stage_bundle", lambda paths: "|".join(paths))
    monkeypatch.setattr(flow, "_run_qa_stage", lambda **kwargs: (coverage, deferred_consistency))
    monkeypatch.setattr(
        research_flow_module,
        "entry_ids_for_packs",
        lambda registry_path, pack_names, entry_types=None: captured_pack_names.append(tuple(pack_names)) or ["judgment_business"],
    )
    monkeypatch.setattr(
        research_flow_module,
        "apply_gate_review",
        lambda registry_path, *, stage_name, entry_ids, review: applied_stage_names.append(stage_name),
    )
    monkeypatch.setattr(flow, "_write_checkpoint", lambda checkpoint_code, payload: "checkpoint.json")

    result = flow.review_research_gate()

    assert result == {"coverage_status": "revise", "consistency_status": "pass"}
    assert captured_pack_names == [("business_pack",)]
    assert applied_stage_names == ["research_qa"]
    assert "业务包仍缺客户集中度证据" in flow.state.last_research_qa_feedback
    assert "business_pack" in flow.state.last_research_qa_feedback


def test_run_qa_stage_passes_registry_markdown_and_diff(tmp_path, monkeypatch):
    """
    目的：验证 `_run_qa_stage()` 会把 registry Markdown 视图和 diff 摘要传给 QA。
    功能：检查传入 QA crew 的输入包含 `registry_markdown_text`、`registry_diff_text` 和 `registry_full_text`。
    实现逻辑：构造最小 registry 与基础文件，替换 `QACrew` 为假对象后直接调用 `_run_qa_stage()` 断言实际输入。
    可调参数：`tmp_path` 用于隔离临时文件，`monkeypatch` 用于替换 QA crew。
    默认参数及原因：默认只模拟 coverage 任务输出，原因是 research 阶段当前只关心外部 QA gate。
    """

    flow = _build_flow(tmp_path)
    flow.state.investment_thesis_path = (tmp_path / "thesis.md").as_posix()
    flow.state.diligence_questions_path = (tmp_path / "diligence.md").as_posix()
    Path(flow.state.investment_thesis_path).write_text("thesis body", encoding="utf-8")
    Path(flow.state.diligence_questions_path).write_text("diligence body", encoding="utf-8")

    captured: dict[str, object] = {}

    class FakeQACrew:
        def __init__(self):
            self.output_log_file_path = None
            self.run_consistency_review = True

        def crew(self):
            current_crew = self

            class FakeRunner:
                def kickoff(self, inputs):
                    captured["inputs"] = inputs
                    captured["run_consistency_review"] = current_crew.run_consistency_review
                    return SimpleNamespace(
                        tasks_output=[
                            SimpleNamespace(
                                json_dict={
                                    "status": "pass",
                                    "summary": "coverage ok",
                                    "key_gaps": [],
                                    "priority_actions": [],
                                    "affected_packs": [],
                                },
                                pydantic=None,
                            )
                        ]
                    )

            return FakeRunner()

    monkeypatch.setattr(research_flow_module, "QACrew", FakeQACrew)
    monkeypatch.setattr(flow, "_current_registry_diff_summary", lambda stage_name: "diff summary")

    coverage, consistency = flow._run_qa_stage(
        stage_name="research",
        stage_focus="研究重点",
        stage_bundle="research-bundle",
        run_consistency_review=False,
    )

    assert coverage.status == "pass"
    assert consistency.status == "pass"
    assert captured["run_consistency_review"] is False
    assert "# 证据注册表：" in captured["inputs"]["registry_markdown_text"]
    assert captured["inputs"]["registry_diff_text"] == "diff summary"
    assert '"entries"' in captured["inputs"]["registry_full_text"]


def test_run_valuation_stage_uses_peer_info_and_operating_metrics_inputs(tmp_path, monkeypatch):
    """
    目的：验证 valuation 阶段会接收新的 `peer_info_pack_text` 和 `operating_metrics_pack_text` 输入。
    功能：检查 `_run_valuation_stage()` 传给 valuation crew 的输入不再依赖 business pack。
    实现逻辑：构造最小上游 pack，替换 valuation crew 为假对象后直接调用 `_run_valuation_stage()` 断言输入。
    可调参数：`tmp_path` 用于隔离临时文件，`monkeypatch` 用于替换 valuation crew。
    默认参数及原因：默认只校验关键输入，原因是这个测试关注的是新设计的接线变化。
    """

    flow = _build_flow(tmp_path)
    flow.state.peer_info_pack_path = (tmp_path / "peer_info.md").as_posix()
    flow.state.finance_pack_path = (tmp_path / "finance.md").as_posix()
    flow.state.operating_metrics_pack_path = (tmp_path / "metrics.md").as_posix()
    flow.state.risk_pack_path = (tmp_path / "risk.md").as_posix()
    for path, text in [
        (flow.state.peer_info_pack_path, "peer info body"),
        (flow.state.finance_pack_path, "finance body"),
        (flow.state.operating_metrics_pack_path, "metrics body"),
        (flow.state.risk_pack_path, "risk body"),
    ]:
        Path(path).write_text(text, encoding="utf-8")

    captured_inputs: dict[str, str] = {}

    class FakeValuationCrew:
        output_log_file_path = None

        def crew(self):
            class FakeRunner:
                def kickoff(self, inputs):
                    captured_inputs.update(inputs)

            return FakeRunner()

    monkeypatch.setattr(research_flow_module, "ValuationCrew", FakeValuationCrew)
    monkeypatch.setattr(flow, "_prepare_tool_context", lambda: None)
    monkeypatch.setattr(flow, "_configure_crew_log", lambda crew_instance, log_path: crew_instance)
    monkeypatch.setattr(flow, "_register_pack_output", lambda path, pack_name, title: None)
    monkeypatch.setattr(flow, "_write_checkpoint", lambda checkpoint_code, payload: "checkpoint.json")
    monkeypatch.setattr(flow, "_log_flow", lambda message: "flow.log")

    flow._run_valuation_stage("initial")

    assert captured_inputs["peer_info_pack_text"] == "peer info body"
    assert captured_inputs["finance_pack_text"] == "finance body"
    assert captured_inputs["operating_metrics_pack_text"] == "metrics body"
    assert captured_inputs["risk_pack_text"] == "risk body"


def test_run_valuation_crew_returns_no_gate_event(tmp_path, monkeypatch):
    """
    目的：验证新链路下估值完成后会直接进入 thesis，不再走外部 valuation gate。
    功能：检查 `run_valuation_crew()` 返回新的无 gate 事件标签。
    实现逻辑：替换 `_run_valuation_stage()` 后直接调用 `run_valuation_crew()` 断言返回值。
    可调参数：`tmp_path` 用于构造最小 Flow 状态，`monkeypatch` 用于替换阶段执行函数。
    默认参数及原因：默认不跑真实 crew，原因是这里只验证 flow 路由标签。
    """

    flow = _build_flow(tmp_path)
    monkeypatch.setattr(flow, "_run_valuation_stage", lambda loop_reason: "valuation.md")

    assert flow.run_valuation_crew() == VALUATION_STAGE_COMPLETED_NO_GATE_EVENT


def test_run_investment_thesis_crew_routes_with_no_gate_event(tmp_path, monkeypatch):
    """
    目的：验证 thesis 阶段会把无 gate 事件作为路由标签发给 writeup。
    功能：同时检查 `run_investment_thesis_crew()` 的装饰器类型和返回事件标签。
    实现逻辑：替换 `_run_thesis_stage()` 后直接调用该方法，并断言其已注册为 router。
    可调参数：`tmp_path` 用于构造最小 Flow 状态，`monkeypatch` 用于替换阶段执行函数。
    默认参数及原因：默认不跑真实 crew，原因是这里只验证 Flow 事件分发边界。
    """

    flow = _build_flow(tmp_path)
    monkeypatch.setattr(flow, "_run_thesis_stage", lambda: "thesis.md")

    assert getattr(ResearchReportFlow.run_investment_thesis_crew, "__is_router__", False) is True
    assert flow.run_investment_thesis_crew() == THESIS_STAGE_COMPLETED_NO_GATE_EVENT
