from __future__ import annotations

import json
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class FinancialMetricsCalculatorInput(BaseModel):
    """
    目的：定义财务指标计算工具的输入格式。
    功能：约束调用方按“指标定义 + 原始字段值”的 JSON 结构传参。
    实现逻辑：通过 Pydantic 固定输入字段，减少 agent 在 prompt 里自由拼接输入的波动。
    可调参数：`metrics_json`，由调用方传入待计算指标和原始字段值。
    默认参数及原因：无默认业务值，原因是不同公司和不同报表期的原始字段差异很大。
    """

    metrics_json: str = Field(
        ...,
        description=(
            "JSON string containing `metrics` and `raw_values`. "
            "`metrics` should describe each metric name, formula, periods, and field mapping."
        ),
    )


class FinancialMetricsCalculatorTool(BaseTool):
    """
    目的：给财务计算任务提供一个确定性的规则计算入口。
    功能：基于传入的原始字段值和公式定义，计算各期公司指标。
    实现逻辑：按指标逐条遍历，读取每个报表期的字段值，调用内置公式完成计算，再返回结构化 JSON。
    可调参数：输入 JSON 中的指标列表、期数字段、公式名称和字段映射。
    默认参数及原因：返回 JSON 字符串，原因是这种格式最适合被 agent 和 task 文本上下文继续消费。
    """

    name: str = "financial_metrics_calculator_tool"
    description: str = (
        "Calculate company financial metrics from raw period-based values using deterministic formulas."
    )
    args_schema: type[BaseModel] = FinancialMetricsCalculatorInput

    def _run(self, metrics_json: str) -> str:
        """
        目的：执行财务指标的确定性规则计算。
        功能：把输入中的指标定义和原始字段值转换成逐指标、逐期的计算结果。
        实现逻辑：先解析 JSON，再按指标和期数循环计算，并输出 `value`、`status`、`formula`、`inputs_used`。
        可调参数：`metrics_json`。
        默认参数及原因：字段缺失时返回 `missing`，原因是缺值比造数更安全。
        """

        payload = json.loads(metrics_json)
        metrics = payload.get("metrics", [])
        raw_values = payload.get("raw_values", {})

        results: list[dict[str, Any]] = []
        for metric in metrics:
            metric_name = str(metric.get("name", "")).strip()
            formula = str(metric.get("formula", "")).strip()
            periods = metric.get("periods", []) or []
            field_mapping = metric.get("field_mapping", {}) or {}
            period_results: dict[str, Any] = {}

            for period in periods:
                period_key = str(period).strip()
                period_raw_values = raw_values.get(period_key, {}) or {}
                period_result = self._calculate_single_period(
                    formula=formula,
                    period_raw_values=period_raw_values,
                    field_mapping=field_mapping,
                )
                period_results[period_key] = period_result

            results.append(
                {
                    "metric_name": metric_name,
                    "formula": formula,
                    "period_results": period_results,
                }
            )

        return json.dumps({"results": results}, ensure_ascii=False, indent=2)

    def _calculate_single_period(
        self,
        *,
        formula: str,
        period_raw_values: dict[str, Any],
        field_mapping: dict[str, Any],
    ) -> dict[str, Any]:
        """
        目的：计算单个报表期的单个指标。
        功能：读取所需字段值，执行对应公式，并返回标准化结果。
        实现逻辑：先根据字段映射提取输入值，再按公式名路由到不同计算函数。
        可调参数：公式名、单期原始字段值、字段映射。
        默认参数及原因：不支持的公式返回 `missing`，原因是宁可让 agent 明确知道未算出，也不输出不可靠结果。
        """

        inputs_used = {
            alias: self._to_float(period_raw_values.get(str(source_field)))
            for alias, source_field in field_mapping.items()
        }

        if formula == "ratio":
            value = self._safe_div(
                inputs_used.get("numerator"),
                inputs_used.get("denominator"),
            )
        elif formula == "difference":
            left = inputs_used.get("left")
            right = inputs_used.get("right")
            value = None if left is None or right is None else left - right
        elif formula == "sum":
            summands = [
                value
                for alias, value in inputs_used.items()
                if alias.startswith("item_")
            ]
            value = None if not summands or any(item is None for item in summands) else sum(summands)
        else:
            value = None

        return {
            "value": value,
            "status": "calculated" if value is not None else "missing",
            "formula": formula,
            "inputs_used": inputs_used,
        }

    def _to_float(self, value: Any) -> float | None:
        """
        目的：统一原始字段的数值转换方式。
        功能：把可解析值转成 `float`，无法解析时返回 `None`。
        实现逻辑：先处理空值，再尝试浮点转换。
        可调参数：任意原始值。
        默认参数及原因：错误时返回 `None`，原因是财务原始字段常存在缺失或占位文本。
        """

        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_div(
        self, numerator: float | None, denominator: float | None
    ) -> float | None:
        """
        目的：统一处理财务比率中的除法。
        功能：在分子分母都有效时返回比值，否则返回 `None`。
        实现逻辑：先判断空值和零分母，再执行除法。
        可调参数：分子和分母。
        默认参数及原因：分母为空或为零时返回 `None`，原因是此时结果不可靠。
        """

        if numerator is None or denominator in (None, 0):
            return None
        return numerator / denominator
