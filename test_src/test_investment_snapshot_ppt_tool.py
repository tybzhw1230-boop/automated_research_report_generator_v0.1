from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation

from automated_research_report_generator.tools.investment_snapshot_ppt_tool import (
    FALLBACK_TEXT,
    InvestmentSnapshotPptTool,
)


def _find_real_finance_pack_sample() -> Path:
    """
    目的：在当前仓库的运行缓存里定位一个真实的 finance pack 样本。
    功能：给 PPT 工具回归测试提供真实 Markdown 输入，而不是只测手工构造的小样例。
    实现逻辑：优先从 `.cache` 中搜索 `05_finance_pack.md`，找到第一份即返回。
    可调参数：无。
    默认参数及原因：找不到样本时直接 `pytest.skip`，原因是这组测试本身就是面向真实缓存样本的回归。
    """

    sample_paths = sorted(Path(".cache").glob("**/05_finance_pack.md"))
    if not sample_paths:
        pytest.skip("No real finance pack sample was found under .cache.")
    return sample_paths[0]


def test_parse_financial_markdown_prefers_latest_three_periods_from_real_cache_sample() -> None:
    """
    目的：锁定工具会从真实 finance pack 中识别指标名称列，并只展示最近三期。
    功能：检查解析结果的行名映射、期间窗口和单位说明都有效。
    实现逻辑：读取真实缓存样本，直接调用 `_parse_financial_markdown()` 做结构断言。
    可调参数：无。
    默认参数及原因：样本来自真实 `.cache`，原因是当前迁移主要防止与正式产物格式不兼容。
    """

    sample_path = _find_real_finance_pack_sample()
    markdown = sample_path.read_text(encoding="utf-8")
    tool = InvestmentSnapshotPptTool()

    parsed = tool._parse_financial_markdown(markdown)

    assert len(parsed.display_periods) == min(3, len(parsed.periods))
    assert parsed.display_periods == parsed.periods[-len(parsed.display_periods) :]
    assert "营业收入" in parsed.row_lookup
    assert "金额" not in parsed.row_lookup
    assert parsed.row_lookup["营业收入"]
    assert parsed.unit_note


def test_parse_financial_markdown_accepts_numbered_heading_inside_fenced_block() -> None:
    """
    目的：锁定工具能兼容 agent 可能额外包裹的代码块，以及带编号的核心财务表标题。
    功能：确保 `## 1、核心财务数据总表` 这类真实变体不会再被误判成缺少标题。
    实现逻辑：构造带外围 ```markdown 围栏的最小财务样例，直接调用解析函数断言结果。
    可调参数：无。
    默认参数及原因：样例保持最小列数和最小字段集合，原因是这里只验证标题与外围格式兼容性。
    """

    markdown = """```markdown
# 测试公司 财务分析

## 1、核心财务数据总表（单位：人民币千元）

| 指标名称 | 2022A | 2023A | 2024A |
| :--- | :--- | :--- | :--- |
| 营业收入 | 100 | 130 | 169 |
| 总资产 | 300 | 320 | 350 |
| 净利润 | 10 | 12 | 15 |
| 所有者权益 | 120 | 130 | 150 |
```"""
    tool = InvestmentSnapshotPptTool()

    parsed = tool._parse_financial_markdown(markdown)

    assert parsed.display_periods == ["2022A", "2023A", "2024A"]
    assert parsed.row_lookup["营业收入"] == ["100", "130", "169"]
    assert "人民币千元" in parsed.unit_note


def test_tool_generates_single_slide_pptx_from_real_cache_sample(tmp_path: Path) -> None:
    """
    目的：验证工具能基于真实 finance pack 成功生成单页 PPTX。
    功能：检查输出文件落地、返回消息正确，且演示文稿确实只有一页。
    实现逻辑：用真实财务 Markdown 配合最小叙事输入调用 `_run()`，再用 `python-pptx` 回读结果。
    可调参数：`tmp_path`。
    默认参数及原因：叙事输入使用固定短文本，原因是这里关注 PPT 生成稳定性而非文案质量。
    """

    sample_path = _find_real_finance_pack_sample()
    markdown = sample_path.read_text(encoding="utf-8")
    output_path = tmp_path / "sample_investment_snapshot.pptx"
    tool = InvestmentSnapshotPptTool()

    result = tool._run(
        pptx_path=output_path.as_posix(),
        slide_title="投资要点速览",
        positioning_line="细分赛道的稳健经营者",
        overview_summary="公司在细分行业拥有稳定经营基础，当前任务只验证工具生成链路是否稳定。",
        overview_product_items=[
            {"name": "核心产品A", "description": "用于验证概况区结构化条目可被正确渲染。"},
            {"name": "核心产品B", "description": "用于验证单页布局在真实财务样本下可以顺利落地。"},
            {"name": "核心产品C", "description": "用于验证工具不会因为缺少额外字段而抛异常。"},
        ],
        financial_source_markdown=markdown,
        highlight_items=[
            {"title": "亮点一", "detail": "验证真实样本下的财务表解析与单页布局。"},
            {"title": "亮点二", "detail": "验证工具级输出路径、保存逻辑和回读兼容性。"},
            {"title": "亮点三", "detail": "验证缺失字段时由工具统一补位而不是直接失败。"},
        ],
        risk_items=[
            {"title": "风险提示", "detail": "本测试只验证工具兼容性，不代表任何投资判断。"},
        ],
    )

    assert output_path.exists()
    assert str(output_path) in result

    presentation = Presentation(str(output_path))
    assert len(presentation.slides) == 1


def test_missing_financial_fields_fall_back_to_que_fa_xin_xi() -> None:
    """
    目的：锁定工具在缺少核心字段时会回落到“缺乏信息”，而不是崩溃或伪造完整表。
    功能：检查只给营业收入的最小样例下，缺失指标会被明确补成占位值。
    实现逻辑：手工构造最小核心财务表，解析后直接检查 8 个固定指标中的缺失项。
    可调参数：无。
    默认参数及原因：最小样例保留三期实际列，原因是这正是当前快照页的固定展示窗口。
    """

    markdown = """
# 测试公司 财务分析

## 1. 核心财务数据总表

| 指标类别 | 指标名称 | 2023A | 2024A | 2025A | 备注 |
| --- | --- | --- | --- | --- | --- |
| 金额 | 营业收入 | 100 | 130 | 169 | reported |

## 2. 其他说明
"""
    tool = InvestmentSnapshotPptTool()
    parsed = tool._parse_financial_markdown(markdown)
    rows = {row.label: row.values for row in tool._build_snapshot_financial_rows(parsed)}

    assert rows["营业收入"] == ["100", "130", "169"]
    assert rows["营业收入增长率"] == [FALLBACK_TEXT, "30.0%", "30.0%"]
    assert rows["总资产"] == [FALLBACK_TEXT, FALLBACK_TEXT, FALLBACK_TEXT]
    assert rows["资产负债率"] == [FALLBACK_TEXT, FALLBACK_TEXT, FALLBACK_TEXT]
    assert rows["ROE"] == [FALLBACK_TEXT, FALLBACK_TEXT, FALLBACK_TEXT]


def test_missing_core_financial_heading_error_contains_heading_candidates() -> None:
    """
    目的：锁定缺少核心财务表标题时的报错会带上候选标题，方便直接定位 agent 传入了什么结构。
    功能：检查错误信息中包含识别到的章节标题，而不是只返回一条泛化失败文案。
    实现逻辑：构造一个没有“核心财务数据总表”但包含其他章节标题的样例，并断言异常消息。
    可调参数：无。
    默认参数及原因：样例只保留少量标题，原因是这里关注错误信息质量而不是完整财务解析。
    """

    markdown = """
# 测试公司 财务分析

## 1. 财务摘要

## 2. 计算补齐后的关键财务指标表

| 指标名称 | 2022A | 2023A | 2024A |
| :--- | :--- | :--- | :--- |
| 资产负债率 | 30.0% | 25.0% | 20.0% |
"""
    tool = InvestmentSnapshotPptTool()

    with pytest.raises(ValueError) as exc_info:
        tool._parse_financial_markdown(markdown)

    error_message = str(exc_info.value)
    assert "Recognized heading candidates" in error_message
    assert "财务摘要" in error_message
    assert "计算补齐后的关键财务指标表" in error_message
