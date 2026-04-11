from __future__ import annotations

import json
import threading
from pathlib import Path

from automated_research_report_generator.flow.registry import (
    entry_ids_for_packs,
    initialize_registry,
    load_registry,
    record_registry_review,
    register_evidence,
    update_entry_fields,
    update_entry_status,
)
from automated_research_report_generator.tools import (
    ReadRegistryTool,
    UpdateEntryTool,
    set_evidence_registry_context,
)


def test_registry_template_initialization_and_updates(tmp_path):
    """
    目的：验证确定性 registry 模板从初始化到补证、改状态、补正文的最小闭环。
    功能：检查模板条目是否落盘、支持证据是否只追加不自动通过、以及状态与正文更新是否生效。
    实现逻辑：先初始化账本，再写 context/support 证据，随后更新状态和正文，最后回读断言。
    可调参数：`tmp_path`。
    默认参数及原因：默认使用 pytest 临时目录，原因是测试不应污染真实运行缓存。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)

    raw_registry = registry_path.read_text(encoding="utf-8")
    assert '"entries"' in raw_registry
    assert '"entry_id": "J_IND_001"' in raw_registry
    assert '"target_pack"' not in raw_registry
    assert '"supporting_evidence_ids"' not in raw_registry

    snapshot = load_registry(registry_path)
    assert snapshot.company_name == "Test Co"
    assert any(entry.entry_id == "J_IND_001" for entry in snapshot.entries)
    assert entry_ids_for_packs(
        registry_path,
        ["industry_pack"],
        entry_types=["judgment"],
    ) == ["J_IND_001", "J_IND_002"]

    context_evidence_id = register_evidence(
        registry_path,
        title="Industry pack",
        summary="Industry pack summary",
        source_type="crew_output",
        source_ref="industry_pack.md",
        pack_name="industry_pack",
        entry_ids=["J_IND_001"],
        stance="context",
    )
    assert context_evidence_id

    snapshot = load_registry(registry_path)
    question = next(entry for entry in snapshot.entries if entry.entry_id == "J_IND_001")
    assert any(
        evidence.evidence_id == context_evidence_id and evidence.entry_ids == ["J_IND_001"] and evidence.stance == "context"
        for evidence in snapshot.evidence
    )
    assert question.status == "unchecked"

    evidence_id = register_evidence(
        registry_path,
        title="Industry growth evidence",
        summary="2023-2025 revenue grew 50%.",
        source_type="agent_output",
        source_ref="招股书-P12",
        pack_name="industry_pack",
        entry_ids=["J_IND_001"],
    )
    assert evidence_id

    snapshot = load_registry(registry_path)
    question = next(entry for entry in snapshot.entries if entry.entry_id == "J_IND_001")
    assert any(
        evidence.evidence_id == evidence_id and evidence.entry_ids == ["J_IND_001"] and evidence.stance == "support"
        for evidence in snapshot.evidence
    )
    assert question.status == "unchecked"

    update_entry_status(
        registry_path,
        ["J_IND_001"],
        status="need_revision",
        revision_detail="缺口：Need better market-size data.；动作：Re-run the industry crew with tighter source selection.",
    )
    snapshot = load_registry(registry_path)
    question = next(entry for entry in snapshot.entries if entry.entry_id == "J_IND_001")
    assert question.status == "need_revision"
    assert "缺口：Need better market-size data." in question.revision_detail

    update_entry_fields(
        registry_path,
        "J_IND_001",
        content="行业需求主要由电动化和自动化升级共同驱动。",
        source="招股书-P12",
        confidence="high",
        status="checked",
    )
    snapshot = load_registry(registry_path)
    question = next(entry for entry in snapshot.entries if entry.entry_id == "J_IND_001")
    assert question.status == "checked"
    assert question.content == "行业需求主要由电动化和自动化升级共同驱动。"
    assert question.source == "招股书-P12"
    assert question.confidence == "high"

    record_registry_review(
        registry_path,
        reviewer="industry_analyst",
        pack_name="industry_pack",
        summary="confirmed current template entry set",
        has_changes=True,
        touched_entry_ids=["J_IND_001"],
    )
    snapshot = load_registry(registry_path)
    assert any("registry_review:" in note for note in snapshot.notes)


def test_read_registry_tool_markdown_full_and_entry_list_views(tmp_path):
    """
    目的：验证 registry 只读工具在新账本模型下的主要读取视图仍然可用。
    功能：检查 Markdown、完整快照和轻量 entry_list 三种视图都能返回稳定结构。
    实现逻辑：先初始化并补一条财务表格，再设置上下文，最后分别调用三个视图断言。
    可调参数：`tmp_path`。
    默认参数及原因：默认只覆盖 financial owner_crew，原因是它同时包含表格数据和 judgment。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)
    update_entry_fields(
        registry_path,
        "D_FIN_001",
        content=[
            {
                "指标": "收入",
                "2022": "10",
                "2023": "12",
                "2024": "15",
                "最新期间": "2025Q1",
                "单位": "亿元",
                "来源": "年报P88",
            }
        ],
        source="年报P88",
        confidence="medium",
        status="checked",
    )
    set_evidence_registry_context(registry_path.as_posix())

    markdown_payload = ReadRegistryTool()._run(owner_crew="financial_crew")
    full_payload = json.loads(ReadRegistryTool()._run(view="full"))
    entry_list_payload = json.loads(
        ReadRegistryTool()._run(
            view="entry_list",
            owner_crew="financial_crew",
        )
    )

    assert "# 证据注册表：" in markdown_payload
    assert "## financial_crew" in markdown_payload
    assert "D_FIN_001" in markdown_payload
    assert full_payload["company_name"] == "Test Co"
    assert full_payload["entries"]
    assert "notes" in full_payload
    assert entry_list_payload["entry_count"] == 3
    assert any(entry["entry_id"] == "D_FIN_001" for entry in entry_list_payload["entries"])


def test_update_entry_tool_backfills_seeded_table_entry(tmp_path):
    """
    目的：验证 `update_entry` 工具能直接回填模板化的 table entry。
    功能：检查工具调用后，条目的表格正文、来源和状态会一起写回 registry。
    实现逻辑：先初始化账本，再设置上下文调用工具，最后读取 entry 详情断言。
    可调参数：`tmp_path`。
    默认参数及原因：默认覆盖 `D_BUS_001`，原因是它是 research 模板里最典型的 table entry。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)
    set_evidence_registry_context(registry_path.as_posix())
    rows = [
        {
            "业务/产品线": "核心业务",
            "2022": "5",
            "2023": "7",
            "2024": "9",
            "最新期间": "2025Q1",
            "单位": "亿元",
            "来源": "招股书P45",
        }
    ]

    payload = json.loads(
        UpdateEntryTool()._run(
            entry_id="D_BUS_001",
            content=rows,
            source="招股书P45",
            confidence="high",
            status="checked",
        )
    )
    markdown_payload = ReadRegistryTool()._run(
        owner_crew="business_crew",
        filter_entry_type="data",
    )
    detail_payload = json.loads(
        ReadRegistryTool()._run(
            view="entry_detail",
            entry_ids=["D_BUS_001"],
        )
    )

    assert payload == {"status": "ok", "entry_id": "D_BUS_001"}
    assert "D_BUS_001" in markdown_payload
    assert "J_BUS_001" not in markdown_payload
    assert detail_payload["entries"][0]["content"] == rows
    assert detail_payload["entries"][0]["source"] == "招股书P45"
    assert detail_payload["entries"][0]["status"] == "checked"


def test_read_registry_tool_keeps_instance_registry_path_across_threads(tmp_path):
    """
    目的：验证 registry tool 在跨线程执行时不会丢失路径上下文。
    功能：先在主线程设置 registry context，再在新线程里复用同一个 tool 实例读取完整快照。
    实现逻辑：初始化临时 registry、创建 tool 实例、在线程中调用 `_run()`，最后断言结果。
    可调参数：`tmp_path`。
    默认参数及原因：默认使用 `view="full"`，原因是这样能覆盖路径解析到快照读取的完整链路。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)
    set_evidence_registry_context(registry_path.as_posix())
    tool = ReadRegistryTool()
    captured: dict[str, object] = {}

    def run_tool_in_thread() -> None:
        """
        目的：在线程环境里执行 registry tool，模拟 CrewAI 的工具执行方式。
        功能：记录线程执行得到的 payload，或在失败时记录异常对象。
        实现逻辑：调用同一个 `ReadRegistryTool` 实例的 `_run()`，并把结果写回外层字典。
        可调参数：无。
        默认参数及原因：默认不吞掉异常文本，原因是测试失败时需要直接暴露真实错误。
        """

        try:
            captured["payload"] = json.loads(tool._run(view="full"))
        except Exception as exc:  # pragma: no cover
            captured["error"] = exc

    worker = threading.Thread(target=run_tool_in_thread)
    worker.start()
    worker.join()

    assert "error" not in captured
    assert captured["payload"]["company_name"] == "Test Co"


def test_load_registry_waits_for_inflight_write_and_avoids_partial_json(tmp_path, monkeypatch):
    """
    目的：验证 registry 在写入过程中不会被其他线程读到半截 JSON。
    功能：人为把一次写入拆成前后两段，再并发触发读取，检查读取会等待写入完成。
    实现逻辑：先初始化 registry，再用 monkeypatch 把目标文件写入改成“写半截后暂停”，随后并发执行写线程和读线程。
    可调参数：`tmp_path` 和 `monkeypatch`。
    默认参数及原因：默认只拦截目标 registry 文件，原因是测试要最小化对其他路径行为的干扰。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)

    first_chunk_written = threading.Event()
    reader_started = threading.Event()
    reader_done = threading.Event()
    allow_finish = threading.Event()
    captured: dict[str, object] = {}
    original_write_text = Path.write_text

    def slow_write_text(self, data, encoding=None, errors=None, newline=None):
        """
        目的：稳定制造“文件只写了一半”的并发窗口。
        功能：对目标 registry 文件先写前半段并暂停，等外部放行后再补完后半段。
        实现逻辑：非目标路径走原始 `Path.write_text()`；目标路径则手动打开文件分两段写入。
        可调参数：沿用 `Path.write_text()` 的 `encoding`、`errors` 和 `newline`。
        默认参数及原因：默认暂停点放在字符串中间，原因是这样最容易制造非法 JSON。
        """

        current_path = Path(self).expanduser().resolve()
        if current_path != registry_path.resolve():
            return original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

        midpoint = max(1, len(data) // 2)
        with current_path.open("w", encoding=encoding or "utf-8", errors=errors, newline=newline) as handle:
            handle.write(data[:midpoint])
            handle.flush()
            first_chunk_written.set()
            if not allow_finish.wait(timeout=5):
                raise TimeoutError("Timed out while waiting to finish slow registry write.")
            handle.write(data[midpoint:])
            return len(data)

    monkeypatch.setattr(Path, "write_text", slow_write_text)

    def writer() -> None:
        """
        目的：在独立线程里执行一次真实的 registry 写入。
        功能：调用 `register_evidence()` 触发完整的读改写链路。
        实现逻辑：把异常写回外层字典，供测试最后统一断言。
        可调参数：无。
        默认参数及原因：默认新增一条支持性证据，原因是这是最常见的运行时写入路径。
        """

        try:
            captured["evidence_id"] = register_evidence(
                registry_path,
                title="Delayed evidence",
                summary="Evidence written through delayed file save.",
                source_type="agent_output",
                source_ref="unit-test",
                pack_name="industry_pack",
                entry_ids=["J_IND_001"],
            )
        except Exception as exc:  # pragma: no cover
            captured["writer_error"] = exc

    def reader() -> None:
        """
        目的：在写入尚未完成时并发读取 registry。
        功能：记录读取结果或异常，并标记线程结束时点。
        实现逻辑：先发出启动信号，再调用 `load_registry()`，最后无论成功失败都写入完成信号。
        可调参数：无。
        默认参数及原因：默认直接读取完整 snapshot，原因是这条路径最接近运行时真实行为。
        """

        reader_started.set()
        try:
            captured["snapshot"] = load_registry(registry_path)
        except Exception as exc:  # pragma: no cover
            captured["reader_error"] = exc
        finally:
            reader_done.set()

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    assert first_chunk_written.wait(timeout=5)

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    assert reader_started.wait(timeout=5)
    assert reader_done.wait(timeout=0.1) is False

    allow_finish.set()
    writer_thread.join()
    reader_thread.join()

    assert "writer_error" not in captured
    assert "reader_error" not in captured
    assert captured["snapshot"].company_name == "Test Co"
    assert captured["evidence_id"]
    final_snapshot = load_registry(registry_path)
    assert len(final_snapshot.evidence) == 1
