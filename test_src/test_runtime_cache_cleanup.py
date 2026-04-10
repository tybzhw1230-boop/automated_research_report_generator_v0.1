import json
import re
from datetime import datetime, timedelta

from automated_research_report_generator import main as main_module
from automated_research_report_generator.flow import common as flow_common


def test_build_run_slug_preserves_non_ascii_company_name():
    """
    设计目的：锁住 run 目录命名不会把中文公司名错误清洗成 unknown-company。
    模块功能：验证 build_run_slug() 会保留合法的中文公司名，只替换真正不适合目录名的字符。
    实现逻辑：直接传入中文公司名，断言返回值保留时间戳前缀，并且 slug 后缀仍然是原公司名。
    可调参数：当前无，直接使用固定中文公司名样例。
    默认参数及原因：默认样例使用纯中文公司名，原因是这正是当前线上暴露问题的最小复现场景。
    """

    run_slug = flow_common.build_run_slug("中微半导体设备（上海）股份有限公司")

    assert re.match(r"^\d{8}_\d{6}_", run_slug)
    assert run_slug.endswith("_中微半导体设备（上海）股份有限公司")


def test_kickoff_preserves_crewai_memory_directory(tmp_path, monkeypatch):
    """
    设计目的：验证 run 前清理机制不会删除历史 run 目录，只清 CrewAI 运行时 memory。
    模块功能：构造假的历史 run 缓存与 CrewAI memory 目录，执行清理后检查 run 缓存仍保留。
    实现逻辑：用 monkeypatch 替换模块级根目录常量，写入测试文件与子目录，再调用清理函数断言结果。
    可调参数：`tmp_path` 与 `monkeypatch` 由 pytest 提供。
    默认参数及原因：默认使用临时目录，原因是测试不应污染项目真实缓存。
    """

    memory_root = tmp_path / "memory_root"
    memory_root.mkdir()
    (memory_root / "agents").mkdir()
    (memory_root / "agents" / "memory.json").write_text("memory", encoding="utf-8")
    monkeypatch.setattr(flow_common, "CREWAI_MEMORY_DIR", memory_root)
    monkeypatch.setattr(main_module, "reset_runtime_logging_state", lambda: None)

    class FakeFlow:
        """
        设计目的：给入口测试提供最小可运行的假 Flow。
        模块功能：接收 kickoff 输入并返回固定结果。
        实现逻辑：只保留 `kickoff()`，避免测试真正进入 CrewAI 运行链。
        可调参数：无。
        默认参数及原因：固定返回 `"ok"`，原因是这里不关心真实业务结果。
        """

        def kickoff(self, inputs):
            """
            设计目的：模拟真实 Flow 的 `kickoff()` 接口。
            模块功能：接收输入并返回固定结果。
            实现逻辑：不做任何副作用，只作为入口测试替身。
            可调参数：`inputs`。
            默认参数及原因：直接原样接收输入，原因是测试要覆盖主入口的参数透传。
            """

            return "ok"

    monkeypatch.setattr(main_module, "ResearchReportFlow", FakeFlow)

    result = main_module.kickoff({"pdf_file_path": (tmp_path / "sample.pdf").as_posix()})

    assert result == "ok"
    assert (memory_root / "agents" / "memory.json").read_text(encoding="utf-8") == "memory"


def test_write_run_debug_manifest_writes_inside_run_md_directory(tmp_path, monkeypatch):
    """
    设计目的：锁住单次 run 的 manifest 必须跟中间产物一起写入 `md/` 目录。
    模块功能：验证 manifest 路径、日志路径和目录层级都落在 `.cache/<run_slug>/` 下的 `md/` 与 `logs/`。
    实现逻辑：用 monkeypatch 替换缓存根目录后调用 manifest 写入函数，再读取 JSON 断言关键路径。
    可调参数：`tmp_path` 与 `monkeypatch` 由 pytest 提供。
    默认参数及原因：默认使用临时目录，原因是测试不应污染项目真实缓存。
    """

    cache_root = tmp_path / ".cache"
    monkeypatch.setattr(flow_common, "CACHE_ROOT", cache_root)

    manifest_path = flow_common.write_run_debug_manifest(
        run_slug="test-run",
        status="prepared",
        pdf_file_path=(tmp_path / "sample.pdf").as_posix(),
        run_cache_dir=(cache_root / "test-run" / "md").as_posix(),
    )

    expected_manifest = (cache_root / "test-run" / "md" / "run_manifest.json").resolve().as_posix()
    assert manifest_path == expected_manifest

    payload = json.loads((cache_root / "test-run" / "md" / "run_manifest.json").read_text(encoding="utf-8"))
    assert payload["run_artifact_dir"] == (cache_root / "test-run" / "md").resolve().as_posix()
    assert payload["run_log_dir"] == (cache_root / "test-run" / "logs").resolve().as_posix()
    assert payload["preprocess_log_file_path"] == (
        cache_root / "test-run" / "logs" / "preprocess.txt"
    ).resolve().as_posix()
    assert "human_review_stage" not in payload
    assert "latest_run.json" not in manifest_path


def test_activate_run_preprocess_log_writes_to_run_logs_directory(tmp_path, monkeypatch):
    """
    设计目的：验证预处理日志会绑定到当前 run 的 `logs/` 目录，而不是项目级 latest 文件。
    模块功能：激活 run 级预处理日志后写入一条日志，并检查文件实际落盘位置。
    实现逻辑：替换缓存根目录，重置日志上下文，再执行激活和写入动作。
    可调参数：`tmp_path` 与 `monkeypatch` 由 pytest 提供。
    默认参数及原因：默认使用临时目录，原因是测试不应污染项目真实缓存。
    """

    cache_root = tmp_path / ".cache"
    monkeypatch.setattr(flow_common, "CACHE_ROOT", cache_root)
    flow_common.reset_runtime_logging_state()

    preprocess_log_path = flow_common.activate_run_preprocess_log("test-run")
    flow_common.append_preprocess_log_line("hello preprocess")

    assert preprocess_log_path == (cache_root / "test-run" / "logs" / "preprocess.txt").resolve().as_posix()
    assert "hello preprocess" in (cache_root / "test-run" / "logs" / "preprocess.txt").read_text(
        encoding="utf-8"
    )


def test_utc_timestamp_uses_beijing_timezone():
    """
    设计目的：锁住项目统一时间戳入口的时区语义。
    模块功能：验证 `utc_timestamp()` 当前返回的是带 `+08:00` 偏移的北京时间字符串。
    实现逻辑：把返回值解析成 `datetime`，再检查 UTC 偏移是否等于 8 小时。
    可调参数：无。
    默认参数及原因：固定校验东八区偏移，原因是当前项目要求统一按北京时间记录时间。
    """

    parsed = datetime.fromisoformat(flow_common.utc_timestamp())

    assert parsed.utcoffset() == timedelta(hours=8)
