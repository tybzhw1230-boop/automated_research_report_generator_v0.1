from __future__ import annotations

from bs4 import BeautifulSoup

from automated_research_report_generator.tools.markdown_to_pdf_tool import MarkdownToPdfTool


def test_decorate_tables_removes_empty_columns_and_repeated_headers() -> None:
    """
    目的：锁住 PDF 渲染前的表格结构清洗行为。
    功能：验证空白列会被移除，混入表体的重复表头会被删除，并补出 `colgroup`。
    实现逻辑：构造一个含空列和重复表头的最小 Markdown 表，走一遍内部 HTML 修表流程后断言结构。
    可调参数：无。
    默认参数及原因：使用最小表格样本，原因是这里关注的是结构规范化而不是样式细节。
    """

    tool = MarkdownToPdfTool()
    markdown_text = """
| 指标 |  | 2024A | 备注 |
| --- | --- | --- | --- |
| 收入 |  | 100 | reported |
| 指标 |  | 2024A | 备注 |
""".strip()

    html = tool._markdown_to_html(tool._normalize_markdown_tables(markdown_text))
    soup = BeautifulSoup(html, "html.parser")

    tool._decorate_tables(soup, allow_local_landscape=False)

    table = soup.find("table")
    assert table is not None
    assert tool._extract_header_texts(table) == ["指标", "2024A", "备注"]

    tbody = table.find("tbody")
    assert tbody is not None
    body_rows = tbody.find_all("tr", recursive=False)
    assert len(body_rows) == 1
    assert tool._extract_row_texts(body_rows[0]) == ["收入", "100", "reported"]
    assert table.find("colgroup") is not None


def test_decorate_tables_marks_wide_tables_for_local_landscape() -> None:
    """
    目的：锁住超宽表只在局部切到横向页，而不是把整份报告拖成横向。
    功能：验证列数较多的表会被包裹到 `table-block--landscape` 容器里。
    实现逻辑：构造一个九列表格，执行表格布局规划后检查外层 wrapper class。
    可调参数：无。
    默认参数及原因：默认九列样本，原因是这已经能稳定触发当前超宽表判定。
    """

    tool = MarkdownToPdfTool()
    markdown_text = """
| 指标口径 | 公司名称 | 2023A | 2024A | 2025A | 2026E | 2027E | 备注 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P/E | 测试公司 | 20.1 | 18.2 | 16.3 | 无信息 | 无信息 | reported | 估值倍数 |
""".strip()

    html = tool._markdown_to_html(tool._normalize_markdown_tables(markdown_text))
    soup = BeautifulSoup(html, "html.parser")

    tool._decorate_tables(soup, allow_local_landscape=True)

    wrapper = soup.find("div", class_="table-block--landscape")
    assert wrapper is not None
    table = wrapper.find("table")
    assert table is not None
    assert "wide-table" in table.get("class", [])


def test_build_full_html_uses_a4_font_scale_and_one_point_five_line_height() -> None:
    """
    目的：锁住 PDF 全局版式已经切到 A4、较大字号和统一 1.5 倍行距。
    功能：验证输出 HTML 内嵌 CSS 包含页面尺寸、正文字号、表格字号和行距关键配置。
    实现逻辑：直接调用完整 HTML 包装函数并检查关键样式片段。
    可调参数：无。
    默认参数及原因：默认检查纵向页面，原因是本轮目标是 A4 纵向为默认版式。
    """

    tool = MarkdownToPdfTool()
    html = tool._build_full_html("<p>正文</p>", "测试标题", landscape=False)

    assert "size: A4;" in html
    assert "@page wide-table" in html
    assert "font-size: 12pt;" in html
    assert "font-size: 10.5pt;" in html
    assert "line-height: 1.5;" in html
