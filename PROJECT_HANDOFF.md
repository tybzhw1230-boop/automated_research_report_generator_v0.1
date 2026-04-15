# Project Handoff

## 目的

这份文件给后续 agent 会话做续接说明。先读 `AGENTS.md`，再读本文件，再决定下一步操作。

本版 handoff 更新时间：`2026-04-16`。
它反映的是当前 `v0.3` source-based 主链的真实工作区状态，不再描述旧的 registry / gathering runtime。

## 仓库快照

- 本地目录：`D:\pyproject\automated_research_report_generator_v0.3`
- 当前分支：`main-v0.3`
- 当前包名：`automated_research_report_generator`
- 当前目标版本：`v0.3`
- 当前 `pyproject.toml` 版本基线：`0.2.0`
- 当前目标版本：`v0.3`
- 当前 `pyproject.toml` 版本基线：`0.2.0`
- 当前 CrewAI 依赖：`crewai[file-processing,google-genai,litellm,tools]==1.14.1`
- 当前项目类型：`[tool.crewai].type = "flow"`
- 当前运行目录类型保持为：`.cache/<run_slug>/md/...`

## 当前真实边界

当前已经落地的核心变化：

- 顶层 Flow 固定为：
  - `prepare_evidence`
  - `run_analysis_phase`
  - `run_valuation_crew`
  - `run_investment_thesis_crew`
  - `publish_if_passed`
- 旧的 runtime 依赖已经移除：
  - `registry`
  - `gathering_crew`
  - `gathering_dispatcher`
  - `registry_template.yaml`
- analysis 主链保持 source-based，但专题内允许最小中间产物：
  - 多数专题仍是 `extract_from_pdf` / `search_public_sources` / `synthesize_and_output`
  - `financial_crew` 额外产出 `finance_computed_metrics` 与 `finance_analysis`
  - `operating_metrics_crew` 额外产出 `operating_metrics_analysis`
- `DueDiligenceCrew` 已经并入 analysis 末尾
- `entry_id` 仍保留，但只保留在专题 source md 中，不再承担 registry 状态管理职责
- 最终报告附录固定拼入 `sources/` 目录下的 14 份 source md 全文，不再拼 `registry_snapshot.md`
- `main.py` 会把 stdout / stderr transcript 写到 `.cache/<run_slug>/logs/console.txt`

## 当前 Flow 行为

1. `prepare_evidence`
   - 识别 PDF 元数据
   - 创建 `.cache/<run_slug>/md/` 与 `.cache/<run_slug>/logs/`
   - 生成页索引
   - 保存 document metadata JSON
   - 刷新 `run_manifest.json`
   - 生成 `cp00_prepared`
2. `run_analysis_phase`
   - 顺序执行 7 个专题 crew
   - 在 `.cache/<run_slug>/md/research/iter_01/sources/` 下生成 14 份 source md
   - 在 `.cache/<run_slug>/md/research/iter_01/` 下额外生成：
     - `05_finance_analysis.md`
     - `06_operating_metrics_analysis.md`
   - 生成 7 个专题 pack
   - 末尾执行 `DueDiligenceCrew`
   - 产出 `08_diligence_questions.md`
   - 生成 `cp03a` 到 `cp03h`
3. `run_valuation_crew`
   - 读取 4 个 pack：
     - `peer_info`
     - `finance`
     - `operating_metrics`
     - `risk`
   - 额外读取：
     - `peer_info_peer_data_source_text`
     - `risk_search_source_text`
   - 产出：
     - `01_peers_pack.md`
     - `02_intrinsic_value_pack.md`
     - `03_valuation_pack.md`
   - 生成 `cp04_valuation`
4. `run_investment_thesis_crew`
   - 继续读取 7 个 analysis packs、3 个 valuation packs 和 `08_diligence_questions.md`
   - 不额外读取 source md
   - 产出：
     - `01_bull_thesis.md`
     - `02_neutral_thesis.md`
     - `03_bear_thesis.md`
     - `04_investment_thesis.md`
   - 生成 `cp05_thesis`
5. `publish_if_passed`
   - Flow 先拼最终 Markdown
   - 正文固定覆盖 thesis、尽调问题、7 个 analysis packs、3 个 valuation packs
   - 附录固定拼入 `sources/` 目录下的 14 份 source md 全文
   - `WriteupCrew` 只做确认和 PDF 导出
   - 生成 `cp06_writeup`

## 当前目录约定

入口层：

- `src/automated_research_report_generator/main.py`
- `src/automated_research_report_generator/llm_config.py`

Flow 层：

- `src/automated_research_report_generator/flow/common.py`
- `src/automated_research_report_generator/flow/document_metadata.py`
- `src/automated_research_report_generator/flow/models.py`
- `src/automated_research_report_generator/flow/pdf_indexing.py`
- `src/automated_research_report_generator/flow/research_flow.py`

Crew 层：

- 7 个专题 crew：
  - `history_background_crew`
  - `industry_crew`
  - `business_crew`
  - `peer_info_crew`
  - `financial_crew`
  - `operating_metrics_crew`
  - `risk_crew`
- 其它运行时 crew：
  - `due_diligence_crew`
  - `valuation_crew`
  - `investment_thesis_crew`
  - `writeup_crew`

Tool 层：

- `src/automated_research_report_generator/tools/`

测试层：

- `test_src/`

## 当前 source 与中间产物边界

`sources/` 目录固定放 14 份 source md：

- `01_history_background_file_source.md`
- `01_history_background_search_source.md`
- `02_industry_file_source.md`
- `02_industry_search_source.md`
- `03_business_file_source.md`
- `03_business_search_source.md`
- `04_peer_info_peer_list.md`
- `04_peer_info_peer_data.md`
- `05_finance_file_source.md`
- `05_finance_computed_metrics.md`
- `06_operating_metrics_file_source.md`
- `06_operating_metrics_search_source.md`
- `07_risk_file_source.md`
- `07_risk_search_source.md`

analysis 目录额外保留：

- `05_finance_analysis.md`
- `06_operating_metrics_analysis.md`
- `08_diligence_questions.md`

固定结构：

- source md 必须保留 `entry_id`
- 每条都要有：
  - `问题`
  - `期望输出`
  - `输出内容`
  - `状态`
- 允许输出：
  - `无信息`

## 当前下游输入边界

- `DueDiligenceCrew`
  - 读取 7 个专题 pack
  - 额外读取 `risk_search_source_text`
  - 不再回读其它 source md
- `ValuationCrew`
  - 读取 4 个专题 pack
  - 额外读取 `peer_info_peer_data_source_text`
  - 额外读取 `risk_search_source_text`
  - 不再回读其它 source md
- `InvestmentThesisCrew`
  - 继续只读 7 个 analysis packs + 3 个 valuation packs + diligence
- `Writeup`
  - 只消费最终 Markdown 路径与 PDF 输出路径
  - 不负责重写正文

## 当前运行时产物

单次 run 根目录：

- `.cache/<run_slug>/`

关键目录与文件：

- `.cache/<run_slug>/indexing/`
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
- `.cache/<run_slug>/md/research/iter_01/`
- `.cache/<run_slug>/md/research/iter_01/sources/`
- `.cache/<run_slug>/md/research/iter_01/05_finance_analysis.md`
- `.cache/<run_slug>/md/research/iter_01/06_operating_metrics_analysis.md`
- `.cache/<run_slug>/md/research/iter_01/08_diligence_questions.md`
- `.cache/<run_slug>/md/valuation/iter_01/`
- `.cache/<run_slug>/md/thesis/iter_01/`
- `.cache/<run_slug>/md/checkpoints/`
- `.cache/<run_slug>/md/run_manifest.json`
- `.cache/<run_slug>/md/<pdf_stem>_v2_report.md`
- `.cache/<run_slug>/md/<pdf_stem>_v2_report.pdf`

`run_manifest.json` 当前重点字段：

- `run_root_dir`
- `run_cache_dir`
- `run_artifact_dir`
- `run_log_dir`
- `analysis_source_dir`
- `analysis_source_paths`
- `page_index_file_path`
- `document_metadata_file_path`
- `diligence_questions_path`
- `investment_thesis_path`
- `final_report_markdown_path`
- `final_report_pdf_path`
- `console_log_file_path`
- `crew_log_paths`
- `failed_stage`
- `failed_crew`
- `error_message`

## 当前验证状态

截至 `2026-04-16`，已完成的验证：

- 当前 Flow 主链与 `README.md` / `AGENTS.md` / `PROJECT_HANDOFF.md` 已完成对齐
- 当前目录骨架仍保持 `.cache/<run_slug>/md/...`
- `uv run python -c "from automated_research_report_generator.flow.research_flow import ResearchReportFlow; print('ok')"`
  - 结果：`ok`
- `uv run pytest -q test_src`
  - 结果：`34 passed, 1 skipped`
  - 备注：存在 CrewAI 上游的 deprecation warnings，但当前没有测试失败

当前没有已知的结构级 blocker。

## 恢复工作时的优先顺序

1. 读 `AGENTS.md`
2. 读 `PROJECT_HANDOFF.md`
3. 看 `git status --short --branch`
4. 看 `pyproject.toml`
5. 看 `README.md`
6. 如需设计背景，再看 `design_docs/delightful-imagining-hamming.md`

## 下一步建议

- 如果继续做 v0.3，默认基于当前 source-based 主链扩展，不要恢复旧 registry / gathering 设计
- 如果要调整专题输出形状，优先改各专题 crew 的 `tasks.yaml`
- 如果要调估值输入边界，优先改 `flow/research_flow.py` 和 `crews/valuation_crew/config/tasks.yaml`
- 如果改了运行目录、日志或最终报告结构，必须同步更新 `README.md`、`PROJECT_HANDOFF.md` 和 `AGENTS.md`
