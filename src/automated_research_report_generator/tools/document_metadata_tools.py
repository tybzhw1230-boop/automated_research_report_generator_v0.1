from __future__ import annotations

# 设计目的：为 metadata 识别提供文件层辅助能力，避免识别逻辑和文件操作耦在一起。
# 模块功能：计算 metadata 路径、读写 JSON、校验指纹，并抽样候选页。
# 实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
# 可调参数：`MAX_METADATA_SOURCE_PAGES` 和 `MAX_METADATA_PAGE_CHARS`。
# 默认参数及原因：默认只抽前面的非空页并截断长度，原因是封面、目录和概览通常已经够用。

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from automated_research_report_generator.tools.pdf_page_tools import (
    compute_pdf_fingerprint,
    extract_pdf_pages,
)


MAX_METADATA_SOURCE_PAGES = 20
MAX_METADATA_PAGE_CHARS = 2500
DOCUMENT_METADATA_PERIOD_PLACEHOLDER_KEYS = (
    "{FQ0/FY0}",
    "{FQ-1}",
    "{FY-1}",
    "{FY-2}",
    "{FY-3}",
    "{FY1}",
    "{FY2}",
    "{FY3}",
    "{FY4}",
    "{FY5}",
)


def document_metadata_periods_are_complete(periods: Any) -> bool:
    """
    目的：判断 metadata 里的 periods 结构是否满足当前版本的完整键集要求。
    功能：校验 `periods` 是否为字典，且包含当前仓库约定的全部期间占位符键。
    实现逻辑：先检查类型，再逐个检查必需键是否存在；缺任一键都视为旧缓存或坏结构。
    可调参数：`periods` 可传入任意已解析 JSON 值。
    默认参数及原因：非字典直接返回 False，原因是 periods 结构异常时应触发 metadata 重建。
    """

    if not isinstance(periods, dict):
        return False
    return all(period_key in periods for period_key in DOCUMENT_METADATA_PERIOD_PLACEHOLDER_KEYS)


def load_document_metadata(metadata_path: str | Path) -> dict[str, Any]:
    """
    设计目的：提供统一的 metadata 读取入口。
    模块功能：读取 JSON 文件并还原成普通字典。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：`metadata_path`。
    默认参数及原因：固定按 UTF-8 读取，原因是项目产物统一使用 UTF-8。
    """

    path = Path(metadata_path).expanduser().resolve()
    return json.loads(path.read_text(encoding="utf-8"))


def save_document_metadata(payload: BaseModel, metadata_path: str | Path) -> str:
    """
    设计目的：把 metadata 落盘逻辑集中管理。
    模块功能：确保目录存在，再把 `payload` 保存成缩进 JSON。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：`payload` 和 `metadata_path`。
    默认参数及原因：默认保留缩进格式，原因是人工排查 metadata 时更易读。
    """

    path = Path(metadata_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def document_metadata_is_current(pdf_file_path: str | Path, metadata_path: str | Path) -> bool:
    """
    设计目的：避免重复调用模型生成已经过期的 metadata。
    模块功能：检查 metadata 文件是否存在且指纹仍匹配当前 PDF。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：`pdf_file_path` 和 `metadata_path`。
    默认参数及原因：读取或解析失败直接判定为过期，原因是宁可重建也不要复用坏文件。
    """

    path = Path(metadata_path).expanduser().resolve()
    if not path.exists():
        return False

    try:
        data = load_document_metadata(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False

    if data.get("fingerprint") != compute_pdf_fingerprint(pdf_file_path):
        return False

    return document_metadata_periods_are_complete(data.get("periods"))


def sample_document_metadata_pages(
    pdf_file_path: str | Path,
    max_pages: int = MAX_METADATA_SOURCE_PAGES,
    max_chars_per_page: int = MAX_METADATA_PAGE_CHARS,
) -> list[tuple[int, str]]:
    """
    设计目的：给 metadata agent 提供高密度候选页。
    模块功能：按页扫描 PDF，只保留前若干个非空页。
    实现逻辑：按当前定义的输入、处理和返回顺序执行，直接复用本函数或类里已经写好的步骤。
    可调参数：最大页数和每页最大字符数。
    默认参数及原因：优先保留靠前页面，原因是更容易覆盖封面、目录和概览。
    """

    sampled_pages: list[tuple[int, str]] = []
    for page_number, page_text in enumerate(extract_pdf_pages(pdf_file_path), start=1):
        if page_text.strip():
            sampled_pages.append((page_number, page_text[:max_chars_per_page]))
        if len(sampled_pages) >= max_pages:
            break
    return sampled_pages
