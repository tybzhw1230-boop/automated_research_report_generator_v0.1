from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Type

from bs4 import BeautifulSoup, Tag
from markdown import Markdown
from pydantic import BaseModel, Field
from weasyprint import HTML

from crewai.tools import BaseTool


class MarkdownToPdfInput(BaseModel):
    """
    目的：定义 Markdown 转 PDF 工具的稳定输入结构。
    功能：约束输入文件、输出文件、标题和页面方向开关。
    实现逻辑：通过 Pydantic 固定字段，减少 writeup 阶段的自由入参波动。
    可调参数：`markdown_path`、`pdf_path`、`title`、`auto_landscape` 和 `force_landscape`。
    默认参数及原因：`auto_landscape` 默认开启，原因是局部超宽表仍需要自动切到横向页。
    """

    markdown_path: str = Field(..., description="Path to the source markdown file")
    pdf_path: str = Field(..., description="Path to the output PDF file")
    title: str = Field(default="Research Report", description="Document title shown in the PDF metadata")
    auto_landscape: bool = Field(
        default=True,
        description="Automatically switch very wide tables to local landscape pages",
    )
    force_landscape: bool = Field(
        default=False,
        description="Force all pages to use landscape layout",
    )


@dataclass(slots=True)
class TableLayoutPlan:
    """
    目的：承载单张表在 HTML 阶段算出的列宽和分页计划。
    功能：保存列宽、列类型、表格样式类和是否需要局部横向页。
    实现逻辑：由表格内容分析阶段一次性生成，渲染阶段直接消费。
    可调参数：字段值由布局计算函数写入，不对外暴露额外参数。
    默认参数及原因：本类只保存结果，不提供业务默认值，原因是每张表的内容差异都很大。
    """

    column_widths: list[float]
    column_classes: list[str]
    table_classes: list[str]
    use_landscape: bool


class MarkdownToPdfTool(BaseTool):
    """
    目的：把最终 Markdown 稳定转换成适合研报阅读的 PDF。
    功能：整理表格结构、分配列宽、控制局部横向页，并调用 WeasyPrint 输出 PDF。
    实现逻辑：先把 Markdown 规范化成 HTML，再在 HTML 层修正表格结构和样式，最后统一渲染。
    可调参数：输入输出路径、标题和页面方向开关。
    默认参数及原因：默认 A4 纵向，仅对超宽表局部横向，原因是正文可读性优先于全局横向铺满。
    """

    name: str = "markdown_to_pdf"
    description: str = (
        "Convert a markdown file into a polished PDF with smarter table layout, "
        "repeated-header cleanup, and local wide-table landscape pages."
    )
    args_schema: Type[BaseModel] = MarkdownToPdfInput

    _PERIOD_HEADER_PATTERN = re.compile(
        r"^(ttm|fy[- ]?\d+|fq[- ]?\d+|fq0/fy0|20\d{2}(a|e)|20\d{2}q[1-4](a|e)|20\d{2}h[12](a|e))$",
        flags=re.IGNORECASE,
    )
    _PLACEHOLDER_TEXTS = {"", "-", "--", "---", "—", "——"}

    def _run(
        self,
        markdown_path: str,
        pdf_path: str,
        title: str = "Research Report",
        auto_landscape: bool = True,
        force_landscape: bool = False,
    ) -> str:
        """
        目的：对外提供最终的 Markdown 转 PDF 执行入口。
        功能：读取 Markdown、清洗表格、生成 HTML，并输出 PDF 文件。
        实现逻辑：固定走“读文件 -> 规范化 -> HTML 修表 -> WeasyPrint”这条流水线。
        可调参数：输入输出路径、标题和方向开关。
        默认参数及原因：默认不全局横向，原因是大多数正文页更适合 A4 纵向。
        """

        md_file = Path(markdown_path).expanduser().resolve()
        out_file = Path(pdf_path).expanduser().resolve()
        if not md_file.exists():
            raise FileNotFoundError(f"Markdown file not found: {md_file}")

        out_file.parent.mkdir(parents=True, exist_ok=True)
        md_text = md_file.read_text(encoding="utf-8")
        md_text = self._normalize_markdown_tables(md_text)

        html_body = self._markdown_to_html(md_text)
        soup = BeautifulSoup(html_body, "html.parser")
        self._decorate_tables(
            soup,
            allow_local_landscape=bool(auto_landscape and not force_landscape),
        )

        final_html = self._build_full_html(
            body_html=str(soup),
            title=title,
            landscape=bool(force_landscape),
        )
        HTML(string=final_html, base_url=str(md_file.parent)).write_pdf(str(out_file))
        return f"PDF created successfully at: {out_file}"

    def _markdown_to_html(self, md_text: str) -> str:
        """
        目的：把 Markdown 文本转换成后续可继续加工的 HTML。
        功能：启用表格、列表等常用扩展，输出结构稳定的 HTML。
        实现逻辑：统一使用 `Markdown(extensions=[...])` 转换，避免多处散落配置。
        可调参数：`md_text`。
        默认参数及原因：默认开启 `tables` 和 `extra`，原因是研报正文大量依赖表格与标准 Markdown 语法。
        """

        md = Markdown(extensions=["extra", "tables", "toc", "sane_lists"])
        return md.convert(md_text)

    def _normalize_markdown_tables(self, md_text: str) -> str:
        """
        目的：在 Markdown 转 HTML 前先把表格块边界整理稳定。
        功能：补齐表格前后的空行，减少表格被正文粘连导致的解析异常。
        实现逻辑：逐行扫描表头和分隔线，只在识别到标准表块时插入必要空行。
        可调参数：`md_text`。
        默认参数及原因：默认只做最小边界整理，原因是不希望在 Markdown 层改写业务内容。
        """

        lines = md_text.splitlines()
        normalized: list[str] = []
        idx = 0
        while idx < len(lines):
            if self._is_table_header(lines, idx):
                if normalized and normalized[-1].strip():
                    normalized.append("")

                while idx < len(lines) and self._is_table_row(lines[idx]):
                    normalized.append(lines[idx].rstrip())
                    idx += 1

                if idx < len(lines) and lines[idx].strip():
                    normalized.append("")
                continue

            normalized.append(lines[idx].rstrip())
            idx += 1

        normalized_text = "\n".join(normalized)
        if md_text.endswith("\n"):
            normalized_text += "\n"
        return normalized_text

    def _is_table_header(self, lines: list[str], idx: int) -> bool:
        """
        目的：识别当前位置是否是 Markdown 表格表头。
        功能：判断当前行和下一行是否构成“表头 + 分隔线”组合。
        实现逻辑：当前行要求是标准表格行，下一行要求是标准分隔线。
        可调参数：`lines` 和 `idx`。
        默认参数及原因：默认依赖标准 Markdown 表格语法，原因是仓库里的 source/pack 模板都遵守这个格式。
        """

        if idx + 1 >= len(lines):
            return False
        return self._is_table_row(lines[idx]) and self._is_table_separator(lines[idx + 1])

    def _is_table_row(self, line: str) -> bool:
        """
        目的：识别某一行是否像标准 Markdown 表格行。
        功能：判断文本是否以 `|` 开头并以 `|` 结尾。
        实现逻辑：只接受标准表格行写法，避免误伤普通列表和正文。
        可调参数：`line`。
        默认参数及原因：默认不兼容非标准写法，原因是项目模板统一使用标准管道表格。
        """

        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|")

    def _is_table_separator(self, line: str) -> bool:
        """
        目的：识别 Markdown 表格的分隔线。
        功能：判断一行文本是否符合 `| --- | --- |` 这类结构。
        实现逻辑：使用正则兼容左右对齐冒号和多个列分隔段。
        可调参数：`line`。
        默认参数及原因：默认兼容常见对齐写法，原因是 agent 输出表格时会混用默认和对齐形式。
        """

        stripped = line.strip()
        return bool(re.fullmatch(r"\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|", stripped))

    def _decorate_tables(self, soup: BeautifulSoup, *, allow_local_landscape: bool) -> None:
        """
        目的：在 HTML 层集中修正所有表格的结构和布局。
        功能：清洗空列、移除重复表头、识别数值列，并为每张表生成列宽计划。
        实现逻辑：逐表做“结构修正 -> 计算布局 -> 应用样式 -> 必要时局部横向”。
        可调参数：`soup` 和 `allow_local_landscape`。
        默认参数及原因：默认允许局部横向，原因是宽表问题应当局部处理，而不是把整份报告全局横向。
        """

        for table in soup.find_all("table"):
            self._ensure_table_sections(table, soup)
            self._drop_separator_rows(table)
            self._drop_repeated_header_rows(table)
            self._remove_empty_columns(table)
            self._ensure_table_sections(table, soup)

            headers = self._extract_header_texts(table)
            if not headers:
                continue

            layout_plan = self._build_table_layout_plan(table)
            self._apply_table_layout(
                table,
                soup,
                layout_plan,
                allow_local_landscape=allow_local_landscape,
            )

    def _ensure_table_sections(self, table: Tag, soup: BeautifulSoup) -> None:
        """
        目的：把表格结构整理成标准的 `thead + tbody` 结构。
        功能：缺少 `thead` 时把首行抬成表头，缺少 `tbody` 时把剩余行收进表体。
        实现逻辑：基于当前 DOM 原地移动节点，不改写单元格内容。
        可调参数：`table` 和 `soup`。
        默认参数及原因：默认表头取首行，原因是 Markdown 表格默认就是首行表头。
        """

        thead = table.find("thead")
        if thead is None:
            first_tr = table.find("tr")
            if first_tr is not None:
                new_thead = soup.new_tag("thead")
                first_tr.extract()
                new_thead.append(first_tr)
                table.insert(0, new_thead)

        tbody = table.find("tbody")
        if tbody is None:
            new_tbody = soup.new_tag("tbody")
            rows = [row for row in table.find_all("tr") if row.find_parent("thead") is None]
            for row in rows:
                row.extract()
                new_tbody.append(row)
            table.append(new_tbody)

    def _drop_separator_rows(self, table: Tag) -> None:
        """
        目的：删除被 Markdown 误解析进表体的分隔线行。
        功能：去掉全由 `---`、`—` 或空值组成的行，避免污染 PDF 表体。
        实现逻辑：逐行读取文本，只要整行都是占位分隔符就删除。
        可调参数：`table`。
        默认参数及原因：默认直接删除，原因是这类行只对 Markdown 解析有意义，对 PDF 内容没有价值。
        """

        tbody = table.find("tbody")
        if tbody is None:
            return
        for row in list(tbody.find_all("tr", recursive=False)):
            row_texts = self._extract_row_texts(row)
            if row_texts and all(self._is_placeholder_text(text) for text in row_texts):
                row.decompose()

    def _drop_repeated_header_rows(self, table: Tag) -> None:
        """
        目的：删除混入表体的重复表头，避免在 PDF 中显示成普通正文行。
        功能：识别与真实表头逐列一致的表体行并删除。
        实现逻辑：把表头文本标准化后，与表体每一行逐列比对。
        可调参数：`table`。
        默认参数及原因：默认只删除与表头完全相同的重复行，原因是这样风险最小。
        """

        header_texts = [self._normalize_cell_text(text) for text in self._extract_header_texts(table)]
        if not header_texts:
            return

        tbody = table.find("tbody")
        if tbody is None:
            return

        for row in list(tbody.find_all("tr", recursive=False)):
            row_texts = [self._normalize_cell_text(text) for text in self._extract_row_texts(row)]
            if row_texts == header_texts:
                row.decompose()

    def _remove_empty_columns(self, table: Tag) -> None:
        """
        目的：删除表格里纯占位的空列，释放宽度给真实内容列。
        功能：识别表头为空且整列没有有效内容的列，并在整张表上统一删掉。
        实现逻辑：先找待删列索引，再对所有行按倒序删除对应单元格。
        可调参数：`table`。
        默认参数及原因：默认只删完全无效的列，原因是避免误删真实但稀疏的数据列。
        """

        header_cells = self._extract_row_cells(table.find("thead").find("tr")) if table.find("thead") else []
        if not header_cells:
            return

        removable_indexes = [
            idx for idx in range(len(header_cells)) if self._column_should_be_removed(table, idx)
        ]
        if not removable_indexes:
            return

        for row in table.find_all("tr"):
            cells = self._extract_row_cells(row)
            for idx in sorted(removable_indexes, reverse=True):
                if idx < len(cells):
                    cells[idx].extract()

    def _column_should_be_removed(self, table: Tag, column_index: int) -> bool:
        """
        目的：判断某一列是否属于应删除的纯占位空列。
        功能：检查表头和表体对应列是否全部为空或只含占位符。
        实现逻辑：表头必须为空，且整列没有任何有效文本，才返回 `True`。
        可调参数：`table` 和 `column_index`。
        默认参数及原因：默认用“表头为空 + 全列无效”双条件，原因是比只看表头更安全。
        """

        column_texts: list[str] = []
        header_cells = self._extract_row_cells(table.find("thead").find("tr")) if table.find("thead") else []
        header_text = ""
        if column_index < len(header_cells):
            header_text = self._normalize_cell_text(header_cells[column_index].get_text(" ", strip=True))
        for row in table.find_all("tr"):
            cells = self._extract_row_cells(row)
            if column_index < len(cells):
                column_texts.append(self._normalize_cell_text(cells[column_index].get_text(" ", strip=True)))
        return not header_text and all(self._is_placeholder_text(text) for text in column_texts)

    def _build_table_layout_plan(self, table: Tag) -> TableLayoutPlan:
        """
        目的：为单张表生成内容感知的列宽和分页计划。
        功能：识别短列、期间列、数值列和长文本列，并计算最终宽度分配。
        实现逻辑：先为每列建立最小宽度和目标权重，再做总宽度归一化和横向页判断。
        可调参数：`table`。
        默认参数及原因：默认优先保护期间列和数字列不被压碎，原因是这类列最容易因挤压而难以阅读。
        """

        headers = self._extract_header_texts(table)
        body_rows = self._extract_table_body_texts(table)
        column_classes: list[str] = []
        min_widths: list[float] = []
        max_widths: list[float] = []
        weights: list[float] = []

        for index, header in enumerate(headers):
            body_texts = [
                row[index] for row in body_rows if index < len(row) and row[index].strip()
            ]
            text_pool = [header, *body_texts]
            max_len = max((len(text) for text in text_pool), default=0)
            avg_len = (
                sum(len(text) for text in text_pool) / len(text_pool)
                if text_pool
                else 0.0
            )
            numeric_ratio = (
                sum(1 for text in body_texts if self._looks_numeric(text)) / len(body_texts)
                if body_texts
                else 0.0
            )
            is_period_column = self._is_period_like(header)
            is_note_column = any(token in header.lower() for token in ("note", "remark", "备注", "说明"))
            is_long_text_column = is_note_column or max_len >= 18 or avg_len >= 10
            mostly_short_values = body_texts and sum(1 for text in body_texts if len(text) <= 8) / len(body_texts) >= 0.8

            column_class = "col-text"
            min_width = 12.0
            max_width = 22.0
            weight = 1.35

            if is_period_column:
                column_class = "col-period"
                min_width = 8.5
                max_width = 12.0
                weight = 0.95
            elif index > 0 and numeric_ratio >= 0.7:
                column_class = "col-numeric"
                min_width = 9.0
                max_width = 13.5
                weight = 1.0
            elif mostly_short_values and max_len <= 10:
                column_class = "col-compact"
                min_width = 10.0
                max_width = 15.0
                weight = 1.05
            elif is_long_text_column:
                column_class = "col-long"
                min_width = 14.0
                max_width = 30.0
                weight = 1.9

            if index == 0 and column_class not in {"col-period", "col-numeric"}:
                min_width = max(min_width, 14.0)
                max_width = max(max_width, 24.0)
                weight = max(weight, 1.55)
                if column_class == "col-compact":
                    column_class = "col-text"

            if is_note_column:
                column_class = "col-long"
                min_width = max(min_width, 16.0)
                max_width = max(max_width, 30.0)
                weight = max(weight, 2.1)

            column_classes.append(column_class)
            min_widths.append(min_width)
            max_widths.append(max_width)
            weights.append(weight)

        column_widths = self._distribute_column_widths(weights, min_widths, max_widths)
        long_column_count = sum(1 for column_class in column_classes if column_class == "col-long")
        use_landscape = (
            len(headers) >= 9
            or sum(min_widths) >= 102
            or (len(headers) >= 7 and long_column_count >= 2)
        )

        table_classes = ["report-table", "has-col-widths"]
        if use_landscape:
            table_classes.append("wide-table")
        if self._table_looks_financial(headers):
            table_classes.append("financial-table")

        return TableLayoutPlan(
            column_widths=column_widths,
            column_classes=column_classes,
            table_classes=table_classes,
            use_landscape=use_landscape,
        )

    def _distribute_column_widths(
        self,
        weights: list[float],
        min_widths: list[float],
        max_widths: list[float],
    ) -> list[float]:
        """
        目的：把每列的目标权重转换成总和为 100 的稳定宽度分配。
        功能：同时满足最小宽度、最大宽度和剩余空间分配。
        实现逻辑：先按权重给目标值，再做上下界夹紧和差额回灌。
        可调参数：`weights`、`min_widths` 和 `max_widths`。
        默认参数及原因：默认总宽度固定为 100%，原因是 PDF 表格最终都要压进页面内容区。
        """

        if not weights:
            return []

        total_min = sum(min_widths)
        if total_min >= 100:
            scale = 100 / total_min
            return [round(width * scale, 3) for width in min_widths]

        total_weight = sum(weights) or 1.0
        widths = [
            min(max((weight / total_weight) * 100, min_width), max_width)
            for weight, min_width, max_width in zip(weights, min_widths, max_widths, strict=True)
        ]

        delta = 100 - sum(widths)
        if abs(delta) <= 0.01:
            return [round(width, 3) for width in widths]

        if delta > 0:
            rooms = [max_width - width for width, max_width in zip(widths, max_widths, strict=True)]
        else:
            rooms = [width - min_width for width, min_width in zip(widths, min_widths, strict=True)]

        while abs(delta) > 0.01:
            eligible_indexes = [idx for idx, room in enumerate(rooms) if room > 0.01]
            if not eligible_indexes:
                break
            total_room = sum(rooms[idx] for idx in eligible_indexes)
            if total_room <= 0:
                break
            for idx in eligible_indexes:
                share = delta * (rooms[idx] / total_room)
                if delta > 0:
                    adjustment = min(share, rooms[idx])
                    widths[idx] += adjustment
                    rooms[idx] -= adjustment
                    delta -= adjustment
                else:
                    reduction = min(abs(share), rooms[idx])
                    widths[idx] -= reduction
                    rooms[idx] -= reduction
                    delta += reduction
                if abs(delta) <= 0.01:
                    break

        total_width = sum(widths) or 1.0
        scale = 100 / total_width
        return [round(width * scale, 3) for width in widths]

    def _apply_table_layout(
        self,
        table: Tag,
        soup: BeautifulSoup,
        layout_plan: TableLayoutPlan,
        *,
        allow_local_landscape: bool,
    ) -> None:
        """
        目的：把列宽计划和分页计划真正写回 HTML DOM。
        功能：插入 `colgroup`、补充列类型 class，并在需要时给表格包裹局部横向页容器。
        实现逻辑：先清理旧 `colgroup`，再写新宽度和 class，最后统一处理外层 wrapper。
        可调参数：`table`、`soup`、`layout_plan` 和 `allow_local_landscape`。
        默认参数及原因：默认优先使用局部横向，原因是正文页不应该被宽表拖着一起横向。
        """

        for colgroup in list(table.find_all("colgroup", recursive=False)):
            colgroup.decompose()

        colgroup = soup.new_tag("colgroup")
        for width in layout_plan.column_widths:
            col = soup.new_tag("col")
            col["style"] = f"width: {width:.3f}%;"
            colgroup.append(col)
        table.insert(0, colgroup)

        table["class"] = layout_plan.table_classes
        for row in table.find_all("tr"):
            cells = self._extract_row_cells(row)
            for index, cell in enumerate(cells):
                cell_classes = list(dict.fromkeys(cell.get("class", [])))
                if index < len(layout_plan.column_classes):
                    cell_classes.append(layout_plan.column_classes[index])
                text = cell.get_text(" ", strip=True)
                if index > 0 and self._looks_numeric(text):
                    cell_classes.append("num")
                cell["class"] = list(dict.fromkeys(cell_classes))

        wrapper = table.parent if isinstance(table.parent, Tag) else None
        wrapper_classes = ["table-block"]
        if allow_local_landscape and layout_plan.use_landscape:
            wrapper_classes.append("table-block--landscape")

        if wrapper is None or wrapper.name != "div" or "table-block" not in wrapper.get("class", []):
            new_wrapper = soup.new_tag("div")
            new_wrapper["class"] = wrapper_classes
            table.wrap(new_wrapper)
        else:
            wrapper["class"] = list(dict.fromkeys(wrapper.get("class", []) + wrapper_classes))

    def _extract_header_texts(self, table: Tag) -> list[str]:
        """
        目的：统一读取单张表的表头文本。
        功能：返回首个表头行的各列文本，供重复表头识别和列宽计算使用。
        实现逻辑：固定读取 `thead` 第一行，没有则返回空列表。
        可调参数：`table`。
        默认参数及原因：默认只看第一行表头，原因是当前仓库表格模板都是单层表头。
        """

        thead = table.find("thead")
        if thead is None:
            return []
        first_row = thead.find("tr")
        if first_row is None:
            return []
        return [self._normalize_cell_text(cell.get_text(" ", strip=True)) for cell in self._extract_row_cells(first_row)]

    def _extract_table_body_texts(self, table: Tag) -> list[list[str]]:
        """
        目的：统一读取单张表表体中的文本样本。
        功能：返回表体每一行的标准化列文本，供列类型和宽度分析使用。
        实现逻辑：固定读取 `tbody` 直属行，避免把嵌套结构误当成普通行。
        可调参数：`table`。
        默认参数及原因：默认只读取表体，原因是列宽分析更依赖真实数据而不是表头自身。
        """

        tbody = table.find("tbody")
        if tbody is None:
            return []
        rows: list[list[str]] = []
        for row in tbody.find_all("tr", recursive=False):
            rows.append([self._normalize_cell_text(text) for text in self._extract_row_texts(row)])
        return rows

    def _extract_row_texts(self, row: Tag) -> list[str]:
        """
        目的：从单行 DOM 里提取各单元格文本。
        功能：返回行内所有 `th/td` 的文本顺序列表。
        实现逻辑：按单元格顺序读取并统一空白。
        可调参数：`row`。
        默认参数及原因：默认直接读取单元格文本，原因是当前表格没有复杂嵌套表结构。
        """

        return [cell.get_text(" ", strip=True) for cell in self._extract_row_cells(row)]

    def _extract_row_cells(self, row: Tag | None) -> list[Tag]:
        """
        目的：统一读取一行里的单元格节点。
        功能：返回当前行下的 `th/td` 节点列表。
        实现逻辑：优先读直属单元格，避免意外把嵌套表的单元格一并读进来。
        可调参数：`row`。
        默认参数及原因：默认只处理普通单层行，原因是当前 Markdown 表格都属于该形态。
        """

        if row is None:
            return []
        return row.find_all(["th", "td"], recursive=False)

    def _table_looks_financial(self, headers: Iterable[str]) -> bool:
        """
        目的：粗略识别一张表是否属于财务或指标类表格。
        功能：根据表头关键词决定是否打上 `financial-table` class。
        实现逻辑：只检查表头关键词，不依赖外部业务上下文。
        可调参数：`headers`。
        默认参数及原因：默认只用轻量关键词规则，原因是这一步只影响样式，不承担业务判断责任。
        """

        header_text = " | ".join(headers).lower()
        keywords = (
            "metric",
            "revenue",
            "ebitda",
            "cash flow",
            "assets",
            "liabilities",
            "margin",
            "ratio",
            "收入",
            "利润",
            "现金流",
            "资产",
            "负债",
            "指标",
            "口径",
        )
        return any(keyword in header_text for keyword in keywords)

    def _normalize_cell_text(self, text: str) -> str:
        """
        目的：统一单元格文本的比较口径。
        功能：压缩空白、去掉首尾多余空格，供布局计算和重复行识别使用。
        实现逻辑：用空格拼回被拆开的文本，再做首尾裁剪。
        可调参数：`text`。
        默认参数及原因：默认仅做最小清洗，原因是不能改写单元格语义。
        """

        return " ".join((text or "").split()).strip()

    def _is_placeholder_text(self, text: str) -> bool:
        """
        目的：识别空列和分隔线判断里用到的占位文本。
        功能：判断文本是否为空、横线或仅由横线字符组成。
        实现逻辑：先做标准化，再匹配固定集合和纯横线正则。
        可调参数：`text`。
        默认参数及原因：默认把纯横线也视为占位符，原因是很多重复表头之间会残留 `---` 行。
        """

        normalized = self._normalize_cell_text(text)
        if normalized in self._PLACEHOLDER_TEXTS:
            return True
        return bool(re.fullmatch(r"[-—]{2,}", normalized))

    def _looks_numeric(self, value: str) -> bool:
        """
        目的：区分数值列和文本列。
        功能：识别金额、百分比、倍数、天数等常见表格数字格式。
        实现逻辑：先去掉千分位和空格，再按多个数值正则依次匹配。
        可调参数：`value`。
        默认参数及原因：默认兼容负数、百分比和 `x` 倍数，原因是财务与估值表最常见就是这些格式。
        """

        normalized = value.replace(",", "").replace(" ", "")
        patterns = (
            r"^\(?-?\d+(\.\d+)?\)?%?$",
            r"^\(?-?\d+(\.\d+)?\)?x$",
            r"^\(?-?\d+(\.\d+)?\)?天$",
            r"^\(?-?\d+(\.\d+)?\)?$",
        )
        return any(re.fullmatch(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)

    def _is_period_like(self, value: str) -> bool:
        """
        目的：识别期间列标题，给年份和 TTM 等列更紧凑的固定宽度。
        功能：识别 `FY-1`、`FQ0/FY0`、`2025A`、`TTM` 等常见期间标签。
        实现逻辑：先标准化文本，再用统一正则和年份补充规则匹配。
        可调参数：`value`。
        默认参数及原因：默认只识别常见财务期间写法，原因是这足以覆盖当前仓库的表头模板。
        """

        normalized = self._normalize_cell_text(value).lower()
        if not normalized:
            return False
        if self._PERIOD_HEADER_PATTERN.fullmatch(normalized):
            return True
        return bool(re.fullmatch(r"(19|20)\d{2}", normalized))

    def _build_full_html(self, body_html: str, title: str, landscape: bool) -> str:
        """
        目的：把正文 HTML 包装成完整的可打印文档。
        功能：统一注入页面尺寸、页眉页脚、字号体系和表格样式。
        实现逻辑：根据 `landscape` 决定全局页面方向，同时始终保留局部横向命名页样式。
        可调参数：`body_html`、`title` 和 `landscape`。
        默认参数及原因：默认页面为 A4 纵向，原因是正文阅读优先。
        """

        page_size = "A4 landscape" if landscape else "A4"
        css = f"""
        @page {{
            size: {page_size};
            margin: 15mm 14mm 15mm 14mm;

            @top-center {{
                content: "{self._css_escape(title)}";
                font-size: 9pt;
                color: #666;
            }}

            @bottom-right {{
                content: "Page " counter(page);
                font-size: 9pt;
                color: #666;
            }}
        }}

        @page wide-table {{
            size: A4 landscape;
            margin: 12mm 12mm 14mm 12mm;

            @top-center {{
                content: "{self._css_escape(title)}";
                font-size: 9pt;
                color: #666;
            }}

            @bottom-right {{
                content: "Page " counter(page);
                font-size: 9pt;
                color: #666;
            }}
        }}

        html {{
            font-size: 12pt;
        }}

        body {{
            font-family: "SimSun", "Songti SC", "Noto Serif CJK SC", serif;
            font-size: 1rem;
            line-height: 1.5;
            color: #111;
            word-break: break-word;
            overflow-wrap: anywhere;
        }}

        h1, h2, h3, h4, h5, h6 {{
            page-break-after: avoid;
            margin-top: 0.7em;
            margin-bottom: 0.25em;
            line-height: 1.35;
        }}

        h1 {{
            font-size: 18pt;
            border-bottom: 1.4pt solid #222;
            padding-bottom: 4pt;
        }}

        h2 {{
            font-size: 15pt;
            border-bottom: 0.8pt solid #999;
            padding-bottom: 3pt;
        }}

        h3 {{
            font-size: 13pt;
        }}

        p, ul, ol, blockquote {{
            margin-top: 0;
            margin-bottom: 0.28em;
        }}

        li {{
            margin-bottom: 0.12em;
        }}

        code {{
            font-family: "Consolas", "Courier New", monospace;
            font-size: 9.5pt;
            background: #f5f5f5;
            padding: 1pt 2pt;
            border-radius: 2pt;
        }}

        pre {{
            background: #f6f6f6;
            border: 0.6pt solid #ddd;
            padding: 8pt;
            white-space: pre-wrap;
        }}

        blockquote {{
            padding-left: 8pt;
            border-left: 2pt solid #bbb;
            color: #444;
        }}

        .table-block {{
            margin: 8pt 0 12pt 0;
            break-inside: avoid;
            page-break-inside: avoid;
        }}

        .table-block--landscape {{
            page: wide-table;
        }}

        .report-table {{
            width: 100%;
            border-collapse: collapse;
            table-layout: auto;
            margin: 0;
            font-size: 10.5pt;
            line-height: 1.5;
        }}

        .report-table.has-col-widths {{
            table-layout: fixed;
        }}

        .report-table thead {{
            display: table-header-group;
        }}

        .report-table tfoot {{
            display: table-footer-group;
        }}

        .report-table tr {{
            break-inside: avoid;
            page-break-inside: avoid;
        }}

        .report-table th,
        .report-table td {{
            border: 0.6pt solid #999;
            padding: 4.2pt 5pt;
            vertical-align: top;
            overflow-wrap: anywhere;
            word-break: break-word;
            background-clip: padding-box;
        }}

        .report-table th {{
            font-weight: 700;
            text-align: center;
            background: #efefef;
        }}

        .report-table tbody tr:nth-child(even) td {{
            background: #fafafa;
        }}

        .report-table td.num {{
            text-align: right;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }}

        .report-table .col-period,
        .report-table .col-compact {{
            text-align: center;
        }}

        .report-table .col-period {{
            white-space: nowrap;
        }}

        .financial-table td:first-child,
        .financial-table th:first-child {{
            font-weight: 700;
        }}

        .wide-table th,
        .wide-table td {{
            padding: 3.6pt 4.4pt;
        }}

        hr {{
            border: none;
            border-top: 0.6pt solid #ccc;
            margin: 12pt 0;
        }}
        """

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{self._html_escape(title)}</title>
  <style>{css}</style>
</head>
<body>
{body_html}
</body>
</html>
"""

    def _html_escape(self, text: str) -> str:
        """
        目的：避免标题文本破坏 HTML 结构。
        功能：转义常见 HTML 特殊字符。
        实现逻辑：按固定顺序替换 `&`、`<`、`>` 和双引号。
        可调参数：`text`。
        默认参数及原因：默认只处理最常见字符，原因是这里的输入主要是标题文本。
        """

        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _css_escape(self, text: str) -> str:
        """
        目的：避免标题文本破坏 CSS 字符串。
        功能：转义反斜杠和双引号。
        实现逻辑：按 CSS 字符串字面量要求替换风险字符。
        可调参数：`text`。
        默认参数及原因：默认只处理最关键字符，原因是页眉文本不需要更复杂的 CSS 逃逸。
        """

        return text.replace("\\", "\\\\").replace('"', '\\"')
