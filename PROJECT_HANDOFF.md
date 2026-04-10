# Project Handoff

## 目的

这份文件是当前仓库给后续 agent 会话使用的续接说明。
先读 `AGENTS.md`，再读本文件，再决定下一步操作。

本版 handoff 生成时间：`2026-04-09`。
它用于替换旧版 handoff，因为工作区里的 `PROJECT_HANDOFF.md` 当时已经缺失，而旧内容仍停留在更早的 GitHub 发布阶段。

## 仓库快照

- 本地目录：`D:\pyproject\automated_research_report_generator_v0.2`
- 当前分支：`main`
- 当前 `HEAD`：`7c41ed3e6efb74e939da326897ed39e5cc642e48`
- 最近提交：`7c41ed3 Add crew refactor draft and tighten LLM retry settings`
- 远端仓库：`https://github.com/tybzhw1230-boop/automated_research_report_generator_v0.1`
- 当前包名：`automated_research_report_generator`
- 当前版本：`0.2.0`
- 当前 CrewAI 依赖：`crewai[file-processing,google-genai,litellm,tools]==1.14.0`
- 当前项目类型：`[tool.crewai].type = "flow"`

## 当前真实工作状态

当前工作区不是干净状态，而且是“两层迁移叠在一起”的状态，后续继续工作前一定要先分清楚 index 和 working tree。

第一层是“已暂存”的迁移：

- 把旧包 `src/automated_research_report_generator_v0_1/` 迁到新包 `src/automated_research_report_generator/`
- 新增 `crews/`、`tools/`、`main.py`
- 更新 `README.md`、`pyproject.toml`
- 删除旧脚本 `scripts/export_codex_project_conversations.py`

第二层是“未暂存”的进一步重构：

- 把原先放在包根目录的 Flow 共享模块继续下沉到 `src/automated_research_report_generator/flow/`
- 新增 `src/automated_research_report_generator/llm_config.py`
- 新增 `src/automated_research_report_generator/tools/registry_tools.py`
- 新增 `src/automated_research_report_generator/tools/tushare_tools.py`
- 新增多份 `test_src/` 测试文件
- 删除工作区中的 `PROJECT_CONVERSATIONS/`
- 删除工作区中的旧 `PROJECT_HANDOFF.md`
- 删除工作区中的若干 `output/` 历史产物

结论：

- 不要假设 “已暂存内容” 就等于 “当前真实代码”
- 提交前必须分别看 `git diff --cached` 和 `git diff`
- 如果要继续整理这次迁移，先确认是沿用当前工作区结构，还是回退到已暂存那一版结构

## 当前未跟踪文件

截至 `2026-04-09`，`git ls-files --others --exclude-standard` 里最重要的未跟踪文件有：

- `.editorconfig`
- `manual_registry_runtime_check.json`
- `src/automated_research_report_generator/flow/`
- `src/automated_research_report_generator/llm_config.py`
- `src/automated_research_report_generator/tools/registry_tools.py`
- `src/automated_research_report_generator/tools/tushare_tools.py`
- `test_src/test_flow_pdf_indexing.py`
- `test_src/test_research_flow_gate.py`
- `test_src/test_runtime_cache_cleanup.py`
- `test_src/test_text_file_encoding.py`
- `test_src/test_tushare_tools.py`

说明：

- 当前真正要保留的 v0.2 Flow 结构就在这些未跟踪文件里
- 如果后续要提交迁移结果，这批文件大概率需要进入版本控制

## 当前代码的真实目录约定

以工作区当前代码为准，目录已经基本稳定为：

- 入口：`src/automated_research_report_generator/main.py`
- LLM 配置：`src/automated_research_report_generator/llm_config.py`
- Flow 层：`src/automated_research_report_generator/flow/`
- Crew 层：`src/automated_research_report_generator/crews/`
- Tool 层：`src/automated_research_report_generator/tools/`
- 测试层：`test_src/`

`flow/` 下当前关键模块包括：

- `common.py`
- `document_metadata.py`
- `models.py`
- `pdf_indexing.py`
- `registry.py`
- `research_flow.py`

## 运行时真相

这里以当前工作区代码为准，不以旧 handoff 或旧经验为准。

- 单次运行根目录：`.cache/<run_slug>/`
- 中间产物目录：`.cache/<run_slug>/md/`
- 日志目录：`.cache/<run_slug>/logs/`
- 运行索引：`.cache/<run_slug>/md/run_manifest.json`
- 证据注册表：`.cache/<run_slug>/md/registry/evidence_registry.json`
- 最终 Markdown：`.cache/<run_slug>/md/<pdf_stem>_v2_report.md`
- 最终 PDF：`.cache/<run_slug>/md/<pdf_stem>_v2_report.pdf`

当前代码里没有项目级 `logs/latest_run.json` 这套 latest 索引。
当前 `.gitignore` 仍然忽略这些本地目录：

- `.env`
- `.venv/`
- `.cache/`
- `crewai_memory/`
- `logs/`

## 文档状态

当前 `README.md`、`AGENTS.md`、`PROJECT_HANDOFF.md` 已经同步到现行实现：

- 单次 run 产物主要写到 `.cache/<run_slug>/md/`
- research 阶段已经拆成 7 个 sub-crews
- 只有 research 阶段保留外部 QA gate

如果后续改动了运行目录、日志目录、manifest 结构或 Flow 主链路，要同步更新这三份文档。

## 当前 Flow 行为摘要

当前主流程仍是：

1. `prepare_evidence`
2. `build_research_plan`
3. `run_research_crew`
   - 顺序执行 7 个 research sub-crews
   - 产出 `history_background`、`industry`、`business`、`peer_info`、`finance`、`operating_metrics`、`risk` 七个 pack
4. `review_research_gate`
   - 只做跨 pack 一致性与覆盖度 QA
   - 如果失败，只定向重跑 `affected_packs`
5. `run_valuation_crew`
   - 不再走外部 valuation QA gate
6. `run_investment_thesis_crew`
   - 不再走外部 thesis QA gate
7. `publish_if_passed`

QA gate 规则仍是：

- 只有 `research` 阶段保留外部 gate
- `max_research_loops = 1`，表示 research 最多自动返工 1 次
- 换算成总执行次数，就是 research 最多执行 2 次
- 第二次仍未通过时，会走 `force pass`
- `valuation_crew` 的 QA 已经内收进 crew 内部
- thesis 阶段不再设置外部 gate

## 建议的恢复顺序

后续会话继续工作时，建议按这个顺序恢复：

1. 读 `AGENTS.md`
2. 读本文件
3. 看 `git status --short --branch`
4. 分开看 `git diff --cached` 和 `git diff`
5. 看 `pyproject.toml`
6. 看 `README.md`
7. 再决定是否需要看 `design_docs/cryptic-petting-treasure.md` 或 `design_docs/CREW_REFACTOR_WORKING_DRAFT.md`
8. 如果继续处理迁移，先做一次导入级 smoke test，确认 `automated_research_report_generator.flow` 路径没有断

## 本次 handoff 的边界

这次 handoff 之后，当前已确认的边界是：

- 7 个 research sub-crews、统一 registry entry 模型、research-only QA 和 checkpoint 已经落地
- `test_src` 当前全量测试通过
- registry/tool 链路上的旧 `question/judgment` 兼容接口已移除，当前以 `entry` 命名为准
