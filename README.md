# Automated Research Report Generator v0.3
# Automated Research Report Generator v0.3

一个基于 CrewAI Flow 的买方研究报告生成项目。当前工作区处于 `v0.3` 的最小可跑通阶段，重点是把 source-based 主链、估值链路、投资逻辑链路和最终导出链路稳定下来。

## 当前状态

- 当前主链固定为：`prepare_evidence -> run_analysis_phase -> run_valuation_crew -> run_investment_thesis_crew -> publish_if_passed`
- 当前 CrewAI 依赖固定为：`crewai[file-processing,google-genai,litellm,tools]==1.14.1`
- 当前项目类型固定为：`[tool.crewai].type = "flow"`
- 当前运行目录类型保持为：`.cache/<run_slug>/md/...`
- 旧的 `registry`、`gathering_crew`、`gathering_dispatcher`、`registry_template.yaml` 已从当前主链移除
- `main.py` 会把本次运行的 PowerShell 原始输出同步写入 `.cache/<run_slug>/logs/console.txt`
- analysis 主链保持 source-based，但允许专题内保留最小中间产物
  - 多数专题仍是：`extract_from_pdf` + `search_public_sources` + `synthesize_and_output`
  - `financial_crew` 为四段式：抽取、计算、分析、汇总
  - `operating_metrics_crew` 为四段式：抽取、搜索、分析、汇总
- `entry_id` 仍保留，但只作为 source md 的追踪锚点，不再承担 registry 状态管理职责

## v0.3 主流程

1. `prepare_evidence`
   - 识别 PDF 元数据
   - 生成页面索引
   - 创建 `.cache/<run_slug>/md/` 与 `.cache/<run_slug>/logs/`
   - 写入 document metadata、`run_manifest.json` 和 `cp00_prepared`
2. `run_analysis_phase`
   - 顺序执行 7 个专题 crew
   - 生成 `sources/` 目录下的 14 份 source md
   - 额外生成 `05_finance_analysis.md` 与 `06_operating_metrics_analysis.md`
   - 生成 7 个专题 pack
   - 末尾执行 `DueDiligenceCrew`，产出 `08_diligence_questions.md`
3. `run_valuation_crew`
   - 读取 4 个专题 pack：`peer_info`、`finance`、`operating_metrics`、`risk`
   - 额外读取 `peer_info_peer_data_source_text` 与 `risk_search_source_text`
   - 产出 `01_peers_pack.md`、`02_intrinsic_value_pack.md`、`03_valuation_pack.md`
4. `run_investment_thesis_crew`
   - 读取 7 个 analysis packs、3 个 valuation packs 与尽调问题
   - 产出 `01_bull_thesis.md`、`02_neutral_thesis.md`、`03_bear_thesis.md`、`04_investment_thesis.md`
5. `publish_if_passed`
   - `WriteupCrew` 现会在最终 Markdown 就绪后继续生成 `pitch material Markdown`、`investment snapshot PPTX` 和最终 `PDF`
   - Flow 先确定性拼装最终 Markdown
   - 正文固定覆盖 thesis、尽调问题、7 个 analysis packs、3 个 valuation packs
   - 附录固定拼入 `sources/` 目录下的 14 份 source md 全文
   - `WriteupCrew` 只做非破坏性确认与导出，不再改写正文；导出产物现包含 `pitch material Markdown`、`investment snapshot PPTX` 和 `PDF`

## Crew 结构

当前专题 crew：

- `history_background_crew`
- `industry_crew`
- `business_crew`
- `peer_info_crew`
- `financial_crew`
- `operating_metrics_crew`
- `risk_crew`

当前运行时 crew：

- `due_diligence_crew`
- `valuation_crew`
- `investment_thesis_crew`
- `writeup_crew`

结构要点：

- 多数专题 crew 暴露 3 个 agents + 3 个 tasks
- `financial_crew` 暴露 4 个 agents + 4 个 tasks
- `operating_metrics_crew` 暴露 4 个 agents + 4 个 tasks
- `due_diligence_crew` 为 1 个 agent + 1 个 task
- `valuation_crew` 为 3 个 agents + 3 个 tasks，其中前两步并行、第三步汇总
- `writeup_crew` 只负责确认最终 Markdown 并导出 PDF

## Source 与中间产物边界

`research/iter_01/sources/` 固定放 14 份 source md：

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

`research/iter_01/` 额外保留专题中间产物：

- `05_finance_analysis.md`
- `06_operating_metrics_analysis.md`
- `08_diligence_questions.md`

说明：

- `entry_id` 必须保留
- source md 允许写“无信息”
- `05_finance_analysis.md` 和 `06_operating_metrics_analysis.md` 不属于附录 source 集合

## 下游输入边界

- `DueDiligenceCrew`
  - 读取 7 个专题 pack
  - 额外读取 `risk_search_source_text`
  - 不回读其它 source md
- `ValuationCrew`
  - 读取 `peer_info_pack_text`、`finance_pack_text`、`operating_metrics_pack_text`、`risk_pack_text`
  - 额外读取 `peer_info_peer_data_source_text` 与 `risk_search_source_text`
  - 不回读其它 source md
- `InvestmentThesisCrew`
  - 读取 7 个专题 pack、3 个 valuation packs 与 `diligence_questions_text`
  - 不额外读取 source md
- `WriteupCrew`
  - ???? Markdown ???? writeup ????? pack/thesis ????
  - ?? `pitch material Markdown`?`investment snapshot PPTX` ? `PDF`
  - ???????

## 运行产物

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
- `.cache/<run_slug>/md/<pdf_stem>_pitch_material.md`
- `.cache/<run_slug>/md/<pdf_stem>_investment_snapshot.pptx`
- `.cache/<run_slug>/md/<pdf_stem>_v2_report.pdf`
- `.cache/live_tests/<suite_id>/`
- `.cache/live_tests/<suite_id>/suite_summary.json`
- `.cache/live_tests/<suite_id>/suite_summary.md`
- `.cache/live_tests/<suite_id>/repair_backlog.md`
- `.cache/live_tests/<suite_id>/cases/<case_id>/`

`run_manifest.json` 当前重点记录：

- `run_root_dir`
- `run_cache_dir`
- `analysis_source_dir`
- `analysis_source_paths`
- `page_index_file_path`
- `document_metadata_file_path`
- `diligence_questions_path`
- `investment_thesis_path`
- `final_report_markdown_path`
- `pitch_material_markdown_path`
- `investment_snapshot_ppt_path`
- `final_report_pdf_path`
- `console_log_file_path`
- `crew_log_paths`
- `failed_stage`、`failed_crew`、`error_message`

## 环境变量

必须：

- `OPENROUTER_API_KEY`

通常需要：

- `SERPER_API_KEY`

按场景需要：

- `TUSHARE_TOKEN`
- `PDF_INDEX_MAX_CONCURRENCY`
- `PDF_INDEX_RETRY_LIMIT`

## 安装

```bash
uv sync
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

## 测试

运行全部测试：

```bash
uv run pytest -q
```

仅运行仓库测试目录：

```bash
uv run pytest -q test_src
```

导入级 smoke test：

```bash
uv run python -c "from automated_research_report_generator.flow.research_flow import ResearchReportFlow; print('ok')"
```

Live API 分段测试入口：

```bash
uv run python -m automated_research_report_generator.testing.live_runner --suite full --pdf pdf/sehk26033003882_c.pdf
```

说明：

- live harness 会先跑 `uv run pytest -q test_src`，失败时后续 live case 记为 `blocked_precheck`
- 组件级 case 使用 `test_src/live_fixtures/` 作为最小上游输入，链路级 case 才串真实上游输出
- 每个 case 单独子进程执行，命中 loop guard 时会在对应 `cases/<case_id>/monitor/` 下落盘现场证据
- 整轮结果统一写到 `.cache/live_tests/<suite_id>/`

## 文档导航

- 项目续接说明：`PROJECT_HANDOFF.md`
- 当前主设计稿：`design_docs/delightful-imagining-hamming.md`
- 仓库级工作说明：`AGENTS.md`
