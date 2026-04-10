# Automated Research Report Generator v0.2

一个基于 CrewAI Flow 的买方研究报告生成项目。当前仓库已经完成从旧版单包结构到 `v0.2` Flow 架构的迁移，核心特征是：

- 稳定包路径：`src/automated_research_report_generator/`
- 多阶段 Flow：预处理、规划、7 个 research sub-crews、估值、投资主线、成文
- 证据注册表：每次 run 都会生成 `evidence_registry.json` 和 `registry_snapshot.md`
- Research QA Gate：只在 research 阶段做外部 QA；最多自动运行 2 次（含初始运行），第二次仍未通过则直接 `force pass`
- Valuation 内部 QA：`valuation_crew` 使用 Hierarchical Process，由 manager 在 crew 内协调校验
- 分组件日志与检查点：`flow`、各个 sub-crew、QA 和 checkpoint 分别落盘，方便排查

## 当前状态

- 当前项目版本：`0.2.0`
- 当前 CrewAI 版本：`1.14.0`
- 当前项目类型：`[tool.crewai].type = "flow"`
- 当前主入口：`src/automated_research_report_generator/main.py`
- 当前主 Flow：`src/automated_research_report_generator/flow/research_flow.py`
- 当前 GitHub 远端仓库名仍是 `automated_research_report_generator_v0.1`
- `pdf/` 目录当前允许跟踪到 Git
- `.env`、`.venv/`、`.cache/`、`crewai_memory/` 和本地临时目录仍然视为本地运行目录

## 当前 Flow 链路

1. `prepare_evidence`
   - 识别 PDF 元数据
   - 生成逐页主题索引
   - 初始化 evidence registry
2. `build_research_plan`
   - 生成 `research_scope`
   - 生成 `question_tree`
   - 生成 `evidence_map_seed`
3. `run_research_crew`
   - 顺序执行 7 个 research sub-crews
   - 输出 `history_background`、`industry`、`business`、`peer_info`、`finance`、`operating_metrics`、`risk` 七个 pack
4. `review_research_gate`
   - 只做跨 pack 一致性与覆盖度 QA
   - 如果失败，只定向重跑受影响的 pack
5. `run_valuation_crew`
   - 输出 `peers_pack`、`intrinsic_value_pack` 和 `valuation_pack`
   - 不再走外部 valuation QA gate
6. `run_investment_thesis_crew`
   - 输出投资主线和尽调问题
   - 不再走外部 thesis QA gate
7. `publish_if_passed`
   - 生成最终 Markdown 和 PDF

## QA Gate 与自动放行

- 只有 `research` 阶段保留外部 QA gate
- `review_research_gate` 只检查跨 pack 一致性、覆盖度和未关闭缺口
- Research QA 最多自动运行 `2` 次（含初始运行）
- 第二次运行后如果 QA 仍未通过，会直接自动 `force pass`
- `valuation_crew` 在 crew 内部完成自校验，不再走外部 gate
- `run_investment_thesis_crew` 直接进入成文阶段，不再走外部 gate

## 运行产物

单次 run 的主要产物路径如下：

- 运行根目录：`.cache/<run_slug>/`
- 中间产物目录：`.cache/<run_slug>/md/`
- 运行日志目录：`.cache/<run_slug>/logs/`
- 证据注册表：`.cache/<run_slug>/md/registry/evidence_registry.json`
- Registry Markdown 快照：`.cache/<run_slug>/md/registry/registry_snapshot.md`
- Registry 历史快照：`.cache/<run_slug>/md/registry/snapshots/`
- Checkpoints：`.cache/<run_slug>/md/checkpoints/cpXX_*.json`
- 运行索引：`.cache/<run_slug>/md/run_manifest.json`
- 最终 Markdown：`.cache/<run_slug>/md/<pdf_stem>_v2_report.md`
- 最终 PDF：`.cache/<run_slug>/md/<pdf_stem>_v2_report.pdf`

## 日志结构

当前日志已经按 run 维度归档，并在 run 目录内继续按 `flow`、`preprocess` 和各个 `crew` 拆分。

单次 run 日志：

- `.cache/<run_slug>/logs/preprocess.txt`
- `.cache/<run_slug>/logs/flow.txt`
- `.cache/<run_slug>/logs/planning_crew.txt`
- `.cache/<run_slug>/logs/history_background_crew.txt`
- `.cache/<run_slug>/logs/industry_crew.txt`
- `.cache/<run_slug>/logs/business_crew.txt`
- `.cache/<run_slug>/logs/peer_info_crew.txt`
- `.cache/<run_slug>/logs/financial_crew.txt`
- `.cache/<run_slug>/logs/operating_metrics_crew.txt`
- `.cache/<run_slug>/logs/risk_crew.txt`
- `.cache/<run_slug>/logs/qa_research.txt`
- `.cache/<run_slug>/logs/valuation_crew.txt`
- `.cache/<run_slug>/logs/investment_thesis_crew.txt`
- `.cache/<run_slug>/logs/writeup_crew.txt`

说明：

- 不再保留 `latest_run.json` 这类项目级 latest 索引
- `run_manifest.json` 跟本次 run 的中间产物一起落在 `md/` 目录
- `flow` 日志记录阶段推进、路由和 gate 状态
- `crew` 日志记录各 crew 自身输出
- 预处理阶段的 PDF 页面索引日志写入同一次 run 的 `preprocess.txt`

## 项目目录

```text
.
├─ AGENTS.md
├─ PROJECT_HANDOFF.md
├─ design_docs/
├─ pdf/
├─ output/
├─ src/
│  └─ automated_research_report_generator/
│     ├─ main.py
│     ├─ llm_config.py
│     ├─ flow/
│     ├─ crews/
│     └─ tools/
└─ test_src/
```

## 环境要求

- Python `>=3.10,<3.14`
- 建议使用 `uv`
- 当前代码默认在 Windows 环境下开发和验证

## 环境变量

必需：

- `OPENROUTER_API_KEY`

通常需要：

- `SERPER_API_KEY`
  - 多个 research sub-crews 使用 `SerperDevTool` 做公开资料搜索

按场景需要：

- `TUSHARE_TOKEN`
  - A 股估值工具 `TushareValuationDataTool` 需要
- `PDF_INDEX_MAX_CONCURRENCY`
  - 覆盖 PDF 页面索引最大并发，当前默认值为 `100`
- `PDF_INDEX_RETRY_LIMIT`
  - 覆盖页面索引失败重试次数，当前默认值为 `2`

## 安装

优先使用 `uv`：

```bash
uv sync
```

如果只想用 `pip`：

```bash
pip install -r requirements.txt
```

## 运行

运行主 Flow：

```bash
crewai run
```

直接指定 PDF：

```bash
uv run python -m automated_research_report_generator.main --pdf <PDF路径>
```

绘制 Flow 图：

```bash
uv run python -m automated_research_report_generator.main --plot
```

## 编码约束

- 仓库里的 Python、YAML、Markdown、TOML、JSON 等文本文件统一使用 `UTF-8` 且不带 BOM
- Windows 环境下不要依赖编辑器、终端或脚本的系统默认编码，尤其不要把中文文件按 ANSI、GBK 或其他本地代码页重新保存
- 当前仓库已经提供 [.editorconfig](./.editorconfig) 约束常见文本文件编码
- 如果你怀疑又出现了中文乱码，可以先运行下面的编码检查：

```bash
uv run pytest test_src/test_text_file_encoding.py -q
```

## 测试

- 测试文件统一放在 `test_src/`
- 当前仓库已经有 flow、registry、tools、Tushare、PDF indexing 相关测试文件
- 当前锁定环境没有把 `pytest` 作为默认依赖写进 `pyproject.toml`

如果你要运行测试，请先保证环境里有 `pytest`，然后执行：

```bash
uv run pytest test_src
```

## 说明

- 默认 PDF 路径来自 `src/automated_research_report_generator/flow/common.py` 中的 `DEFAULT_PDF_PATH`
- 当前 LLM 入口统一放在 `src/automated_research_report_generator/llm_config.py`
- 当前项目的主模型供应商是 OpenRouter，不再使用 README 里常见的 `OPENAI_API_KEY` 约定
