from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crewai.flow.flow import Flow, listen, router, start

from automated_research_report_generator.crews.business_crew.business_crew import BusinessCrew
from automated_research_report_generator.crews.due_diligence_crew.due_diligence_crew import (
    DueDiligenceCrew,
)
from automated_research_report_generator.crews.financial_crew.financial_crew import FinancialCrew
from automated_research_report_generator.crews.history_background_crew.history_background_crew import (
    HistoryBackgroundCrew,
)
from automated_research_report_generator.crews.industry_crew.industry_crew import IndustryCrew
from automated_research_report_generator.crews.investment_thesis_crew.investment_thesis_crew import (
    InvestmentThesisCrew,
)
from automated_research_report_generator.crews.operating_metrics_crew.operating_metrics_crew import (
    OperatingMetricsCrew,
)
from automated_research_report_generator.crews.peer_info_crew.peer_info_crew import PeerInfoCrew
from automated_research_report_generator.crews.risk_crew.risk_crew import RiskCrew
from automated_research_report_generator.crews.valuation_crew.valuation_crew import (
    ValuationCrew,
)
from automated_research_report_generator.crews.writeup_crew.writeup_crew import WriteupCrew
from automated_research_report_generator.flow.common import (
    DEFAULT_PDF_PATH,
    RUN_FLOW_LOG_FILE_NAME,
    activate_run_preprocess_log,
    append_text_log_line,
    build_run_directories,
    ensure_runtime_artifact_path_allowed,
    ensure_directory,
    normalize_path,
    read_text_if_exists,
    run_log_dir,
    utc_timestamp,
    write_run_debug_manifest,
)
from automated_research_report_generator.flow.document_metadata import (
    resolve_pdf_document_metadata_payload,
)
from automated_research_report_generator.flow.models import ResearchFlowState
from automated_research_report_generator.flow.pdf_indexing import (
    ensure_pdf_page_index,
    reset_pdf_preprocessing_runtime_state,
)
from automated_research_report_generator.tools.document_metadata_tools import (
    load_document_metadata,
    save_document_metadata,
)
from automated_research_report_generator.tools.pdf_page_tools import (
    activate_page_index_directory,
    set_pdf_context,
)

# 目的：把 v0.3 的最小 POC 主链固定为 prepare -> analysis -> valuation -> thesis -> writeup。
# 功能：统一管理运行目录、专题 source md、专题 pack、估值产物、thesis 产物、最终报告和调试清单。
# 实现逻辑：先准备 PDF 元数据与页索引，再顺序执行 7 个专题 crew、估值、thesis 与最终导出。
# 可调参数：阶段事件名、专题输出布局、checkpoint 编号和最终报告拼装顺序。
# 默认参数及原因：analysis 产物继续落到 `research/iter_XX`，原因是这样对现有目录结构改动最小。

ANALYSIS_STAGE_COMPLETED_EVENT = "analysis_stage_completed"
VALUATION_STAGE_COMPLETED_EVENT = "valuation_stage_completed"
THESIS_STAGE_COMPLETED_EVENT = "thesis_stage_completed"

STAGE_FAILURE_CHECKPOINT_CODES = {
    "analysis": "cp03_analysis_failed",
    "valuation": "cp04_valuation_failed",
    "thesis": "cp05_thesis_failed",
    "writeup": "cp06_writeup_failed",
}

ANALYSIS_PACK_LAYOUT = [
    {
        "crew_class_name": "HistoryBackgroundCrew",
        "topic_slug": "history_background",
        "source_prefix": "01_history_background",
        "output_file_name": "01_history_background_pack.md",
        "state_attr": "history_background_pack_path",
        "file_source_state_attr": "history_background_file_source_path",
        "search_source_state_attr": "history_background_search_source_path",
        "checkpoint_code": "cp03a_history_background_pack",
    },
    {
        "crew_class_name": "IndustryCrew",
        "topic_slug": "industry",
        "source_prefix": "02_industry",
        "output_file_name": "02_industry_pack.md",
        "state_attr": "industry_pack_path",
        "file_source_state_attr": "industry_file_source_path",
        "search_source_state_attr": "industry_search_source_path",
        "checkpoint_code": "cp03b_industry_pack",
    },
    {
        "crew_class_name": "BusinessCrew",
        "topic_slug": "business",
        "source_prefix": "03_business",
        "output_file_name": "03_business_pack.md",
        "state_attr": "business_pack_path",
        "file_source_state_attr": "business_file_source_path",
        "search_source_state_attr": "business_search_source_path",
        "checkpoint_code": "cp03c_business_pack",
    },
    {
        "crew_class_name": "PeerInfoCrew",
        "topic_slug": "peer_info",
        "source_prefix": "04_peer_info",
        "output_file_name": "04_peer_info_pack.md",
        "state_attr": "peer_info_pack_path",
        "file_source_state_attr": "peer_info_peer_list_source_path",
        "search_source_state_attr": "peer_info_peer_data_source_path",
        "file_source_output_file_name": "04_peer_info_peer_list.md",
        "search_source_output_file_name": "04_peer_info_peer_data.md",
        "file_source_label": "同行信息分析包 Peer List Source",
        "search_source_label": "同行信息分析包 Peer Data Source",
        "checkpoint_code": "cp03d_peer_info_pack",
    },
    {
        "crew_class_name": "FinancialCrew",
        "topic_slug": "finance",
        "source_prefix": "05_finance",
        "output_file_name": "05_finance_pack.md",
        "state_attr": "finance_pack_path",
        "file_source_state_attr": "finance_file_source_path",
        "search_source_state_attr": "finance_computed_metrics_path",
        "search_source_output_file_name": "05_finance_computed_metrics.md",
        "search_source_label": "财务分析包 Computed Metrics",
        "extra_output_state_attrs": {
            "finance_analysis_output_path": "finance_analysis_path",
        },
        "extra_output_file_names": {
            "finance_analysis_output_path": "05_finance_analysis.md",
        },
        "checkpoint_code": "cp03e_finance_pack",
    },
    {
        "crew_class_name": "OperatingMetricsCrew",
        "topic_slug": "operating_metrics",
        "source_prefix": "06_operating_metrics",
        "output_file_name": "06_operating_metrics_pack.md",
        "state_attr": "operating_metrics_pack_path",
        "file_source_state_attr": "operating_metrics_file_source_path",
        "search_source_state_attr": "operating_metrics_search_source_path",
        "extra_output_state_attrs": {
            "operating_metrics_analysis_output_path": "operating_metrics_analysis_path",
        },
        "extra_output_file_names": {
            "operating_metrics_analysis_output_path": "06_operating_metrics_analysis.md",
        },
        "checkpoint_code": "cp03f_operating_metrics_pack",
    },
    {
        "crew_class_name": "RiskCrew",
        "topic_slug": "risk",
        "source_prefix": "07_risk",
        "output_file_name": "07_risk_pack.md",
        "state_attr": "risk_pack_path",
        "file_source_state_attr": "risk_file_source_path",
        "search_source_state_attr": "risk_search_source_path",
        "checkpoint_code": "cp03g_risk_pack",
    },
]


class ResearchReportFlow(Flow[ResearchFlowState]):
    """
    目的：把 v0.3 的 PDF 预处理、analysis、valuation、thesis 和 writeup 串成一条稳定主流程。
    功能：统一管理运行目录、工具上下文、阶段执行顺序、checkpoint、manifest 和最终报告产物。
    实现逻辑：先建立 PDF 基础上下文，再按固定阶段顺序推进，并在关键节点把状态和产物路径写回 `ResearchFlowState`。
    可调参数：输入 PDF 路径、阶段输出目录、checkpoint 编号和最终报告章节顺序。
    默认参数及原因：默认不再保留 registry / gathering runtime，原因是当前版本目标是最小可跑通的 source-based POC。
    """

    @start()
    def prepare_evidence(self):
        """
        目的：为整次 Flow 建立最小且真实的运行上下文。
        功能：解析 PDF、生成元数据与页索引，并创建当前 run 的目录边界。
        实现逻辑：先校验 PDF 路径来源与存在性，再依次完成预处理、上下文注入和状态字段回写。
        可调参数：PDF 路径来自 `state.pdf_file_path` 或 `DEFAULT_PDF_PATH`。
        默认参数及原因：默认使用 `DEFAULT_PDF_PATH`，原因是本地直接运行时需要一个稳定入口。
        """

        pdf_path = Path(self.state.pdf_file_path or DEFAULT_PDF_PATH).expanduser().resolve()
        pdf_path = Path(
            ensure_runtime_artifact_path_allowed(
                pdf_path,
                label="pdf file path",
            )
        )
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")

        reset_pdf_preprocessing_runtime_state()
        metadata_payload = resolve_pdf_document_metadata_payload(str(pdf_path))
        run_paths = build_run_directories(metadata_payload.company_name)
        artifact_dir = Path(run_paths["cache_dir"])
        indexing_dir = Path(run_paths["run_root_dir"]) / "indexing"

        self.state.pdf_file_path = pdf_path.as_posix()
        self.state.run_slug = Path(run_paths["run_slug"]).name
        self.state.run_cache_dir = artifact_dir.as_posix()
        self.state.run_output_dir = artifact_dir.as_posix()
        self.state.final_report_markdown_path = (artifact_dir / f"{pdf_path.stem}_v2_report.md").as_posix()
        self.state.final_report_pdf_path = (artifact_dir / f"{pdf_path.stem}_v2_report.pdf").as_posix()

        activate_run_preprocess_log(self.state.run_slug)
        activate_page_index_directory(indexing_dir)
        document_metadata_path = Path(
            save_document_metadata(
                metadata_payload,
                indexing_dir / f"{pdf_path.stem}_document_metadata.json",
            )
        ).resolve()

        self._log_flow(f"prepare_evidence started | pdf_file_path={pdf_path.as_posix()}")
        page_index_path = ensure_pdf_page_index(str(pdf_path), company_name=metadata_payload.company_name)
        set_pdf_context(str(pdf_path), page_index_path)

        self.state.company_name = metadata_payload.company_name
        self.state.industry = metadata_payload.industry
        self.state.document_metadata_file_path = document_metadata_path.as_posix()
        self.state.page_index_file_path = page_index_path
        self._clear_run_outcome()
        self._write_manifest_from_state("prepared")
        self._write_checkpoint(
            "cp00_prepared",
            {
                "pdf_file_path": self.state.pdf_file_path,
                "company_name": self.state.company_name,
                "industry": self.state.industry,
                "document_metadata_file_path": self.state.document_metadata_file_path,
                "page_index_file_path": self.state.page_index_file_path,
            },
        )
        self._log_flow(
            "prepare_evidence completed | "
            f"run_slug={self.state.run_slug} | "
            f"company_name={self.state.company_name} | "
            f"industry={self.state.industry}"
        )
        return {"company_name": self.state.company_name, "industry": self.state.industry}

    @router(prepare_evidence)
    def run_analysis_phase(self):
        """
        目的：触发 v0.3 analysis 阶段。
        功能：顺序执行 7 个专题 crew，并在末尾生成尽调问题清单。
        实现逻辑：每个专题 crew 先并行生成两份 source md，再综合写出专题 pack。
        可调参数：当前无额外参数。
        默认参数及原因：analysis 产物继续写入 `research/iter_XX`，原因是这样对现有目录体系影响最小。
        """

        self._run_analysis_stage()
        return ANALYSIS_STAGE_COMPLETED_EVENT

    @router(ANALYSIS_STAGE_COMPLETED_EVENT)
    def run_valuation_crew(self):
        """
        目的：触发估值阶段。
        功能：基于 4 个专题 pack 和对应 7 份 source md 运行 valuation crew。
        实现逻辑：估值阶段保持单轮顺序执行，完成后直接进入 thesis。
        可调参数：当前无额外参数。
        默认参数及原因：默认不走外部 valuation QA gate，原因是当前估值链路的自校验保留在 crew 内部。
        """

        self._run_valuation_stage()
        return VALUATION_STAGE_COMPLETED_EVENT

    @router(VALUATION_STAGE_COMPLETED_EVENT)
    def run_investment_thesis_crew(self):
        """
        目的：触发 thesis 阶段。
        功能：顺序生成 bull / neutral / bear 三份立场稿，再综合输出最终投资逻辑。
        实现逻辑：先消费 analysis、valuation 和 diligence 产物，再把路径写回 state。
        可调参数：当前无额外参数。
        默认参数及原因：默认 thesis 继续只读 pack，不再额外读取 source md，原因是用户要求保持 pack-based。
        """

        self._run_thesis_stage()
        return THESIS_STAGE_COMPLETED_EVENT

    @listen(THESIS_STAGE_COMPLETED_EVENT)
    def publish_if_passed(self):
        """
        目的：在 thesis 阶段完成后生成最终报告。
        功能：先由 Flow 确定性拼装最终 Markdown，再调用 writeup crew 做非破坏性确认和 PDF 导出。
        实现逻辑：WriteupCrew 不再负责重写正文，只消费 Flow 已经拼装好的稳定稿件。
        可调参数：最终报告路径和 PDF 输出路径。
        默认参数及原因：默认复用 state 中已经确定的路径，原因是最终产物位置需要稳定可追踪。
        """

        self._log_flow("publish_if_passed started")
        try:
            self._write_final_report_markdown()
            self._prepare_tool_context()
            writeup_crew = self._configure_crew_log(
                WriteupCrew(),
                self._crew_log_path("writeup_crew"),
            )
            writeup_crew.crew().kickoff(
                inputs=self._base_inputs()
                | {
                    "final_report_markdown_path": self.state.final_report_markdown_path,
                    "final_report_pdf_path": self.state.final_report_pdf_path,
                }
            )
        except Exception as exc:
            self._record_stage_failure(
                stage="writeup",
                crew_name="writeup_crew",
                error=exc,
                checkpoint_payload={
                    "final_report_markdown_path": self.state.final_report_markdown_path,
                    "final_report_pdf_path": self.state.final_report_pdf_path,
                },
            )
            raise

        self._clear_run_outcome()
        self._write_manifest_from_state("completed")
        self._write_checkpoint(
            "cp06_writeup",
            {
                "final_report_markdown_path": self.state.final_report_markdown_path,
                "final_report_pdf_path": self.state.final_report_pdf_path,
            },
        )
        self._log_flow(
            "publish_if_passed completed | "
            f"final_report_markdown_path={self.state.final_report_markdown_path} | "
            f"final_report_pdf_path={self.state.final_report_pdf_path}"
        )
        return self.state.final_report_pdf_path

    def _run_analysis_stage(self) -> str:
        """
        目的：封装 v0.3 analysis 阶段的实际执行逻辑。
        功能：顺序生成 7 个专题 pack，并在阶段末尾输出尽调问题清单。
        实现逻辑：逐个实例化专题 crew，执行“source -> pack”主链；对带内部中间产物的专题额外登记分析产物路径，再汇总进入尽调阶段。
        可调参数：analysis 目录、pack 输出文件名、专题 crew 元数据和 diligence 输出路径。
        默认参数及原因：阶段目录继续使用 `research/iter_XX`，原因是这样对 README、日志和下游路径影响最小。
        """

        self._clear_run_outcome()
        self._prepare_tool_context()
        analysis_dir = self._stage_iteration_dir("analysis")
        source_dir = ensure_directory(analysis_dir / "sources")
        self.state.analysis_source_dir = source_dir.as_posix()
        self._log_flow(
            f"run_analysis_phase started | analysis_output_dir={analysis_dir.as_posix()}"
        )

        for spec in self._analysis_pack_specs():
            output_path = (analysis_dir / spec["output_file_name"]).as_posix()
            file_source_output_path = (
                source_dir / spec["file_source_output_file_name"]
            ).as_posix()
            has_search_source = bool(spec["search_source_output_file_name"])
            search_source_output_path = (
                (source_dir / spec["search_source_output_file_name"]).as_posix()
                if has_search_source
                else ""
            )
            extra_output_paths = {
                input_key: (analysis_dir / output_file_name).as_posix()
                for input_key, output_file_name in dict(
                    spec.get("extra_output_file_names", {})
                ).items()
            }
            analysis_crew = self._configure_crew_log(
                spec["crew_instance"],
                self._crew_log_path(spec["crew_name"]),
            )
            extra_inputs: dict[str, str] = {}
            if spec["topic_slug"] == "peer_info":
                extra_inputs = {
                    "industry_pack_text": self._read(self.state.industry_pack_path),
                    "business_pack_text": self._read(self.state.business_pack_path),
                    "peer_list_source_output_path": file_source_output_path,
                    "peer_data_source_output_path": search_source_output_path,
                }
            elif spec["topic_slug"] == "finance":
                extra_inputs = {
                    "peer_info_peer_data_source_text": self._read(
                        self.state.peer_info_peer_data_source_path
                    ),
                    "industry_pack_text": self._read(self.state.industry_pack_path),
                    "business_pack_text": self._read(self.state.business_pack_path),
                    "finance_computed_metrics_output_path": extra_output_paths.get(
                        "finance_computed_metrics_output_path", search_source_output_path
                    ),
                    "finance_analysis_output_path": extra_output_paths.get(
                        "finance_analysis_output_path", ""
                    ),
                }
            elif spec["topic_slug"] == "operating_metrics":
                extra_inputs = {
                    "industry_pack_text": self._read(self.state.industry_pack_path),
                    "business_pack_text": self._read(self.state.business_pack_path),
                    "peer_info_peer_list_source_text": self._read(
                        self.state.peer_info_peer_list_source_path
                    ),
                    "operating_metrics_analysis_output_path": extra_output_paths.get(
                        "operating_metrics_analysis_output_path", ""
                    ),
                }
            elif spec["topic_slug"] == "risk":
                extra_inputs = {
                    "history_background_pack_text": self._read(
                        self.state.history_background_pack_path
                    ),
                    "business_pack_text": self._read(self.state.business_pack_path),
                    "industry_pack_text": self._read(self.state.industry_pack_path),
                    "finance_pack_text": self._read(self.state.finance_pack_path),
                    "operating_metrics_pack_text": self._read(
                        self.state.operating_metrics_pack_path
                    ),
                }
            try:
                analysis_crew.crew().kickoff(
                    inputs=self._base_inputs()
                    | {
                        "owner_crew": spec["crew_name"],
                        "topic_slug": spec["topic_slug"],
                        "pack_title": spec["pack_title"],
                        "pack_output_path": output_path,
                    }
                    | (
                        extra_inputs
                        if spec["topic_slug"] == "peer_info"
                        else (
                            (
                                {
                                    "file_source_output_path": file_source_output_path,
                                    "search_source_output_path": search_source_output_path,
                                }
                                | extra_inputs
                            )
                            if spec["topic_slug"] in {
                                "finance",
                                "operating_metrics",
                                "risk",
                            }
                            else (
                                {
                                    "file_source_output_path": file_source_output_path,
                                    "search_source_output_path": search_source_output_path,
                                }
                                if has_search_source
                                else {
                                    "file_source_output_path": file_source_output_path,
                                }
                            )
                        )
                    )
                )
            except Exception as exc:
                checkpoint_payload = {
                    "pack_name": spec["pack_name"],
                    "pack_output_path": output_path,
                    "file_source_output_path": file_source_output_path,
                }
                if has_search_source:
                    checkpoint_payload["search_source_output_path"] = search_source_output_path
                checkpoint_payload.update(extra_output_paths)
                self._record_stage_failure(
                    stage="analysis",
                    crew_name=spec["crew_name"],
                    error=exc,
                    checkpoint_payload=checkpoint_payload,
                )
                raise

            setattr(self.state, spec["state_attr"], output_path)
            setattr(self.state, spec["file_source_state_attr"], file_source_output_path)
            if spec["search_source_state_attr"]:
                setattr(self.state, spec["search_source_state_attr"], search_source_output_path)
            for input_key, state_attr in dict(
                spec.get("extra_output_state_attrs", {})
            ).items():
                setattr(self.state, state_attr, extra_output_paths.get(input_key, ""))
            checkpoint_payload = {
                "pack_name": spec["pack_name"],
                "owner_crew": spec["crew_name"],
                "topic_slug": spec["topic_slug"],
                "pack_output_path": output_path,
                "file_source_output_path": file_source_output_path,
            }
            if has_search_source:
                checkpoint_payload["search_source_output_path"] = search_source_output_path
            checkpoint_payload.update(extra_output_paths)
            self._write_checkpoint(
                spec["checkpoint_code"],
                checkpoint_payload,
            )
            log_parts = [
                "run_analysis_phase pack completed",
                f"pack_name={spec['pack_name']}",
                f"file_source_output_path={file_source_output_path}",
            ]
            if has_search_source:
                log_parts.append(f"search_source_output_path={search_source_output_path}")
            log_parts.append(f"pack_output_path={output_path}")
            self._log_flow(" | ".join(log_parts))

        diligence_output_path = (analysis_dir / "08_diligence_questions.md").as_posix()
        diligence_crew = self._configure_crew_log(
            DueDiligenceCrew(),
            self._crew_log_path("due_diligence_crew"),
        )
        try:
            diligence_crew.crew().kickoff(
                inputs=self._base_inputs()
                | self._due_diligence_inputs()
                | {"diligence_output_path": diligence_output_path}
            )
        except Exception as exc:
            self._record_stage_failure(
                stage="analysis",
                crew_name="due_diligence_crew",
                error=exc,
                checkpoint_payload={
                    "diligence_output_path": diligence_output_path,
                },
            )
            raise

        self.state.diligence_questions_path = diligence_output_path
        self._write_manifest_from_state("analysis_completed")
        self._write_checkpoint(
            "cp03h_diligence_questions",
            {
                "analysis_source_dir": self.state.analysis_source_dir,
                "diligence_questions_path": self.state.diligence_questions_path,
            },
        )
        self._log_flow(
            "run_analysis_phase completed | "
            f"analysis_source_dir={self.state.analysis_source_dir} | "
            f"diligence_questions_path={self.state.diligence_questions_path}"
        )
        return analysis_dir.as_posix()

    def _run_valuation_stage(self) -> str:
        """
        目的：封装估值阶段的实际执行逻辑。
        功能：运行 valuation crew，并登记三份估值分析包。
        实现逻辑：创建估值目录、注入 4 个专题 pack 与 7 份 source md，再把输出路径写回 state。
        可调参数：估值目录和下游需要的文本输入。
        默认参数及原因：产物默认写入 `valuation/iter_XX`，原因是阶段隔离更清晰。
        """

        valuation_dir = self._stage_iteration_dir("valuation")
        self._log_flow(
            f"run_valuation_crew started | valuation_output_dir={valuation_dir.as_posix()}"
        )
        self._prepare_tool_context()
        valuation_crew = self._configure_crew_log(
            ValuationCrew(),
            self._crew_log_path("valuation_crew"),
        )
        try:
            valuation_crew.crew().kickoff(
                inputs=self._base_inputs()
                | {
                    "valuation_output_dir": valuation_dir.as_posix(),
                    "peer_info_pack_text": self._read(self.state.peer_info_pack_path),
                    "finance_pack_text": self._read(self.state.finance_pack_path),
                    "operating_metrics_pack_text": self._read(self.state.operating_metrics_pack_path),
                    "risk_pack_text": self._read(self.state.risk_pack_path),
                    "peer_info_peer_data_source_text": self._read(
                        self.state.peer_info_peer_data_source_path
                    ),
                    "risk_search_source_text": self._read(self.state.risk_search_source_path),
                }
            )
        except Exception as exc:
            self._record_stage_failure(
                stage="valuation",
                crew_name="valuation_crew",
                error=exc,
                checkpoint_payload={
                    "valuation_output_dir": valuation_dir.as_posix(),
                },
            )
            raise

        self.state.peers_pack_path = (valuation_dir / "01_peers_pack.md").as_posix()
        self.state.intrinsic_value_pack_path = (valuation_dir / "02_intrinsic_value_pack.md").as_posix()
        self.state.valuation_pack_path = (valuation_dir / "03_valuation_pack.md").as_posix()

        self._write_manifest_from_state("valuation_completed")
        self._write_checkpoint(
            "cp04_valuation",
            {
                "peers_pack_path": self.state.peers_pack_path,
                "intrinsic_value_pack_path": self.state.intrinsic_value_pack_path,
                "valuation_pack_path": self.state.valuation_pack_path,
            },
        )
        self._log_flow(
            "run_valuation_crew completed | "
            f"peers_pack_path={self.state.peers_pack_path} | "
            f"intrinsic_value_pack_path={self.state.intrinsic_value_pack_path} | "
            f"valuation_pack_path={self.state.valuation_pack_path}"
        )
        return self.state.valuation_pack_path

    def _run_thesis_stage(self) -> str:
        """
        目的：封装 thesis 阶段的实际执行逻辑。
        功能：运行 bull / neutral / bear / synthesizer 四个任务，并登记最终投资逻辑产物。
        实现逻辑：把 analysis、valuation 和 diligence 文本一次性注入 thesis crew，再把输出路径写回 state。
        可调参数：thesis 目录和各类上游文本输入。
        默认参数及原因：默认保留三份立场稿和一份最终稿，原因是需要保留多视角辩论痕迹。
        """

        thesis_dir = self._stage_iteration_dir("thesis")
        self._log_flow(
            f"run_investment_thesis_crew started | thesis_output_dir={thesis_dir.as_posix()}"
        )
        self._prepare_tool_context()
        thesis_crew = self._configure_crew_log(
            InvestmentThesisCrew(),
            self._crew_log_path("investment_thesis_crew"),
        )
        try:
            thesis_crew.crew().kickoff(
                inputs=self._base_inputs()
                | {
                    "thesis_output_dir": thesis_dir.as_posix(),
                    "history_background_pack_text": self._read(self.state.history_background_pack_path),
                    "industry_pack_text": self._read(self.state.industry_pack_path),
                    "business_pack_text": self._read(self.state.business_pack_path),
                    "peer_info_pack_text": self._read(self.state.peer_info_pack_path),
                    "finance_pack_text": self._read(self.state.finance_pack_path),
                    "operating_metrics_pack_text": self._read(self.state.operating_metrics_pack_path),
                    "risk_pack_text": self._read(self.state.risk_pack_path),
                    "peers_pack_text": self._read(self.state.peers_pack_path),
                    "intrinsic_value_pack_text": self._read(self.state.intrinsic_value_pack_path),
                    "valuation_pack_text": self._read(self.state.valuation_pack_path),
                    "diligence_questions_text": self._read(self.state.diligence_questions_path),
                }
            )
        except Exception as exc:
            self._record_stage_failure(
                stage="thesis",
                crew_name="investment_thesis_crew",
                error=exc,
                checkpoint_payload={
                    "thesis_output_dir": thesis_dir.as_posix(),
                },
            )
            raise

        self.state.bull_thesis_path = (thesis_dir / "01_bull_thesis.md").as_posix()
        self.state.neutral_thesis_path = (thesis_dir / "02_neutral_thesis.md").as_posix()
        self.state.bear_thesis_path = (thesis_dir / "03_bear_thesis.md").as_posix()
        self.state.investment_thesis_path = (thesis_dir / "04_investment_thesis.md").as_posix()

        self._write_manifest_from_state("thesis_completed")
        self._write_checkpoint(
            "cp05_thesis",
            {
                "bull_thesis_path": self.state.bull_thesis_path,
                "neutral_thesis_path": self.state.neutral_thesis_path,
                "bear_thesis_path": self.state.bear_thesis_path,
                "investment_thesis_path": self.state.investment_thesis_path,
            },
        )
        self._log_flow(
            "run_investment_thesis_crew completed | "
            f"investment_thesis_path={self.state.investment_thesis_path}"
        )
        return self.state.investment_thesis_path

    def _analysis_pack_specs(self) -> list[dict[str, object]]:
        """
        目的：把 analysis 阶段真正需要的专题元数据集中组装出来。
        功能：读取 7 个专题 crew 的公开 pack 元数据，并补齐 Flow 需要的 source/path 配置。
        实现逻辑：静态布局只保留顺序和路径；专题文案与 pack 标识直接从 crew 实例读取。
        可调参数：当前无显式参数。
        默认参数及原因：默认逐个实例化专题 crew，原因是 pack 元数据已经收口在各自目录内，直接读取最稳妥。
        """

        specs: list[dict[str, object]] = []
        for layout in ANALYSIS_PACK_LAYOUT:
            crew_class = globals()[layout["crew_class_name"]]
            crew_instance = crew_class()
            specs.append(
                {
                    "crew_instance": crew_instance,
                    "topic_slug": layout["topic_slug"],
                    "source_prefix": layout["source_prefix"],
                    "output_file_name": layout["output_file_name"],
                    "file_source_output_file_name": layout.get(
                        "file_source_output_file_name",
                        f"{layout['source_prefix']}_file_source.md",
                    ),
                    "search_source_output_file_name": layout.get(
                        "search_source_output_file_name",
                        f"{layout['source_prefix']}_search_source.md"
                        if layout.get("search_source_state_attr") is not None
                        else None,
                    ),
                    "state_attr": layout["state_attr"],
                    "file_source_state_attr": layout["file_source_state_attr"],
                    "search_source_state_attr": layout.get("search_source_state_attr"),
                    "file_source_label": layout.get(
                        "file_source_label",
                        f"{crew_instance.pack_title} File Source",
                    ),
                    "search_source_label": layout.get(
                        "search_source_label",
                        f"{crew_instance.pack_title} Search Source"
                        if layout.get("search_source_state_attr") is not None
                        else None,
                    ),
                    "extra_output_state_attrs": layout.get("extra_output_state_attrs", {}),
                    "extra_output_file_names": layout.get("extra_output_file_names", {}),
                    "checkpoint_code": layout["checkpoint_code"],
                    "crew_name": crew_instance.crew_name,
                    "pack_name": crew_instance.pack_name,
                    "pack_title": crew_instance.pack_title,
                }
            )
        return specs

    def _analysis_pack_text_inputs(self) -> dict[str, str]:
        """
        目的：统一收口 analysis 阶段下游要消费的 7 个专题 pack 文本。
        功能：把各专题 pack 路径读取成 diligence 和 thesis 可直接消费的输入字典。
        实现逻辑：显式读取 7 个固定 state 字段，避免下游自己拼字段名。
        可调参数：当前无显式参数。
        默认参数及原因：默认缺文件时返回空串，原因是运行失败时也要能稳定记录 manifest 和错误上下文。
        """

        return {
            "history_background_pack_text": self._read(self.state.history_background_pack_path),
            "industry_pack_text": self._read(self.state.industry_pack_path),
            "business_pack_text": self._read(self.state.business_pack_path),
            "peer_info_pack_text": self._read(self.state.peer_info_pack_path),
            "finance_pack_text": self._read(self.state.finance_pack_path),
            "operating_metrics_pack_text": self._read(self.state.operating_metrics_pack_path),
            "risk_pack_text": self._read(self.state.risk_pack_path),
        }

    def _analysis_source_text_inputs(self) -> dict[str, str]:
        """
        目的：统一收口 analysis 阶段下游要消费的 13 份 source md 文本。
        功能：把各专题 file/search source 路径读取成 valuation 等仍需 source 的下游可直接消费的输入字典。
        实现逻辑：显式读取当前真实存在的 source 字段，避免下游自己推断路径命名，同时把 due diligence 的极简输入与这里分离。
        可调参数：当前无显式参数。
        默认参数及原因：默认缺文件时返回空串，原因是失败时仍需要保留稳定的错误上下文。
        """

        return {
            "history_background_file_source_text": self._read(self.state.history_background_file_source_path),
            "history_background_search_source_text": self._read(self.state.history_background_search_source_path),
            "industry_file_source_text": self._read(self.state.industry_file_source_path),
            "industry_search_source_text": self._read(self.state.industry_search_source_path),
            "business_file_source_text": self._read(self.state.business_file_source_path),
            "business_search_source_text": self._read(self.state.business_search_source_path),
            "peer_info_peer_list_source_text": self._read(self.state.peer_info_peer_list_source_path),
            "peer_info_peer_data_source_text": self._read(self.state.peer_info_peer_data_source_path),
            "finance_file_source_text": self._read(self.state.finance_file_source_path),
            "finance_computed_metrics_text": self._read(self.state.finance_computed_metrics_path),
            "finance_analysis_text": self._read(self.state.finance_analysis_path),
            "operating_metrics_file_source_text": self._read(self.state.operating_metrics_file_source_path),
            "operating_metrics_search_source_text": self._read(self.state.operating_metrics_search_source_path),
            "risk_file_source_text": self._read(self.state.risk_file_source_path),
            "risk_search_source_text": self._read(self.state.risk_search_source_path),
        }

    def _due_diligence_inputs(self) -> dict[str, str]:
        """
        目的：为尽调问题生成阶段提供最小且高价值的文本输入集合。
        功能：只注入 7 个专题 pack 和 1 份跨专题风险分析中间产物，避免 due diligence 重复消费大量原始 source。
        实现逻辑：复用统一的 pack 文本输入，再单独补上 `risk_search_source_text`，明确与 valuation 使用的 source 输入边界分离。
        可调参数：当前无显式参数。
        默认参数及原因：默认不再传入其他 source 文本，原因是尽调阶段应围绕已沉淀结论和跨专题风险传导来提问，而不是回到原始材料重做研究。
        """

        return self._analysis_pack_text_inputs() | {
            "risk_search_source_text": self._read(self.state.risk_search_source_path),
        }

    def _analysis_source_paths(self) -> dict[str, str]:
        """
        目的：统一收口 run manifest 和最终附录要用到的 13 份 source md 路径。
        功能：返回按专题和 source 类型展开的路径字典。
        实现逻辑：直接从 state 读取显式路径字段，不做推断式拼接。
        可调参数：当前无显式参数。
        默认参数及原因：默认保留空串值，原因是 manifest 需要反映真实缺失情况而不是静默跳过。
        """

        return {
            "history_background_file_source_path": self.state.history_background_file_source_path,
            "history_background_search_source_path": self.state.history_background_search_source_path,
            "industry_file_source_path": self.state.industry_file_source_path,
            "industry_search_source_path": self.state.industry_search_source_path,
            "business_file_source_path": self.state.business_file_source_path,
            "business_search_source_path": self.state.business_search_source_path,
            "peer_info_peer_list_source_path": self.state.peer_info_peer_list_source_path,
            "peer_info_peer_data_source_path": self.state.peer_info_peer_data_source_path,
            "finance_file_source_path": self.state.finance_file_source_path,
            "finance_computed_metrics_path": self.state.finance_computed_metrics_path,
            "finance_analysis_path": self.state.finance_analysis_path,
            "operating_metrics_file_source_path": self.state.operating_metrics_file_source_path,
            "operating_metrics_search_source_path": self.state.operating_metrics_search_source_path,
            "risk_file_source_path": self.state.risk_file_source_path,
            "risk_search_source_path": self.state.risk_search_source_path,
        }

    def _demote_markdown_headings(self, text: str, *, level_shift: int = 2) -> str:
        """
        目的：把上游 Markdown 安全嵌入最终报告，而不破坏其正文内容。
        功能：仅对标题层级做整体下调，避免上游 pack 或 source 的 `#` 冲掉最终报告骨架。
        实现逻辑：逐行扫描并跳过代码块；遇到 ATX 标题时统一增加层级，正文、表格和列表保持原样。
        可调参数：`text` 和 `level_shift`。
        默认参数及原因：默认下调 2 级，原因是最终报告已占用 `#` 和 `##` 两层主骨架。
        """

        lines: list[str] = []
        in_fenced_block = False
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("```"):
                in_fenced_block = not in_fenced_block
                lines.append(line.rstrip())
                continue
            if not in_fenced_block and stripped.startswith("#"):
                prefix_length = len(stripped) - len(stripped.lstrip("#"))
                if 1 <= prefix_length <= 6:
                    heading_level = min(prefix_length + level_shift, 6)
                    heading_text = stripped[prefix_length:].lstrip()
                    leading_whitespace = line[: len(line) - len(stripped)]
                    if heading_text:
                        lines.append(f"{leading_whitespace}{'#' * heading_level} {heading_text}")
                    else:
                        lines.append(f"{leading_whitespace}{'#' * heading_level}")
                    continue
            lines.append(line.rstrip())
        return "\n".join(lines).strip()

    def _render_report_source_markdown(self, *, label: str, text: str, source_path: str) -> str:
        """
        目的：把单份 source md 转成可直接嵌入最终报告附录的 Markdown 片段。
        功能：有正文时仅做标题降级；缺失时输出明确占位，避免最终报告静默吞掉整段材料。
        实现逻辑：先判断文本是否为空，再分别走“降级嵌入”或“缺失占位”两条最小分支。
        可调参数：材料标签、正文文本和源文件路径。
        默认参数及原因：缺失时保留期望路径，原因是排查上游断链时需要立即知道缺的是哪一份文件。
        """

        normalized_text = text.strip()
        if normalized_text:
            return self._demote_markdown_headings(normalized_text)
        expected_path = source_path or "未设置"
        return f"> 上游材料缺失：{label}。期望路径：{expected_path}"

    def _build_final_report_markdown(self) -> str:
        """
        目的：用确定性方式生成最终报告 Markdown，避免 writeup 阶段再次改写正文。
        功能：按固定章节顺序拼接 thesis、尽调问题、7 个分析包、3 个估值包和 13 份 source md 附录。
        实现逻辑：Flow 只做章节骨架拼装和标题降级，不改写任何上游正文。
        可调参数：各阶段产物路径和 source md 路径。
        默认参数及原因：缺失产物时写明占位说明，原因是最终报告不能靠静默省略掩盖链路断点。
        """

        report_sections: list[tuple[str, list[tuple[str | None, str, str, str]]]] = [
            (
                "1. 投资逻辑",
                [(None, "投资逻辑", self._read(self.state.investment_thesis_path), self.state.investment_thesis_path)],
            ),
            (
                "2. 尽调问题",
                [(None, "尽调问题", self._read(self.state.diligence_questions_path), self.state.diligence_questions_path)],
            ),
            (
                "3. 公司历史与背景",
                [(None, "历史与背景分析包", self._read(self.state.history_background_pack_path), self.state.history_background_pack_path)],
            ),
            (
                "4. 行业分析",
                [(None, "行业分析包", self._read(self.state.industry_pack_path), self.state.industry_pack_path)],
            ),
            (
                "5. 业务分析",
                [(None, "业务分析包", self._read(self.state.business_pack_path), self.state.business_pack_path)],
            ),
            (
                "6. 经营指标分析",
                [(None, "经营指标分析包", self._read(self.state.operating_metrics_pack_path), self.state.operating_metrics_pack_path)],
            ),
            (
                "7. 财务分析",
                [(None, "财务分析包", self._read(self.state.finance_pack_path), self.state.finance_pack_path)],
            ),
            (
                "8. 风险分析",
                [(None, "风险分析包", self._read(self.state.risk_pack_path), self.state.risk_pack_path)],
            ),
            (
                "9. 同行情况",
                [(None, "同行信息分析包", self._read(self.state.peer_info_pack_path), self.state.peer_info_pack_path)],
            ),
            (
                "10. 综合估值",
                [
                    ("### 10.1 可比估值分析包", "可比估值分析包", self._read(self.state.peers_pack_path), self.state.peers_pack_path),
                    (
                        "### 10.2 内在价值分析包",
                        "内在价值分析包",
                        self._read(self.state.intrinsic_value_pack_path),
                        self.state.intrinsic_value_pack_path,
                    ),
                    (
                        "### 10.3 综合估值分析包",
                        "综合估值分析包",
                        self._read(self.state.valuation_pack_path),
                        self.state.valuation_pack_path,
                    ),
                ],
            ),
        ]

        lines = [f"# {self.state.company_name} 研究报告"]
        for section_title, blocks in report_sections:
            lines.append("")
            lines.append(f"## {section_title}")
            for subheading, label, text, source_path in blocks:
                block_markdown = self._render_report_source_markdown(
                    label=label,
                    text=text,
                    source_path=source_path,
                )
                if subheading:
                    lines.append("")
                    lines.append(subheading)
                lines.append("")
                lines.append(block_markdown)

        lines.append("")
        lines.append("## 11. 附录：专题 Source 全文")
        for spec in self._analysis_pack_specs():
            file_path = getattr(self.state, spec["file_source_state_attr"])
            lines.append("")
            lines.append(f"### {spec['file_source_label']}")
            lines.append("")
            lines.append(
                self._render_report_source_markdown(
                    label=str(spec["file_source_label"]),
                    text=self._read(file_path),
                    source_path=file_path,
                )
            )
            if spec["search_source_state_attr"] and spec["search_source_label"]:
                search_path = getattr(self.state, spec["search_source_state_attr"])
                lines.append("")
                lines.append(f"### {spec['search_source_label']}")
                lines.append("")
                lines.append(
                    self._render_report_source_markdown(
                        label=str(spec["search_source_label"]),
                        text=self._read(search_path),
                        source_path=search_path,
                    )
                )

        return "\n".join(lines).strip() + "\n"

    def _write_final_report_markdown(self) -> str:
        """
        目的：把最终报告 Markdown 落盘到稳定路径。
        功能：构建最终 Markdown 并写入 `state.final_report_markdown_path`。
        实现逻辑：先确保父目录存在，再按 UTF-8 写入。
        可调参数：最终报告路径。
        默认参数及原因：默认按 UTF-8 写盘，原因是报告和 source md 都包含中英文内容。
        """

        report_path = Path(self.state.final_report_markdown_path).expanduser().resolve()
        ensure_directory(report_path.parent)
        report_text = self._build_final_report_markdown()
        report_path.write_text(report_text, encoding="utf-8")
        self.state.final_report_markdown_path = report_path.as_posix()
        return self.state.final_report_markdown_path

    def _prepare_tool_context(self) -> None:
        """
        目的：在进入 crew 前统一准备 PDF 工具上下文。
        功能：让所有需要 PDF 的任务共享同一份当前文档和页索引。
        实现逻辑：仅向 PDF 工具层注入当前 PDF 路径与页索引路径。
        可调参数：当前 PDF 路径和页索引路径。
        默认参数及原因：默认每次阶段前重设上下文，原因是这样更不容易串缓存。
        """

        if self.state.pdf_file_path and self.state.page_index_file_path:
            set_pdf_context(
                ensure_runtime_artifact_path_allowed(
                    self.state.pdf_file_path,
                    label="pdf file path",
                ),
                ensure_runtime_artifact_path_allowed(
                    self.state.page_index_file_path,
                    label="page index file path",
                ),
            )

    def _configure_crew_log(self, crew_instance: Any, log_path: str) -> Any:
        """
        目的：把 run 级日志路径注入到具体 crew 实例。
        功能：统一设置 `output_log_file_path`，并确保日志父目录存在。
        实现逻辑：如果 crew 暴露了 `output_log_file_path` 属性，就直接覆盖成当前 run 的日志文件。
        可调参数：crew 实例和目标日志路径。
        默认参数及原因：默认按 run 隔离日志，原因是排查单次运行问题时更直接。
        """

        if hasattr(crew_instance, "output_log_file_path"):
            crew_instance.output_log_file_path = log_path
        ensure_directory(Path(log_path).expanduser().resolve().parent)
        return crew_instance

    def _crew_log_path(self, crew_name: str) -> str:
        """
        目的：统一生成单个 crew 在当前 run 下的日志路径。
        功能：返回 `.cache/<run_slug>/logs/<crew_name>.txt` 的标准化路径。
        实现逻辑：复用 run 级日志目录 helper，再拼接固定文件名。
        可调参数：`crew_name`。
        默认参数及原因：默认使用 `.txt`，原因是当前项目的 flow/crew 日志都按文本方式排查。
        """

        if not self.state.run_slug:
            return normalize_path(Path(self.state.run_cache_dir).parent / "logs" / f"{crew_name}.txt")
        return normalize_path(run_log_dir(self.state.run_slug) / f"{crew_name}.txt")

    def _flow_log_path(self) -> str:
        """
        目的：统一生成当前 run 的 flow 日志路径。
        功能：返回 `.cache/<run_slug>/logs/flow.txt` 的标准化路径。
        实现逻辑：优先使用 `run_slug`，缺失时退回到 `run_cache_dir` 推导路径。
        可调参数：当前无显式参数。
        默认参数及原因：默认写入固定文件名 `flow.txt`，原因是便于快速定位主流程日志。
        """

        if not self.state.run_slug:
            return normalize_path(Path(self.state.run_cache_dir).parent / "logs" / RUN_FLOW_LOG_FILE_NAME)
        return normalize_path(run_log_dir(self.state.run_slug) / RUN_FLOW_LOG_FILE_NAME)

    def _log_flow(self, message: str) -> str:
        """
        目的：给 Flow 主链提供统一的文本日志入口。
        功能：把单行消息追加到当前 run 的 `flow.txt`。
        实现逻辑：统一调用 `append_text_log_line()`，并返回标准化后的日志路径。
        可调参数：`message`。
        默认参数及原因：默认一行一条日志，原因是 grep 和人工排查都更直接。
        """

        return append_text_log_line(self._flow_log_path(), message)

    def _stage_iteration_dir(self, stage_name: str) -> Path:
        """
        目的：统一生成各阶段的迭代目录。
        功能：返回 `research/iter_01`、`valuation/iter_01` 或 `thesis/iter_01`。
        实现逻辑：按阶段名映射到固定根目录，再创建 `iter_01`。
        可调参数：`stage_name`。
        默认参数及原因：默认固定使用 `iter_01`，原因是当前最小 POC 不引入多轮回退迭代逻辑。
        """

        root_name = "research" if stage_name == "analysis" else stage_name
        stage_dir = ensure_directory(Path(self.state.run_cache_dir).expanduser().resolve() / root_name / "iter_01")
        return stage_dir

    def _read(self, path: str) -> str:
        """
        目的：统一读取上游产物文本。
        功能：文件存在时返回 UTF-8 正文，不存在时返回空串。
        实现逻辑：直接复用 `read_text_if_exists()`。
        可调参数：`path`。
        默认参数及原因：默认缺文件时返回空串，原因是失败态下仍要能写 manifest 和 checkpoint。
        """

        return read_text_if_exists(
            ensure_runtime_artifact_path_allowed(
                path,
                label="analysis artifact path",
            )
        )

    def _base_inputs(self) -> dict[str, str]:
        """
        目的：统一收口多个 crew 都需要的公共输入。
        功能：提供公司名、行业、PDF 路径、页索引路径、元数据路径和 analysis source 目录。
        实现逻辑：直接从 state 读取稳定公共字段。
        可调参数：当前无显式参数。
        默认参数及原因：默认只暴露稳定公共字段，原因是不同 crew 的业务输入由各阶段单独补充。
        """

        base_inputs = {
            "company_name": self.state.company_name,
            "industry": self.state.industry,
            "pdf_file_path": ensure_runtime_artifact_path_allowed(
                self.state.pdf_file_path,
                label="pdf file path",
            ),
            "page_index_file_path": ensure_runtime_artifact_path_allowed(
                self.state.page_index_file_path,
                label="page index file path",
            ),
            "document_metadata_file_path": ensure_runtime_artifact_path_allowed(
                self.state.document_metadata_file_path,
                label="document metadata file path",
            ),
            "analysis_source_dir": ensure_runtime_artifact_path_allowed(
                self.state.analysis_source_dir,
                label="analysis source directory",
            ),
        }
        base_inputs.update(self._period_placeholder_inputs())
        return base_inputs

    def _period_placeholder_inputs(self) -> dict[str, str]:
        """
        目的：把 document metadata 里的期间占位符映射成 CrewAI 可插值的输入键。
        功能：读取 metadata 中的 `periods`，把 `{FY-3}` 这类占位转成 `FY-3` 键供 tasks.yaml 使用。
        实现逻辑：优先读取当前 run 的 metadata 文件；只保留形如 `{...}` 的字符串键，并去掉外层花括号。
        可调参数：当前无显式参数，数据来源固定为 `state.document_metadata_file_path`。
        默认参数及原因：缺少 metadata 或 periods 非字典时返回空字典，原因是不要让辅助输入构造反向阻塞主流程。
        """

        metadata_path = ensure_runtime_artifact_path_allowed(
            (self.state.document_metadata_file_path or "").strip(),
            label="document metadata file path",
        )
        if not metadata_path:
            return {}

        try:
            metadata = load_document_metadata(metadata_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

        raw_periods = metadata.get("periods")
        if not isinstance(raw_periods, dict):
            return {}

        period_inputs: dict[str, str] = {}
        for raw_key, raw_value in raw_periods.items():
            if not isinstance(raw_key, str):
                continue
            if not (raw_key.startswith("{") and raw_key.endswith("}")):
                continue
            period_inputs[raw_key[1:-1]] = raw_value if isinstance(raw_value, str) else ""
        return period_inputs

    def _clear_run_outcome(self) -> None:
        """
        目的：在新阶段开始前清理上一阶段残留的失败状态。
        功能：重置失败阶段、失败 crew、错误消息、阻塞包和阻塞原因。
        实现逻辑：直接把失败相关 state 字段置空或清空列表。
        可调参数：当前无显式参数。
        默认参数及原因：默认每次关键阶段前都清理，原因是 manifest 应反映当前最新状态。
        """

        self.state.failed_stage = ""
        self.state.failed_crew = ""
        self.state.error_message = ""
        self.state.blocked_packs = []
        self.state.block_reason = ""

    def _write_checkpoint(self, checkpoint_code: str, payload: dict[str, Any]) -> str:
        """
        目的：为每个关键节点落一份可追踪 checkpoint。
        功能：把状态摘要和阶段产物路径写入 `md/checkpoints/`。
        实现逻辑：统一写成 UTF-8 JSON，文件名使用 `checkpoint_code`。
        可调参数：checkpoint 编号和附加 payload。
        默认参数及原因：默认写 JSON，原因是这类快照更适合程序和人工同时消费。
        """

        checkpoint_dir = ensure_directory(
            Path(self.state.run_cache_dir).expanduser().resolve() / "checkpoints"
        )
        checkpoint_path = checkpoint_dir / f"{checkpoint_code}.json"
        serialized = json.dumps(
            {
                "checkpoint_code": checkpoint_code,
                "generated_at": utc_timestamp(),
                "run_slug": self.state.run_slug,
                "company_name": self.state.company_name,
                "industry": self.state.industry,
                "payload": payload,
            },
            ensure_ascii=False,
            indent=2,
        )
        checkpoint_path.write_text(serialized, encoding="utf-8")
        return checkpoint_path.as_posix()

    def _record_stage_failure(
        self,
        *,
        stage: str,
        crew_name: str,
        error: Exception,
        checkpoint_payload: dict[str, Any],
    ) -> None:
        """
        目的：统一记录关键阶段失败。
        功能：把失败信息写回 state、manifest、checkpoint 和 flow 日志。
        实现逻辑：先更新 state，再写 manifest 和失败 checkpoint，最后落日志。
        可调参数：失败阶段、crew 名、异常对象和附加 payload。
        默认参数及原因：默认保留原始异常文本，原因是排查链路问题时需要最直接的错误上下文。
        """

        self.state.failed_stage = stage
        self.state.failed_crew = crew_name
        self.state.error_message = str(error)
        self._write_manifest_from_state("failed")
        self._write_checkpoint(
            STAGE_FAILURE_CHECKPOINT_CODES[stage],
            checkpoint_payload
            | {
                "failed_stage": stage,
                "failed_crew": crew_name,
                "error_message": str(error),
            },
        )
        self._log_flow(
            "stage failed | "
            f"stage={stage} | "
            f"crew_name={crew_name} | "
            f"error_message={str(error)}"
        )

    def _write_manifest_from_state(self, status: str) -> str:
        """
        目的：把当前 Flow 状态写入 run manifest。
        功能：统一记录 PDF、目录、source md、pack、thesis 和最终报告等关键路径。
        实现逻辑：调用 `write_run_debug_manifest()`，再把返回路径写回 state。
        可调参数：`status`。
        默认参数及原因：默认每个关键阶段后都刷新 manifest，原因是运行中断时也能从最新状态定位问题。
        """

        manifest_path = write_run_debug_manifest(
            run_slug=self.state.run_slug,
            status=status,
            pdf_file_path=self.state.pdf_file_path,
            run_cache_dir=self.state.run_cache_dir,
            analysis_source_dir=self.state.analysis_source_dir,
            analysis_source_paths=self._analysis_source_paths(),
            page_index_file_path=self.state.page_index_file_path,
            document_metadata_file_path=self.state.document_metadata_file_path,
            investment_thesis_path=self.state.investment_thesis_path,
            diligence_questions_path=self.state.diligence_questions_path,
            final_report_markdown_path=self.state.final_report_markdown_path,
            final_report_pdf_path=self.state.final_report_pdf_path,
            failed_stage=self.state.failed_stage,
            failed_crew=self.state.failed_crew,
            error_message=self.state.error_message,
            blocked_packs=self.state.blocked_packs,
            block_reason=self.state.block_reason,
        )
        self.state.run_debug_manifest_path = manifest_path
        return manifest_path
