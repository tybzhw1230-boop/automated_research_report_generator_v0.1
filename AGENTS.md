# Project-Specific Continuation Note

Before starting work on this repository, read `PROJECT_HANDOFF.md`.
This repository no longer keeps `PROJECT_CONVERSATIONS/` in-tree, so do not assume the archive directory exists.

# AGENTS.md - automated_research_report_generator v0.3

## 第一性原则

1. 先确认问题的最小真实边界，再动代码，不要靠猜。
2. 优先看公开官方文档，尤其是 CrewAI 文档和 changelog。
3. 处理文件时默认按 UTF-8 读写，避免中英文内容损坏。
4. 新增或修改 Python 代码时，继续保持中文注释可读、直接、贴近代码。

## 当前仓库状态

- 当前 Python 包前缀固定为 `automated_research_report_generator`。
- 当前主包目录固定为 `src/automated_research_report_generator/`。
- 当前工作区目标版本是 `v0.3`。
- 当前 `pyproject.toml` 仍保留 `0.2.0` 基线，只有在正式切版或发版时再改。
- 当前 CrewAI 依赖固定为 `crewai[file-processing,google-genai,litellm,tools]==1.14.1`。
- 当前项目类型是 Flow 项目，`[tool.crewai].type = "flow"`。
- 当前 GitHub 远端仓库名仍是 `automated_research_report_generator_v0.1`，不要把远端名字和本地包路径混淆。
- `pdf/` 和 `output/` 目录是正式产物目录，当前仓库允许跟踪它们。
- `.env`、`.venv/`、`.cache/`、`logs/` 和本地临时目录仍然应保持不入库。

## 当前目录约定

### 1. Flow 相关模块

凡是直接服务于 Flow 编排、Flow 状态、Flow 预处理、Flow 中间产物管理的 Python 模块，都放在：

`src/automated_research_report_generator/flow/`

当前核心模块包括：

- `common.py`
- `document_metadata.py`
- `models.py`
- `pdf_indexing.py`
- `research_flow.py`

### 2. 入口层

- 命令行和脚本入口保留在 `src/automated_research_report_generator/main.py`
- 模型配置入口保留在 `src/automated_research_report_generator/llm_config.py`
- `main.py` 只负责参数解析、默认输入和调用 Flow，不承载具体业务编排细节

### 3. Crew 层

Crew 定义统一放在：

`src/automated_research_report_generator/crews/`

当前包含：

- `history_background_crew`
- `industry_crew`
- `business_crew`
- `peer_info_crew`
- `financial_crew`
- `operating_metrics_crew`
- `risk_crew`
- `due_diligence_crew`
- `valuation_crew`
- `investment_thesis_crew`
- `writeup_crew`

补充说明：

- 当前主 Flow 使用的是 7 个专题 research crews，analysis 末尾再串 `due_diligence_crew`。

### 4. Tool 层

工具统一放在：

`src/automated_research_report_generator/tools/`

Flow 与 Crew 都通过 `tools/` 访问工具，不要把工具逻辑重新散落回 `flow/` 或 `crews/`。

### 5. 测试层

测试统一放在：

`test_src/`

当前已有：

- Flow 编排与阶段接线测试
- PDF indexing 与并发测试
- sub-crew 配置边界测试
- 终端 transcript / console logging 测试
- UTF-8 文本编码测试
- Tushare / v2 tools 测试

## 当前导入规则

1. 不要再从包根目录导入这些旧路径：
   - `automated_research_report_generator.common`
   - `automated_research_report_generator.document_metadata`
   - `automated_research_report_generator.models`
   - `automated_research_report_generator.pdf_indexing`
   - `automated_research_report_generator.research_flow`
2. 现在统一改为从 `automated_research_report_generator.flow` 下导入。
3. 新增 Flow 相关模块时，先判断它是不是“被 Flow 直接调用或共享的流程级模块”。
   - 如果是，放进 `flow/`
   - 如果是 Crew 定义，放进 `crews/`
   - 如果是工具，放进 `tools/`

## CrewAI 新鲜度检查

在修改任何 CrewAI 相关代码前，先做下面几件事：

1. 检查本地安装版本：
   - `uv run python -c "import crewai; print(crewai.__version__)"`
2. 检查 PyPI 最新版本：
   - `https://pypi.org/pypi/crewai/json`
3. 看官方 changelog：
   - `https://docs.crewai.com/en/changelog`
4. 按修改内容查看对应概念文档：
   - `https://docs.crewai.com/en/concepts/agents`
   - `https://docs.crewai.com/en/concepts/tasks`
   - `https://docs.crewai.com/en/concepts/flows`
   - `https://docs.crewai.com/en/concepts/tools`
5. 如果本文件和官方文档冲突，以官方文档为准，再反向更新本文件。

截至本次整理时，本地 `crewai` 版本是 `1.14.1`。

## 当前 Flow 真实行为

1. `prepare_evidence`
   - 识别 PDF 元数据
   - 创建 `.cache/<run_slug>/md/` 与 `.cache/<run_slug>/logs/`
   - 生成页面索引与 document metadata
   - 刷新 `run_manifest.json`
   - 生成 `cp00_prepared`
2. `run_analysis_phase`
   - 顺序执行 7 个专题 crews
   - 生成 `sources/` 目录下的 14 份 source md
   - 额外生成 `05_finance_analysis.md` 与 `06_operating_metrics_analysis.md`
   - 产出 `history_background`、`industry`、`business`、`peer_info`、`finance`、`operating_metrics`、`risk` 七个 pack
   - 末尾执行 `DueDiligenceCrew`
   - 产出 `08_diligence_questions.md`
   - 生成 `cp03a` 到 `cp03h`
3. `run_valuation_crew`
   - 读取 4 个专题 pack：`peer_info`、`finance`、`operating_metrics`、`risk`
   - 额外读取 `peer_info_peer_data_source_text` 与 `risk_search_source_text`
   - 产出 `01_peers_pack.md`、`02_intrinsic_value_pack.md`、`03_valuation_pack.md`
   - 生成 `cp04_valuation`
4. `run_investment_thesis_crew`
   - 读取 7 个 analysis packs、3 个 valuation packs 与 `08_diligence_questions.md`
   - 不额外读取 source md
   - 产出 `01_bull_thesis.md`、`02_neutral_thesis.md`、`03_bear_thesis.md`、`04_investment_thesis.md`
   - 生成 `cp05_thesis`
5. `publish_if_passed`
   - Flow 先确定性拼装最终 Markdown
   - 正文固定覆盖 thesis、尽调问题、7 个 analysis packs、3 个 valuation packs
   - 附录固定拼入 `sources/` 目录下的 14 份 source md 全文
   - `WriteupCrew` 只做确认和 PDF 导出
   - 生成 `cp06_writeup`

当前下游输入边界：

- `DueDiligenceCrew`
  - 读取 7 个专题 pack
  - 额外读取 `risk_search_source_text`
  - 不回读其它 source md
- `ValuationCrew`
  - 读取 4 个专题 pack
  - 额外读取 `peer_info_peer_data_source_text`
  - 额外读取 `risk_search_source_text`
  - 不回读其它 source md
- `InvestmentThesisCrew`
  - 继续只读 7 个 analysis packs + 3 个 valuation packs + diligence
- `WriteupCrew`
  - 只消费最终 Markdown 和 PDF 输出路径
  - 不负责重写正文

## 当前运行产物和日志

单次 run 的关键产物：

- 运行缓存：`.cache/<run_slug>/`
- 中间产物目录：`.cache/<run_slug>/md/`
- source 目录：`.cache/<run_slug>/md/research/iter_01/sources/`
- 专题中间分析产物：`.cache/<run_slug>/md/research/iter_01/05_finance_analysis.md`
- 专题中间分析产物：`.cache/<run_slug>/md/research/iter_01/06_operating_metrics_analysis.md`
- 尽调问题：`.cache/<run_slug>/md/research/iter_01/08_diligence_questions.md`
- Checkpoints：`.cache/<run_slug>/md/checkpoints/cpXX_*.json`
- 运行索引：`.cache/<run_slug>/md/run_manifest.json`
- 最终 Markdown：`.cache/<run_slug>/md/<pdf_stem>_v2_report.md`
- 最终 PDF：`.cache/<run_slug>/md/<pdf_stem>_v2_report.pdf`

单次 run 日志：

- `.cache/<run_slug>/logs/preprocess.txt`
- `.cache/<run_slug>/logs/flow.txt`
- `.cache/<run_slug>/logs/console.txt`
- `.cache/<run_slug>/logs/history_background_crew.txt`
- `.cache/<run_slug>/logs/industry_crew.txt`
- `.cache/<run_slug>/logs/business_crew.txt`
- `.cache/<run_slug>/logs/peer_info_crew.txt`
- `.cache/<run_slug>/logs/financial_crew.txt`
- `.cache/<run_slug>/logs/operating_metrics_crew.txt`
- `.cache/<run_slug>/logs/risk_crew.txt`
- `.cache/<run_slug>/logs/due_diligence_crew.txt`
- `.cache/<run_slug>/logs/valuation_crew.txt`
- `.cache/<run_slug>/logs/investment_thesis_crew.txt`
- `.cache/<run_slug>/logs/writeup_crew.txt`

说明：

- 当前代码里没有项目级 `latest_run.json` 这套 latest 索引
- `run_manifest.json` 会写当前 run 的路径索引
- `flow`、各个专题 crew、diligence、valuation、thesis、writeup 的日志都可以分开看
- `console.txt` 保留 PowerShell 可见输出的原始 transcript

## 当前环境与工具约定

- LLM 配置统一从 `src/automated_research_report_generator/llm_config.py` 取
- 当前模型供应商是 OpenRouter，核心环境变量是 `OPENROUTER_API_KEY`
- 多个 research sub-crews 的公开资料搜索通常需要 `SERPER_API_KEY`
- A 股估值工具 `TushareValuationDataTool` 需要 `TUSHARE_TOKEN`
- PDF 页面索引并发默认值当前是 `100`
  - 可通过 `PDF_INDEX_MAX_CONCURRENCY` 覆盖
- 页面索引重试次数默认值当前是 `2`
  - 可通过 `PDF_INDEX_RETRY_LIMIT` 覆盖

## 注释与编码要求

### 编码

- 默认按 UTF-8 处理文本文件。
- 修改含中文的 Markdown、Python、YAML 文件时，先确认不会把编码写坏。

### 注释

- 所有 `class` 和 `def` 都要有中文注释。
- 注释尽量包含这五个模块：
  - 目的
  - 功能
  - 实现逻辑
  - 可调参数
  - 默认参数及原因
- 注释要简单直白，不要故作复杂。

## 运行方式

安装依赖：

```bash
uv sync
```

运行主 Flow：

```bash
crewai run
```

或直接指定 PDF：

```bash
uv run python -m automated_research_report_generator.main --pdf <PDF路径>
```

生成 Flow 图：

```bash
uv run python -m automated_research_report_generator.main --plot
```

## 修改后的最低检查

每次做完结构调整，至少检查这几件事：

1. 所有受影响的 `from import` 是否已经同步更新。
2. `main.py` 是否还能正确导入 `ResearchReportFlow`。
3. `crews/`、`tools/`、`test_src/` 中是否还残留旧导入路径。
4. 至少做一次导入级 smoke test，确认包路径没有断。
5. 如果改动了日志、人审或运行目录，要同步检查 `README.md` 和 `PROJECT_HANDOFF.md` 是否还准确。

## 恢复工作时的优先顺序

1. 读 `AGENTS.md`
2. 读 `PROJECT_HANDOFF.md`
3. 看 `git status --short --branch`
4. 看 `pyproject.toml`
5. 看 `README.md`
6. 再决定是否需要看 `design_docs/` 下的设计讨论文档
