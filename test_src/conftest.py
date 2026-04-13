from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if SRC_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, SRC_ROOT.as_posix())


def _reset_local_package_import_cache() -> None:
    """
    目的：确保测试总是从当前仓库的 `src/` 读取包，而不是误用旧环境里已安装的同名版本。
    功能：清理 `automated_research_report_generator` 相关的已加载模块缓存。
    实现逻辑：遍历 `sys.modules`，删除包根和其所有子模块，让后续导入重新走当前 `sys.path`。
    可调参数：当前无。
    默认参数及原因：默认全量清理该包前缀，原因是仓库里正在做结构调整，最容易受旧安装包污染。
    """

    for module_name in list(sys.modules):
        if module_name == "automated_research_report_generator" or module_name.startswith(
            "automated_research_report_generator."
        ):
            sys.modules.pop(module_name, None)


_reset_local_package_import_cache()
