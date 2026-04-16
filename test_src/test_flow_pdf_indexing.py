from __future__ import annotations

import json
import time
from pathlib import Path
from typing import get_args

import pytest
import automated_research_report_generator.tools.pdf_page_tools as pdf_page_tools
from pydantic import ValidationError
import automated_research_report_generator.tools.document_metadata_tools as document_metadata_tools_module
from automated_research_report_generator.flow import pdf_indexing
from automated_research_report_generator.flow import document_metadata as document_metadata_module
from automated_research_report_generator.flow.common import (
    ALLOW_TEST_FIXTURE_ENV,
    TEST_FIXTURE_ROOT,
    ensure_runtime_artifact_path_allowed,
)
from automated_research_report_generator.flow.research_flow import ResearchReportFlow
from automated_research_report_generator.tools.pdf_page_tools import (
    PAGE_INDEX_ALLOWED_TOPICS,
    PdfPageIndexEntry,
    ReadPdfPageIndexInput,
    ReadPdfPageIndexTool,
)


def test_summarize_pages_in_parallel_keeps_page_order(monkeypatch) -> None:
    """
    设计目的：锁住并发索引最关键的顺序约束。
    模块功能：验证页面并发执行后，返回结果仍然按原始页码排序。
    实现逻辑：用假的单页摘要函数制造乱序完成，再检查最终列表顺序。
    可调参数：通过 `monkeypatch` 替换 `summarize_page_topic`。
    默认参数及原因：测试固定 3 页，原因是这个规模已经足够覆盖乱序回收场景。
    """

    pages = ["第一页", "第二页", "第三页"]

    def fake_summarize_page_topic(
        page_number: int,
        page_text: str,
        company_name: str = "",
        total_pages: int = 0,
    ) -> PdfPageIndexEntry:
        """
        设计目的：给并发调度测试提供稳定的假执行器。
        模块功能：按不同页码制造不同等待时间，并返回固定结构的页索引结果。
        实现逻辑：页码越小延迟越长，故意制造“先提交的不一定先完成”的情况。
        可调参数：页码、页面文本、公司名和总页数。
        默认参数及原因：这里只用页码控制延迟，原因是测试目标只关心并发回收顺序。
        """

        time.sleep((4 - page_number) * 0.02)
        return PdfPageIndexEntry(
            page_number=page_number,
            topic=f"主题{page_number}",
            matched_topics=["业务"],
        )

    monkeypatch.setattr(pdf_indexing, "summarize_page_topic", fake_summarize_page_topic)

    entries = pdf_indexing.summarize_pages_in_parallel(
        pages=pages,
        company_name="测试公司",
        max_concurrency=3,
    )

    assert [entry.page_number for entry in entries] == [1, 2, 3]
    assert [entry.topic for entry in entries] == ["主题1", "主题2", "主题3"]
    assert [entry.matched_topics for entry in entries] == [["业务"], ["业务"], ["业务"]]


def test_page_index_runtime_config_from_env(monkeypatch) -> None:
    """
    设计目的：锁住并发数和重试次数的运行时配置入口。
    模块功能：验证环境变量能够覆盖默认并发数和重试次数。
    实现逻辑：先写入环境变量，再读取模块配置函数结果并做断言。
    可调参数：通过 `monkeypatch` 写入环境变量。
    默认参数及原因：测试只覆盖整数环境变量，原因是这两个入口都以整数配置为核心。
    """

    monkeypatch.setenv("PDF_INDEX_MAX_CONCURRENCY", "6")
    monkeypatch.setenv("PDF_INDEX_RETRY_LIMIT", "3")

    assert pdf_indexing.get_page_index_max_concurrency() == 6
    assert pdf_indexing.get_page_index_retry_limit() == 3


def test_extract_page_summary_from_raw_parses_json_code_block() -> None:
    """
    设计目的：锁住页面索引对原始 JSON 文本的本地解析能力。
    模块功能：验证模型把结果包在 Markdown 代码块里时，仍能正确还原主题和主题分类。
    实现逻辑：直接调用原始文本解析函数，覆盖 JSON 代码块这一条关键回归路径。
    可调参数：无。
    默认参数及原因：固定使用一段最小 JSON 示例，原因是这里关注解析链路而不是模型行为。
    """

    topic, matched_topics = pdf_indexing._extract_page_summary_from_raw(
        raw='```json\n{"topic":"主营业务概览","matched_topics":["业务","产品"]}\n```',
        fallback_topic="第1页内容",
        fallback_topics=["其他"],
    )

    assert topic == "主营业务概览"
    assert matched_topics == ["业务", "产品"]


def test_default_page_index_path_follows_active_run_indexing_directory(tmp_path) -> None:
    """
    设计目的：锁住页索引文件要跟随当前 run 的 `indexing/` 目录，而不是落到项目级公共缓存目录。
    模块功能：激活 run 级 indexing 目录后，验证默认页索引路径会写到该目录下。
    实现逻辑：先重置工具层运行时状态，再激活一个临时 indexing 目录并检查默认路径。
    可调参数：`tmp_path` 由 pytest 提供。
    默认参数及原因：默认使用临时目录，原因是测试不应污染项目真实缓存。
    """

    pdf_page_tools.reset_pdf_page_tool_runtime_state()
    indexing_dir = tmp_path / ".cache" / "20260409_test-company" / "indexing"
    pdf_page_tools.activate_page_index_directory(indexing_dir)

    index_path = pdf_page_tools.default_page_index_path(tmp_path / "sample.pdf")

    assert index_path == Path(indexing_dir / "sample_page_index.json").resolve()


def test_default_page_index_path_requires_active_run_indexing_directory(tmp_path, monkeypatch) -> None:
    """
    设计目的：锁住页索引默认路径不再允许回退到项目级 `pdf_page_indexes` 目录。
    模块功能：验证未激活 run 级 indexing 目录时，`default_page_index_path()` 会直接报错。
    实现逻辑：先把 `PROJECT_ROOT` 指向临时目录，再在未激活上下文下调用默认路径函数并断言抛出异常。
    可调参数：`tmp_path` 与 `monkeypatch` 由 pytest 提供。
    默认参数及原因：默认使用临时项目根目录，原因是测试不应污染真实工作区缓存。
    """

    pdf_page_tools.reset_pdf_page_tool_runtime_state()
    monkeypatch.setattr(pdf_page_tools, "PROJECT_ROOT", tmp_path)

    fallback_dir = tmp_path / ".cache" / "pdf_page_indexes"
    with pytest.raises(RuntimeError, match="activate_page_index_directory"):
        pdf_page_tools.default_page_index_path(tmp_path / "sample.pdf")
    assert not fallback_dir.exists()


def test_resolve_pdf_document_metadata_payload_uses_in_memory_identification_path(tmp_path, monkeypatch) -> None:
    """
    设计目的：锁住 metadata 预读逻辑会直接走当前内存识别链路。
    模块功能：验证 `resolve_pdf_document_metadata_payload()` 在替换抽样和摘要逻辑后，仍能成功返回 payload。
    实现逻辑：只替换抽样、agent 创建和摘要逻辑，不再依赖任何遗留默认路径占位符。
    可调参数：`tmp_path` 与 `monkeypatch` 由 pytest 提供。
    默认参数及原因：默认用最小假 payload，原因是这里只验证当前内存识别链路不需要遗留兼容层。
    """

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_text("stub pdf", encoding="utf-8")

    monkeypatch.setattr(document_metadata_module, "sample_document_metadata_pages", lambda *args, **kwargs: [(1, "封面")])
    monkeypatch.setattr(document_metadata_module, "create_document_metadata_agent", lambda: object())
    monkeypatch.setattr(
        document_metadata_module,
        "summarize_document_metadata",
        lambda agent, pdf_file_path, sampled_pages: document_metadata_module.PdfDocumentMetadataPayload(
            pdf_file_path=str(Path(pdf_file_path).resolve()),
            generated_at="2026-04-09T22:30:00+08:00",
            fingerprint="fake-fingerprint",
            company_name="测试公司",
            industry="测试行业",
            source_pages=[1],
        ),
    )

    payload = document_metadata_module.resolve_pdf_document_metadata_payload(pdf_path.as_posix())

    assert payload.company_name == "测试公司"
    assert payload.industry == "测试行业"
    assert payload.source_pages == [1]


def test_document_metadata_payload_uses_fy_fq_placeholder_defaults() -> None:
    """
    设计目的：锁住 metadata payload 的默认期间占位符集合。
    模块功能：验证默认 `periods` 只包含新的 FY/FQ 语义占位符，不再保留旧期间编号。
    实现逻辑：直接构造最小 payload，并检查默认字典键集合。
    可调参数：无。
    默认参数及原因：使用最小必填字段，原因是这里只验证默认结构而非识别流程。
    """

    payload = document_metadata_module.PdfDocumentMetadataPayload(
        pdf_file_path="sample.pdf",
        generated_at="2026-04-15T00:00:00+08:00",
        fingerprint="fake-fingerprint",
        company_name="测试公司",
        industry="测试行业",
        source_pages=[1],
    )

    assert payload.periods == {
        "{FQ0/FY0}": "",
        "{FQ-1}": "",
        "{FY-1}": "",
        "{FY-2}": "",
        "{FY-3}": "",
        "{FY1}": "",
        "{FY2}": "",
        "{FY3}": "",
        "{FY4}": "",
        "{FY5}": "",
    }


def test_extract_metadata_from_raw_parses_new_fy_fq_keys() -> None:
    """
    设计目的：锁住 raw JSON 回退解析对新 FY/FQ 字段的支持。
    模块功能：验证 `_extract_metadata_from_raw()` 会把新语义字段映射成统一占位符字典。
    实现逻辑：传入最小 JSON 字符串，直接断言返回值。
    可调参数：无。
    默认参数及原因：只覆盖关键字段组合，原因是目标是锁住新字段命名而不是穷举所有分支。
    """

    company_name, industry, periods = document_metadata_module._extract_metadata_from_raw(
        json.dumps(
            {
                "company_name": "测试公司",
                "industry": "测试行业",
                "fq0_or_fy0": "2025Q1A",
                "fq_minus_1": "2024Q1A",
                "fy_minus_1": "2024A",
                "fy_minus_2": "2023A",
                "fy_minus_3": "2022A",
                "fy_1": "2026E",
                "fy_2": "2027E",
            },
            ensure_ascii=False,
        )
    )

    assert company_name == "测试公司"
    assert industry == "测试行业"
    assert periods == {
        "{FQ0/FY0}": "2025Q1A",
        "{FQ-1}": "2024Q1A",
        "{FY-1}": "2024A",
        "{FY-2}": "2023A",
        "{FY-3}": "2022A",
        "{FY1}": "2026E",
        "{FY2}": "2027E",
        "{FY3}": "",
        "{FY4}": "",
        "{FY5}": "",
    }


def test_summarize_document_metadata_maps_new_fy_fq_fields(tmp_path, monkeypatch) -> None:
    """
    设计目的：锁住 metadata 结构化识别结果到占位符字典的映射规则。
    模块功能：验证 `summarize_document_metadata()` 会把新字段写入统一的 `periods`。
    实现逻辑：构造假的 agent 返回新 `PdfDocumentMetadata`，再检查输出 payload。
    可调参数：`tmp_path` 与 `monkeypatch` 由 pytest 提供。
    默认参数及原因：默认使用最小假 PDF 和固定指纹，原因是这里只验证字段映射而不是文件处理。
    """

    class FakeResult:
        """
        设计目的：给 metadata 汇总测试提供最小返回对象。
        模块功能：模拟 `agent.kickoff()` 的返回结构。
        实现逻辑：只暴露 `pydantic` 与 `raw` 两个当前用到的属性。
        可调参数：无。
        默认参数及原因：固定为测试需要的最小字段，原因是便于聚焦映射逻辑。
        """

        def __init__(self) -> None:
            """
            设计目的：初始化固定的结构化返回内容。
            模块功能：把测试所需的 metadata 字段挂到实例上。
            实现逻辑：直接写死 `pydantic` 和 `raw`，避免引入额外依赖。
            可调参数：无。
            默认参数及原因：固定写死，原因是这里只验证映射行为。
            """

            self.pydantic = document_metadata_module.PdfDocumentMetadata(
                company_name="测试公司",
                industry="测试行业",
                fq0_or_fy0="2025Q1A",
                fq_minus_1="2024Q1A",
                fy_minus_1="2024A",
                fy_minus_2="2023A",
                fy_minus_3="2022A",
                fy_1="2026E",
                fy_2="2027E",
                fy_3="2028E",
            )
            self.raw = ""

    class FakeAgent:
        """
        设计目的：给 metadata 汇总测试提供最小 agent 替身。
        模块功能：返回固定的结构化结果。
        实现逻辑：实现一个与真实 agent 兼容的 `kickoff()` 方法。
        可调参数：无。
        默认参数及原因：固定返回 `FakeResult`，原因是这里只验证汇总后的映射结果。
        """

        def kickoff(self, prompt, response_format=None):
            """
            设计目的：模拟真实 agent 的最小调用接口。
            模块功能：返回固定的结构化结果对象。
            实现逻辑：忽略输入参数，直接返回 `FakeResult`。
            可调参数：prompt 与 response_format。
            默认参数及原因：参数保留但不使用，原因是这里只关心返回值结构。
            """

            return FakeResult()

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_text("stub pdf", encoding="utf-8")
    monkeypatch.setattr(document_metadata_module, "compute_pdf_fingerprint", lambda _path: "fake-fingerprint")

    payload = document_metadata_module.summarize_document_metadata(
        agent=FakeAgent(),
        pdf_file_path=pdf_path,
        sampled_pages=[(1, "封面"), (2, "财务摘要")],
    )

    assert payload.company_name == "测试公司"
    assert payload.industry == "测试行业"
    assert payload.source_pages == [1, 2]
    assert payload.periods == {
        "{FQ0/FY0}": "2025Q1A",
        "{FQ-1}": "2024Q1A",
        "{FY-1}": "2024A",
        "{FY-2}": "2023A",
        "{FY-3}": "2022A",
        "{FY1}": "2026E",
        "{FY2}": "2027E",
        "{FY3}": "2028E",
        "{FY4}": "",
        "{FY5}": "",
    }


def test_base_inputs_include_period_placeholder_values_from_document_metadata(tmp_path) -> None:
    """
    目的：锁住 Flow 给各个 crew 注入的公共输入里包含期间占位符的实际值。
    功能：验证 `_base_inputs()` 会把 metadata 的 `periods` 映射成 CrewAI 可插值的 `FY-3`、`FQ0/FY0` 以及别名 `FQ0_OR_FY0` 等键。
    实现逻辑：先写入一个最小 metadata 文件，再构造 Flow 状态并直接读取 `_base_inputs()`。
    可调参数：`tmp_path` 由 pytest 提供，用于隔离临时 metadata 文件。
    默认参数及原因：只校验几个关键期间键，原因是这里关注的是占位符注入链路而不是 metadata 全字段序列化。
    """

    metadata_path = tmp_path / "sample_document_metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "company_name": "测试公司",
                "industry": "测试行业",
                "periods": {
                    "{FQ0/FY0}": "2025Q1A",
                    "{FQ-1}": "",
                    "{FY-1}": "2024A",
                    "{FY-2}": "2023A",
                    "{FY-3}": "2022A",
                    "{FY1}": "",
                    "{FY2}": "",
                    "{FY3}": "",
                    "{FY4}": "",
                    "{FY5}": "",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    flow = ResearchReportFlow()
    flow.state.company_name = "测试公司"
    flow.state.industry = "测试行业"
    flow.state.pdf_file_path = "sample.pdf"
    flow.state.page_index_file_path = "sample_page_index.json"
    flow.state.document_metadata_file_path = metadata_path.as_posix()
    flow.state.analysis_source_dir = "analysis_dir"

    base_inputs = flow._base_inputs()

    assert base_inputs["company_name"] == "测试公司"
    assert base_inputs["FY-3"] == "2022A"
    assert base_inputs["FY-2"] == "2023A"
    assert base_inputs["FY-1"] == "2024A"
    assert "FQ-1" in base_inputs
    assert base_inputs["FQ-1"] == ""
    assert base_inputs["FQ0/FY0"] == "2025Q1A"
    assert base_inputs["FQ0_OR_FY0"] == "2025Q1A"
    assert base_inputs["FY1"] == ""
    assert base_inputs["FY5"] == ""


def test_sparse_metadata_periods_are_marked_stale_and_rebuilt(tmp_path, monkeypatch) -> None:
    """
    目的：锁住 Flow 在 metadata 缺失时仍会提供完整期间占位键集。
    功能：验证 `_period_placeholder_inputs()` 至少返回当前仓库所有 `tasks.yaml` 用到的 10 个稳定键位。
    实现逻辑：直接构造一个未设置 metadata 路径的 Flow，再读取占位输入字典并与默认键集比对。
    可调参数：当前无显式参数，直接使用 Flow 默认状态。
    默认参数及原因：默认值统一为空字符串，原因是未识别到期间时也必须满足 CrewAI 的模板插值要求。
    """

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_text("stub pdf", encoding="utf-8")
    metadata_path = pdf_path.with_name(f"{pdf_path.stem}_document_metadata.json")
    metadata_path.write_text(
        json.dumps(
            {
                "pdf_file_path": pdf_path.as_posix(),
                "generated_at": "2026-04-16T00:00:00+08:00",
                "fingerprint": "fake-fingerprint",
                "company_name": "测试公司",
                "industry": "测试行业",
                "source_pages": [1],
                "periods": {
                    "{FY-1}": "2024A",
                    "{FY-2}": "2023A",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(document_metadata_module, "compute_pdf_fingerprint", lambda _path: "fake-fingerprint")
    monkeypatch.setattr(document_metadata_tools_module, "compute_pdf_fingerprint", lambda _path: "fake-fingerprint")
    monkeypatch.setattr(document_metadata_module, "sample_document_metadata_pages", lambda *args, **kwargs: [(1, "封面")])
    monkeypatch.setattr(document_metadata_module, "create_document_metadata_agent", lambda: object())
    monkeypatch.setattr(
        document_metadata_module,
        "summarize_document_metadata",
        lambda agent, pdf_file_path, sampled_pages: document_metadata_module.PdfDocumentMetadataPayload(
            pdf_file_path=str(Path(pdf_file_path).resolve()),
            generated_at="2026-04-16T10:00:00+08:00",
            fingerprint="fake-fingerprint",
            company_name="测试公司",
            industry="测试行业",
            source_pages=[1],
        ),
    )

    assert not document_metadata_tools_module.document_metadata_is_current(pdf_path, metadata_path)

    result = document_metadata_module.ensure_pdf_document_metadata(pdf_path.as_posix())
    rebuilt_metadata = document_metadata_tools_module.load_document_metadata(metadata_path)

    assert result["document_metadata_file_path"] == str(metadata_path.resolve())
    assert rebuilt_metadata["company_name"] == "测试公司"
    assert rebuilt_metadata["industry"] == "测试行业"
    assert rebuilt_metadata["periods"] == {
        "{FQ0/FY0}": "",
        "{FQ-1}": "",
        "{FY-1}": "",
        "{FY-2}": "",
        "{FY-3}": "",
        "{FY1}": "",
        "{FY2}": "",
        "{FY3}": "",
        "{FY4}": "",
        "{FY5}": "",
    }


def test_runtime_artifact_path_guard_blocks_fixture_path_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    目的：锁住正式运行态对 `test_src/live_fixtures` 的硬隔离边界。
    功能：验证未显式开启测试态时，fixture 路径会被立即判定为非法来源。
    实现逻辑：清掉统一环境变量后，直接调用路径守卫并断言抛出异常。
    可调参数：`monkeypatch` 用于隔离环境变量。
    默认参数及原因：默认使用仓库真实 fixture 根目录，原因是这正是当前高风险污染源。
    """

    monkeypatch.delenv(ALLOW_TEST_FIXTURE_ENV, raising=False)

    with pytest.raises(RuntimeError, match="Illegal finance artifact path|Illegal artifact path"):
        ensure_runtime_artifact_path_allowed(
            TEST_FIXTURE_ROOT / "analysis_sources" / "05_finance_file_source.md",
            label="finance artifact path",
        )


def test_runtime_artifact_path_guard_allows_fixture_path_in_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    目的：锁住测试态仍可显式使用 live fixture，不影响现有 harness。
    功能：验证统一环境变量打开后，fixture 路径会被正常放行。
    实现逻辑：设置测试态环境变量后，直接检查返回的标准化路径。
    可调参数：`monkeypatch` 用于隔离环境变量。
    默认参数及原因：默认只检查单一路径，原因是守卫逻辑本身与具体文件内容无关。
    """

    fixture_path = TEST_FIXTURE_ROOT / "analysis_sources" / "05_finance_file_source.md"
    monkeypatch.setenv(ALLOW_TEST_FIXTURE_ENV, "1")

    assert ensure_runtime_artifact_path_allowed(
        fixture_path,
        label="finance artifact path",
    ) == fixture_path.resolve().as_posix()


def test_flow_read_blocks_fixture_path_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    目的：锁住 Flow 回读中间产物时不会悄悄吃到 fixture 文件。
    功能：验证 `_read()` 遇到 fixture 路径会直接失败，而不是读出内容继续运行。
    实现逻辑：构造最小 Flow 后直接调用 `_read()` 并断言抛出异常。
    可调参数：`monkeypatch` 用于清理测试态环境变量。
    默认参数及原因：默认读取 finance fixture，原因是本轮发现的问题就集中在该专题。
    """

    monkeypatch.delenv(ALLOW_TEST_FIXTURE_ENV, raising=False)
    flow = ResearchReportFlow()

    with pytest.raises(RuntimeError, match="Illegal analysis artifact path"):
        flow._read((TEST_FIXTURE_ROOT / "analysis_sources" / "05_finance_file_source.md").as_posix())


def test_prepare_evidence_blocks_fixture_pdf_path_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    目的：锁住正式 Flow 在预处理前就拦截非法 fixture PDF 路径。
    功能：验证 `prepare_evidence()` 不会先去读非法路径，再在后续阶段才报错。
    实现逻辑：给 Flow 注入一个位于 fixture 根目录下的假 PDF 路径，断言立即抛出路径越界异常。
    可调参数：`monkeypatch` 用于清理测试态环境变量。
    默认参数及原因：默认不要求文件真实存在，原因是路径来源本身就应先被拦截。
    """

    monkeypatch.delenv(ALLOW_TEST_FIXTURE_ENV, raising=False)
    flow = ResearchReportFlow()
    flow.state.pdf_file_path = (TEST_FIXTURE_ROOT / "fake_fixture_document.pdf").as_posix()

    with pytest.raises(RuntimeError, match="Illegal pdf file path"):
        flow.prepare_evidence()


def test_read_pdf_page_index_tool_uses_explicit_pdf_path(tmp_path) -> None:
    """
    设计目的：锁住 PDF 页索引工具必须显式携带 `pdf_path` 才能工作。
    模块功能：验证工具调用会基于显式传入的 PDF 路径绑定上下文，而不是偷偷依赖上一轮残留状态。
    实现逻辑：先准备临时 PDF 文件和对应页索引，再直接调用工具并检查返回的 `pdf_file_path`。
    可调参数：`tmp_path` 由 `pytest` 提供。
    默认参数及原因：使用最小化的单页索引，原因是这里只关心显式传参和上下文绑定，不关心索引生成质量。
    """

    pdf_page_tools.reset_pdf_page_tool_runtime_state()
    indexing_dir = tmp_path / ".cache" / "20260409_test-company" / "indexing"
    pdf_page_tools.activate_page_index_directory(indexing_dir)
    pdf_path = (tmp_path / "sample.pdf").resolve()
    pdf_path.write_text("stub pdf", encoding="utf-8")

    payload = pdf_page_tools.build_page_index_payload(
        pdf_path,
        [PdfPageIndexEntry(page_number=1, topic="公司概览", matched_topics=["业务"])],
    )
    pdf_page_tools.save_page_index(payload)

    result = ReadPdfPageIndexTool()._run(pdf_path=pdf_path.as_posix())

    assert '"pdf_file_path": "' + pdf_path.as_posix() + '"' in result


def test_read_pdf_page_index_tool_filters_by_matched_topic_and_max_results(tmp_path) -> None:
    """
    设计目的：锁住页索引工具已经从模糊关键词过滤收紧为固定主题精确过滤。
    模块功能：验证传入 `matched_topic` 后，只返回 `matched_topics` 包含该主题的页面，并在过滤后再执行结果截断。
    实现逻辑：准备一份带 3 页索引的临时数据，分别覆盖“仅按主题过滤”和“主题过滤后再截断”两条路径。
    可调参数：`tmp_path` 由 `pytest` 提供。
    默认参数及原因：使用最小三页样本，原因是已足够覆盖命中、多命中和截断行为。
    """

    pdf_page_tools.reset_pdf_page_tool_runtime_state()
    indexing_dir = tmp_path / ".cache" / "20260409_test-company" / "indexing"
    pdf_page_tools.activate_page_index_directory(indexing_dir)
    pdf_path = (tmp_path / "sample.pdf").resolve()
    pdf_path.write_text("stub pdf", encoding="utf-8")

    payload = pdf_page_tools.build_page_index_payload(
        pdf_path,
        [
            PdfPageIndexEntry(page_number=1, topic="公司业务概览", matched_topics=["业务"]),
            PdfPageIndexEntry(page_number=2, topic="核心产品与方案", matched_topics=["产品", "业务"]),
            PdfPageIndexEntry(page_number=3, topic="风险提示", matched_topics=["风险"]),
        ],
    )
    pdf_page_tools.save_page_index(payload)

    filtered_result = json.loads(
        ReadPdfPageIndexTool()._run(
            pdf_path=pdf_path.as_posix(),
            matched_topic="业务",
        )
    )
    truncated_result = json.loads(
        ReadPdfPageIndexTool()._run(
            pdf_path=pdf_path.as_posix(),
            matched_topic="业务",
            max_results=1,
        )
    )
    full_result = json.loads(ReadPdfPageIndexTool()._run(pdf_path=pdf_path.as_posix()))

    assert [page["page_number"] for page in filtered_result["pages"]] == [1, 2]
    assert all("业务" in page["matched_topics"] for page in filtered_result["pages"])
    assert [page["page_number"] for page in truncated_result["pages"]] == [1]
    assert [page["page_number"] for page in full_result["pages"]] == [1, 2, 3]


def test_read_pdf_page_index_input_requires_valid_matched_topic_enum() -> None:
    """
    设计目的：锁住页索引工具输入契约已经改成固定主题枚举。
    模块功能：验证非法 `matched_topic` 无法通过 schema 校验，合法枚举值集合与共享主题常量完全一致。
    实现逻辑：先断言共享常量和 schema 注解枚举一致，再覆盖一条非法输入校验路径。
    可调参数：无。
    默认参数及原因：固定用一个非法主题字符串，原因是这里只关心枚举边界而不是具体业务语义。
    """

    matched_topic_annotation = ReadPdfPageIndexInput.model_fields["matched_topic"].annotation
    assert set(get_args(get_args(matched_topic_annotation)[0])) == set(PAGE_INDEX_ALLOWED_TOPICS)

    with pytest.raises(ValidationError, match="Input should be"):
        ReadPdfPageIndexInput(pdf_path="/tmp/sample.pdf", matched_topic="随便筛")
