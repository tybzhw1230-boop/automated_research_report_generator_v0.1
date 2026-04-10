import json
import threading
from pathlib import Path

from automated_research_report_generator.flow.models import RegistrySeedEntryPlan, RegistrySeedPlan
from automated_research_report_generator.flow.registry import (
    entry_ids_for_packs,
    initialize_registry,
    load_registry,
    record_registry_review,
    register_evidence,
    replace_registry_entries,
    update_entry_status,
)
from automated_research_report_generator.tools import ReadRegistryTool, set_evidence_registry_context


def test_registry_seeding_and_evidence_registration(tmp_path):
    """
    目的：验证统一 entry registry 从初始化到写入证据、再到状态更新的基本闭环。
    功能：检查 seed entries 是否落盘、证据是否挂到 judgment 上，以及状态更新是否生效。
    实现逻辑：先初始化账本，再读取断言 seed entry，接着写证据、更新状态，最后再次读取校验结果。
    可调参数：`tmp_path`。
    默认参数及原因：默认使用 pytest 提供的临时目录，原因是测试不应污染项目里的真实缓存。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)

    raw_registry = registry_path.read_text(encoding="utf-8")
    assert '"entries"' in raw_registry
    assert '"entry_id": "judgment_industry"' in raw_registry

    snapshot = load_registry(registry_path)
    assert snapshot.company_name == "Test Co"
    assert any(entry.entry_id == "judgment_industry" for entry in snapshot.entries)
    assert entry_ids_for_packs(registry_path, ["industry_pack"], entry_types=["judgment"]) == ["judgment_industry"]

    context_evidence_id = register_evidence(
        registry_path,
        title="Industry pack",
        summary="Industry pack summary",
        source_type="crew_output",
        source_ref="industry_pack.md",
        pack_name="industry_pack",
        entry_ids=["judgment_industry"],
        stance="context",
    )
    assert context_evidence_id

    snapshot = load_registry(registry_path)
    question = next(entry for entry in snapshot.entries if entry.entry_id == "judgment_industry")
    assert question.context_evidence_ids == [context_evidence_id]
    assert question.status == "open"

    evidence_id = register_evidence(
        registry_path,
        title="Industry growth evidence",
        summary="2023-2025 revenue grew 50%.",
        source_type="agent_output",
        source_ref="招股书-P12",
        pack_name="industry_pack",
        entry_ids=["judgment_industry"],
    )
    assert evidence_id

    snapshot = load_registry(registry_path)
    question = next(entry for entry in snapshot.entries if entry.entry_id == "judgment_industry")
    assert question.supporting_evidence_ids
    assert question.status == "supported"

    update_entry_status(
        registry_path,
        ["judgment_industry"],
        status="gap",
        gap_note="Need better market-size data.",
        next_action="Re-run the industry crew with tighter source selection.",
    )
    snapshot = load_registry(registry_path)
    question = next(entry for entry in snapshot.entries if entry.entry_id == "judgment_industry")
    assert question.status == "gap"
    assert question.gap_note == "Need better market-size data."

    replace_registry_entries(
        registry_path,
        RegistrySeedPlan(
            summary="Replace with concrete entries",
            entries=[
                RegistrySeedEntryPlan(
                    entry_id="charging_volume_growth",
                    entry_type="judgment",
                    title="充电量持续增长",
                    content="2023-2025 年充电量持续增长。",
                    target_pack="industry_pack",
                    evidence_needed="需要 2023-2025 年充电量同比数据。",
                    owner_crew="industry_crew",
                    entry_level="L2",
                    priority="high",
                    next_action="补 2023-2025 年充电量页码证据。",
                ),
                RegistrySeedEntryPlan(
                    entry_id="industry_growth_rate",
                    entry_type="data",
                    title="行业增速",
                    content="行业 2024-2026 年 CAGR。",
                    target_pack="industry_pack",
                    owner_crew="industry_crew",
                    priority="medium",
                    value="10",
                    unit="%",
                    period="2024-2026",
                    calibration_note="CAGR 口径",
                ),
            ],
        ),
    )
    snapshot = load_registry(registry_path)
    assert [entry.entry_id for entry in snapshot.entries] == ["charging_volume_growth", "industry_growth_rate"]

    record_registry_review(
        registry_path,
        reviewer="industry_analyst",
        pack_name="industry_pack",
        summary="confirmed current entry set",
        has_changes=False,
        touched_entry_ids=["charging_volume_growth"],
    )
    snapshot = load_registry(registry_path)
    assert any("registry_review:" in note for note in snapshot.notes)


def test_read_registry_tool_markdown_and_full_views(tmp_path):
    """
    目的：验证 registry 只读工具既支持默认 Markdown 视图，也支持完整 JSON 快照。
    功能：检查默认视图会返回分组 Markdown，而 `view="full"` 会返回完整 JSON。
    实现逻辑：先初始化 registry 并写入一条证据，再设置上下文后分别调用两个视图断言结果。
    可调参数：`tmp_path` 由 pytest 提供，用于隔离临时 registry 文件。
    默认参数及原因：默认只校验最关键的标题和字段，原因是本测试关注的是读取粒度。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)
    register_evidence(
        registry_path,
        title="Industry pack",
        summary="Industry pack summary",
        source_type="crew_output",
        source_ref="industry_pack.md",
        pack_name="industry_pack",
        entry_ids=["judgment_industry"],
    )
    set_evidence_registry_context(registry_path.as_posix())

    markdown_payload = ReadRegistryTool()._run()
    full_payload = json.loads(ReadRegistryTool()._run(view="full"))

    assert "# 证据注册表：" in markdown_payload
    assert "## 判断" in markdown_payload
    assert full_payload["company_name"] == "Test Co"
    assert full_payload["entries"]
    assert full_payload["evidence"]
    assert "notes" in full_payload


def test_read_registry_tool_supports_filter_entry_type(tmp_path):
    """
    目的：验证 registry 只读工具可以按 entry_type 过滤 Markdown 视图。
    功能：检查传入 `filter_entry_type="data"` 时，只保留数据条目而不混入事实条目。
    实现逻辑：先写入 fact 和 data 两类 seed，再调用 `ReadRegistryTool()._run()` 断言输出内容。
    可调参数：`tmp_path` 由 pytest 提供，用于隔离临时 registry 文件。
    默认参数及原因：默认验证 Markdown 视图，原因是 sub-crew 和 QA 最常使用的就是这个入口。
    """

    registry_path = tmp_path / "registry.json"
    initialize_registry("Test Co", "Automation", registry_path)
    replace_registry_entries(
        registry_path,
        RegistrySeedPlan(
            summary="typed seed",
            entries=[
                RegistrySeedEntryPlan(
                    entry_id="F_HIS_001",
                    entry_type="fact",
                    title="公司成立时间",
                    content="公司成立于 2001 年。",
                    target_pack="history_background_pack",
                    owner_crew="planning_crew",
                ),
                RegistrySeedEntryPlan(
                    entry_id="D_FIN_001",
                    entry_type="data",
                    title="营业收入",
                    content="2024 年营业收入待补。",
                    target_pack="finance_pack",
                    owner_crew="planning_crew",
                    value="待补",
                    unit="亿元",
                    period="2024",
                    calibration_note="合并口径",
                ),
            ],
        ),
    )
    set_evidence_registry_context(registry_path.as_posix())

    markdown_payload = ReadRegistryTool()._run(filter_entry_type="data")

    assert "## 数据 (共 1 条)" in markdown_payload
    assert "D_FIN_001" in markdown_payload
    assert "F_HIS_001" not in markdown_payload


def test_read_registry_tool_keeps_instance_registry_path_across_threads(tmp_path):
    """
    目的：验证 registry tool 在跨线程执行时不会再丢失路径上下文。
    功能：先在主线程设置 registry context，再在新线程里执行同一个 tool 实例并读取完整快照。
    实现逻辑：初始化临时 registry、创建 tool 实例、在线程中调用 `_run()`，最后断言结果和异常状态。
    可调参数：`tmp_path` 由 pytest 提供，用于隔离临时 registry 文件。
    默认参数及原因：默认使用 `view="full"`，原因是这样能直接覆盖 `_require_registry_path()` 到快照读取的完整链路。
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
    可调参数：`tmp_path` 用于隔离临时文件，`monkeypatch` 用于替换目标文件的写入行为。
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
                entry_ids=["judgment_industry"],
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
