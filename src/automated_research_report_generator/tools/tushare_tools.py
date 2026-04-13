from __future__ import annotations

import json
import os
import re
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# 设计目的：给估值阶段提供一个面向 Tushare Pro 的专用数据入口，避免 agent 在 prompt 里自己拼接接口细节。
# 模块功能：解析公司名称或代码，拉取最新市场数据、财务指标和三大报表摘要，并整理成估值可直接使用的结构。
# 实现逻辑：先解析输入公司列表，再用 Tushare 解析证券代码，最后聚合 `daily_basic`、`fina_indicator`、`income`、`balancesheet` 和 `cashflow`。
# 可调参数：公司列表、交易日期、报告期和接口字段列表。
# 默认参数及原因：默认读取最新可得数据，原因是估值 crew 当前更需要最新横截面对比而不是完整历史序列。

TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"
TUSHARE_STOCK_BASIC_FIELDS = "ts_code,symbol,name,area,industry,market,list_date"
TUSHARE_DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,ps_ttm,dv_ttm,total_share,float_share,"
    "free_share,total_mv,circ_mv"
)
TUSHARE_FINA_INDICATOR_FIELDS = (
    "ts_code,ann_date,end_date,eps,dt_eps,bps,roe,roe_dt,roa,grossprofit_margin,netprofit_margin,"
    "ocfps,ocf_to_or,or_yoy,q_sales_yoy,netprofit_yoy,q_netprofit_yoy,assets_yoy,equity_yoy,"
    "saleexp_to_gr,adminexp_of_gr,finaexp_of_gr,op_of_gr,debt_to_assets,int_to_talcap,"
    "interestdebt,assets_turn,inv_turn,ar_turn,ebit,ebitda,rd_exp"
)
TUSHARE_INCOME_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,total_revenue,revenue,operate_profit,n_income_attr_p,ebit,ebitda"
)
TUSHARE_PEER_INCOME_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,total_revenue,revenue,operate_profit,n_income_attr_p,"
    "ebit,ebitda,sell_exp,admin_exp,fin_exp,rd_exp"
)
TUSHARE_BALANCESHEET_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,total_share,money_cap,total_assets,total_liab,"
    "total_hldr_eqy_exc_min_int,total_cur_assets,total_nca,total_cur_liab,total_ncl"
)
TUSHARE_CASHFLOW_FIELDS = (
    "ts_code,ann_date,f_ann_date,end_date,n_cashflow_act,c_inf_fr_operate_a,n_cashflow_inv_act,"
    "n_cash_flows_fnc_act"
)
TS_CODE_PATTERN = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.IGNORECASE)
SYMBOL_PATTERN = re.compile(r"^\d{6}$")
MARKET_VALUE_TO_CNY_MULTIPLIER = 10_000.0

_TUSHARE_PRO_CLIENT: Any | None = None
_STOCK_BASIC_RECORDS_CACHE: list[dict[str, Any]] | None = None
PEER_INFO_REQUIRED_METRIC_KEYS = (
    "revenue_amount",
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "selling_expense_ratio",
    "administrative_expense_ratio",
    "rd_expense_ratio",
    "financial_expense_ratio",
    "net_margin",
    "net_profit_growth",
    "ebitda_margin",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "ev_sales",
    "ev_ebitda",
    "asset_liability_ratio",
    "interest_bearing_debt_ratio",
    "asset_turnover",
    "inventory_turnover",
    "accounts_receivable_turnover",
    "accounts_payable_turnover",
)


def assess_tushare_valuation_tool_peer_coverage() -> dict[str, Any]:
    """
    目的：明确检查现有 `TushareValuationDataTool` 是否足以覆盖 peer info 场景。
    功能：输出 peer info 所需指标在当前估值工具中的覆盖矩阵，避免误把估值快照工具当作同行全量工具。
    实现逻辑：按当前估值工具真实返回字段，把每个 peer 指标归类为“直接覆盖 / 可推导 / 不支持”，并补充原因说明。
    可调参数：当前无显式参数，检查口径固定为 `PEER_INFO_REQUIRED_METRIC_KEYS`。
    默认参数及原因：默认以当前估值工具的真实字段边界做静态审计，原因是这类能力检查比运行时猜测更稳定。
    """

    direct_metrics = {
        "revenue_amount": "income.revenue / income.total_revenue",
        "revenue_growth": "fina_indicator.or_yoy / fina_indicator.q_sales_yoy",
        "gross_margin": "fina_indicator.grossprofit_margin",
        "selling_expense_ratio": "fina_indicator.saleexp_to_gr",
        "administrative_expense_ratio": "fina_indicator.adminexp_of_gr",
        "rd_expense_ratio": "fina_indicator.rd_exp / income.revenue",
        "financial_expense_ratio": "fina_indicator.finaexp_of_gr",
        "net_margin": "fina_indicator.netprofit_margin",
        "net_profit_growth": "fina_indicator.netprofit_yoy / fina_indicator.q_netprofit_yoy",
        "pe_ttm": "daily_basic.pe_ttm",
        "pb": "daily_basic.pb",
        "ps_ttm": "daily_basic.ps_ttm",
        "asset_turnover": "fina_indicator.assets_turn",
        "inventory_turnover": "fina_indicator.inv_turn",
        "accounts_receivable_turnover": "fina_indicator.ar_turn",
    }
    derived_metrics = {
        "operating_margin": "fina_indicator.op_of_gr / income.operate_profit / income.revenue",
        "ebitda_margin": "income.ebitda / income.revenue",
        "asset_liability_ratio": "fina_indicator.debt_to_assets / balancesheet.total_liab / balancesheet.total_assets",
        "interest_bearing_debt_ratio": "fina_indicator.interestdebt / balancesheet.total_assets",
        "ev_sales": "(market_cap + total_liab - money_cap) / revenue",
        "ev_ebitda": "(market_cap + total_liab - money_cap) / ebitda",
    }
    unsupported_reason = {
        "accounts_payable_turnover": "当前估值工具未拉取应付账款周转率字段。",
    }

    coverage: dict[str, dict[str, str]] = {}
    for metric_key in PEER_INFO_REQUIRED_METRIC_KEYS:
        if metric_key in direct_metrics:
            coverage[metric_key] = {
                "status": "direct",
                "detail": direct_metrics[metric_key],
            }
            continue
        if metric_key in derived_metrics:
            coverage[metric_key] = {
                "status": "derived",
                "detail": derived_metrics[metric_key],
            }
            continue
        coverage[metric_key] = {
            "status": "unsupported",
            "detail": unsupported_reason[metric_key],
        }

    unsupported_metrics = [
        metric_key
        for metric_key, payload in coverage.items()
        if payload["status"] == "unsupported"
    ]
    return {
        "tool_name": "TushareValuationDataTool",
        "summary": "current_valuation_tool_is_not_sufficient_for_full_peer_info_pack",
        "coverage": coverage,
        "unsupported_metrics": unsupported_metrics,
        "supports_multi_period_peer_matrix": False,
        "supports_future_period_consensus": False,
    }


def assess_tushare_peer_data_tool_coverage() -> dict[str, Any]:
    """
    目的：明确检查 `TusharePeerDataTool` 自身对当前 peer info 指标集合的真实覆盖边界。
    功能：输出同行数据工具的覆盖矩阵，避免把“估值工具缺口”误投射到“同行数据工具缺口”上。
    实现逻辑：按 `TusharePeerDataTool` 当前真实拉取和推导的字段，把每个指标归类为“直接覆盖 / 可推导 / 不支持”。
    可调参数：当前无显式参数，检查口径固定为 `PEER_INFO_REQUIRED_METRIC_KEYS`。
    默认参数及原因：默认走静态能力审计，原因是工具边界应先于 agent 调用被明确描述。
    """

    direct_metrics = {
        "revenue_amount": "income.revenue / income.total_revenue",
        "revenue_growth": "fina_indicator.or_yoy / fina_indicator.q_sales_yoy",
        "gross_margin": "fina_indicator.grossprofit_margin",
        "selling_expense_ratio": "fina_indicator.saleexp_to_gr",
        "administrative_expense_ratio": "fina_indicator.adminexp_of_gr",
        "rd_expense_ratio": "fina_indicator.rd_exp / income.revenue",
        "financial_expense_ratio": "fina_indicator.finaexp_of_gr",
        "net_margin": "fina_indicator.netprofit_margin",
        "net_profit_growth": "fina_indicator.netprofit_yoy / fina_indicator.q_netprofit_yoy",
        "pe_ttm": "daily_basic.pe_ttm",
        "pb": "daily_basic.pb",
        "ps_ttm": "daily_basic.ps_ttm",
        "asset_turnover": "fina_indicator.assets_turn",
        "inventory_turnover": "fina_indicator.inv_turn",
        "accounts_receivable_turnover": "fina_indicator.ar_turn",
    }
    derived_metrics = {
        "operating_margin": "fina_indicator.op_of_gr / income.operate_profit / income.revenue",
        "ebitda_margin": "income.ebitda / income.revenue",
        "ev_sales": "(market_cap + total_liab - money_cap) / revenue",
        "ev_ebitda": "(market_cap + total_liab - money_cap) / ebitda",
        "asset_liability_ratio": "fina_indicator.debt_to_assets / balancesheet.total_liab / balancesheet.total_assets",
        "interest_bearing_debt_ratio": "fina_indicator.interestdebt / balancesheet.total_assets",
    }
    unsupported_reason = {
        "accounts_payable_turnover": "当前同行数据工具未拉取应付账款周转率字段。",
    }

    coverage: dict[str, dict[str, str]] = {}
    for metric_key in PEER_INFO_REQUIRED_METRIC_KEYS:
        if metric_key in direct_metrics:
            coverage[metric_key] = {
                "status": "direct",
                "detail": direct_metrics[metric_key],
            }
            continue
        if metric_key in derived_metrics:
            coverage[metric_key] = {
                "status": "derived",
                "detail": derived_metrics[metric_key],
            }
            continue
        coverage[metric_key] = {
            "status": "unsupported",
            "detail": unsupported_reason[metric_key],
        }

    unsupported_metrics = [
        metric_key
        for metric_key, payload in coverage.items()
        if payload["status"] == "unsupported"
    ]
    return {
        "tool_name": "TusharePeerDataTool",
        "summary": "current_peer_data_tool_has_partial_gaps_for_peer_info_pack",
        "coverage": coverage,
        "unsupported_metrics": unsupported_metrics,
        "supports_multi_period_peer_matrix": True,
        "supports_future_period_consensus": False,
    }


class TushareValuationDataInput(BaseModel):
    """
    设计目的：定义 Tushare 估值数据工具的输入格式。
    模块功能：约束公司列表、交易日期和报告期输入。
    实现逻辑：通过 Pydantic 保证工具收到结构稳定的字符串参数。
    可调参数：`companies`、`trade_date` 和 `period`。
    默认参数及原因：日期和报告期默认留空，原因是多数估值场景先读取最新可得数据。
    """

    companies: str = Field(
        ...,
        description=(
            "公司列表。支持 JSON 数组或逗号分隔字符串。每项可以是公司简称、六位股票代码，"
            "也可以是 TS 代码，如 `宁德时代,比亚迪,300750.SZ`。"
        ),
    )
    trade_date: str = Field(
        default="",
        description="可选交易日期，格式为 YYYYMMDD。留空时读取最新可得每日指标。",
    )
    period: str = Field(
        default="",
        description="可选报告期，格式为 YYYYMMDD。留空时读取最新可得财务数据。",
    )


class TusharePeerDataInput(BaseModel):
    """
    目的：定义 peer info 专用 Tushare 工具的输入格式。
    功能：约束同行公司列表、多期间参数和所需指标列表，方便 agent 稳定批量取数。
    实现逻辑：使用 Pydantic 固定输入结构，避免 agent 在 prompt 中自由拼装字段。
    可调参数：`companies`、`periods` 和 `required_metrics`。
    默认参数及原因：`periods` 和 `required_metrics` 默认留空，原因是允许工具先回退到最新一期和默认指标全集。
    """

    companies: str = Field(
        ...,
        description=(
            "同行公司列表。支持 JSON 数组或逗号分隔字符串。元素可为公司简称、六位股票代码或 TS 代码。"
        ),
    )
    periods: str = Field(
        default="",
        description=(
            "期间列表，推荐使用 JSON 数组。每项包含 `label`，可选包含 `statement_period` 和 `trade_date`，"
            "例如 `[{'label':'FY-1','statement_period':'20241231','trade_date':'20250415'}]`。"
        ),
    )
    required_metrics: str = Field(
        default="",
        description=(
            "所需指标列表。支持 JSON 数组或逗号分隔字符串。留空时使用 peer info 默认指标全集。"
        ),
    )


def _get_tushare_pro_client() -> Any:
    """
    设计目的：集中管理 Tushare Pro 客户端初始化。
    模块功能：读取环境变量并返回可复用的 `pro_api` 客户端。
    实现逻辑：先检查 `TUSHARE_TOKEN`，再按需懒加载 `tushare` 并缓存客户端实例。
    可调参数：环境变量 `TUSHARE_TOKEN`。
    默认参数及原因：客户端默认做进程内缓存，原因是同一轮估值任务会重复调用多个接口。
    """

    global _TUSHARE_PRO_CLIENT
    if _TUSHARE_PRO_CLIENT is not None:
        return _TUSHARE_PRO_CLIENT

    token = os.getenv(TUSHARE_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(
            f"Missing {TUSHARE_TOKEN_ENV}. Set the Tushare token before using TushareValuationDataTool."
        )

    try:
        import tushare as ts
    except ImportError as exc:
        raise RuntimeError(
            "tushare is not installed. Run `uv sync` after adding the dependency, then retry."
        ) from exc

    _TUSHARE_PRO_CLIENT = ts.pro_api(token)
    return _TUSHARE_PRO_CLIENT


def _parse_companies_input(companies: str) -> list[str]:
    """
    设计目的：统一解析 agent 传入的公司列表格式。
    模块功能：支持 JSON 数组和逗号分隔两种输入方式。
    实现逻辑：优先按 JSON 解析，失败后回退到逗号分隔，再清洗空白项。
    可调参数：`companies`。
    默认参数及原因：不支持嵌套复杂对象，原因是估值工具当前只需要一维公司标识列表。
    """

    cleaned = (companies or "").strip()
    if not cleaned:
        raise ValueError("`companies` cannot be empty.")

    items: list[Any]
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            items = parsed
        else:
            items = [parsed]
    except json.JSONDecodeError:
        items = [part.strip() for part in cleaned.split(",")]

    normalized_items: list[str] = []
    for item in items:
        if isinstance(item, dict):
            candidate = item.get("ts_code") or item.get("company") or item.get("name") or ""
        else:
            candidate = str(item or "")
        candidate = " ".join(str(candidate).split()).strip()
        if candidate:
            normalized_items.append(candidate)

    if not normalized_items:
        raise ValueError("No valid company identifiers were parsed from `companies`.")
    return normalized_items


def _parse_generic_string_list_input(raw_value: str, *, field_name: str) -> list[str]:
    """
    目的：统一解析字符串列表类输入，减少多种工具重复写 JSON/CSV 兼容逻辑。
    功能：支持 JSON 数组、单值和逗号分隔字符串三种输入形式。
    实现逻辑：优先尝试 JSON 解析，失败后回退到逗号分隔，再清洗空白与空值。
    可调参数：原始输入值和字段名。
    默认参数及原因：解析失败时返回清洗后的最小列表，原因是工具层应尽量兼容 agent 的常见输入形式。
    """

    cleaned = (raw_value or "").strip()
    if not cleaned:
        return []

    items: list[Any]
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            items = parsed
        else:
            items = [parsed]
    except json.JSONDecodeError:
        items = [part.strip() for part in cleaned.split(",")]

    normalized_items = [
        " ".join(str(item or "").split()).strip()
        for item in items
        if " ".join(str(item or "").split()).strip()
    ]
    if not normalized_items and cleaned:
        raise ValueError(f"`{field_name}` was provided but no valid values were parsed.")
    return normalized_items


def _parse_peer_periods_input(periods: str) -> list[dict[str, str]]:
    """
    目的：把 peer info 的期间输入解析成统一的结构化列表。
    功能：支持多期间批量取数，并固定每个期间的标签、报表期和行情日期口径。
    实现逻辑：优先解析 JSON 数组；留空时回退到一个 `LATEST` 期间，保证工具仍可单次运行。
    可调参数：`periods` 原始字符串。
    默认参数及原因：留空时默认只取 `LATEST`，原因是便于最小调试和单期排障。
    """

    cleaned = (periods or "").strip()
    if not cleaned:
        return [
            {
                "label": "LATEST",
                "statement_period": "",
                "trade_date": "",
            }
        ]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("`periods` must be a JSON array when provided.") from exc

    if not isinstance(parsed, list) or not parsed:
        raise ValueError("`periods` must be a non-empty JSON array when provided.")

    normalized_periods: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Each item in `periods` must be a JSON object.")
        label = " ".join(str(item.get("label", "")).split()).strip()
        if not label:
            raise ValueError("Each item in `periods` must include a non-empty `label`.")
        normalized_periods.append(
            {
                "label": label,
                "statement_period": " ".join(str(item.get("statement_period", "")).split()).strip(),
                "trade_date": " ".join(str(item.get("trade_date", "")).split()).strip(),
            }
        )
    return normalized_periods


def _load_stock_basic_records() -> list[dict[str, Any]]:
    """
    设计目的：集中缓存股票基础信息，避免重复打同一个全量接口。
    模块功能：拉取上市股票基础列表并缓存为字典列表。
    实现逻辑：首次调用时请求 `stock_basic`，后续直接复用进程内缓存。
    可调参数：字段列表固定为估值场景所需的最小集合。
    默认参数及原因：默认只读取上市状态 `L`，原因是估值对比优先关注当前可交易公司。
    """

    global _STOCK_BASIC_RECORDS_CACHE
    if _STOCK_BASIC_RECORDS_CACHE is not None:
        return _STOCK_BASIC_RECORDS_CACHE

    pro = _get_tushare_pro_client()
    dataframe = pro.stock_basic(
        list_status="L",
        fields=TUSHARE_STOCK_BASIC_FIELDS,
    )
    _STOCK_BASIC_RECORDS_CACHE = dataframe.to_dict(orient="records")
    return _STOCK_BASIC_RECORDS_CACHE


def _resolve_company_identifier(identifier: str) -> dict[str, Any]:
    """
    设计目的：把公司简称、六位代码或 TS 代码解析成统一证券身份。
    模块功能：在 `stock_basic` 结果中找到唯一匹配的上市公司。
    实现逻辑：按 TS 代码、六位代码、精确简称、唯一模糊匹配的顺序依次解析。
    可调参数：`identifier`。
    默认参数及原因：模糊匹配只在唯一命中时采用，原因是估值数据不能容忍错配公司。
    """

    records = _load_stock_basic_records()
    normalized_identifier = " ".join((identifier or "").split()).strip()
    upper_identifier = normalized_identifier.upper()

    if TS_CODE_PATTERN.match(upper_identifier):
        for record in records:
            if str(record.get("ts_code", "")).upper() == upper_identifier:
                return record

    if SYMBOL_PATTERN.match(normalized_identifier):
        exact_symbol_matches = [record for record in records if str(record.get("symbol", "")) == normalized_identifier]
        if len(exact_symbol_matches) == 1:
            return exact_symbol_matches[0]

    exact_name_matches = [record for record in records if str(record.get("name", "")) == normalized_identifier]
    if len(exact_name_matches) == 1:
        return exact_name_matches[0]

    fuzzy_matches = [
        record
        for record in records
        if normalized_identifier in str(record.get("name", ""))
        or str(record.get("name", "")) in normalized_identifier
    ]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]

    if fuzzy_matches:
        candidate_names = [str(record.get("name", "")) for record in fuzzy_matches[:5]]
        raise ValueError(
            f"Ambiguous company identifier `{identifier}`. Candidates: {candidate_names}"
        )

    raise ValueError(f"Unable to resolve company identifier `{identifier}` from Tushare stock_basic.")


def _pick_latest_row(dataframe: Any) -> dict[str, Any] | None:
    """
    设计目的：统一从 Tushare DataFrame 中取最新一条记录。
    模块功能：把 DataFrame 结果转换成单条字典。
    实现逻辑：优先按常见日期字段倒序排序，再取首条记录。
    可调参数：Tushare 返回的 DataFrame。
    默认参数及原因：空结果返回 `None`，原因是部分公司或权限条件下确实可能没有数据。
    """

    if dataframe is None or getattr(dataframe, "empty", True):
        return None

    rows = dataframe.to_dict(orient="records")
    if not rows:
        return None

    def sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
        """
        目的：给 Tushare 行记录提供统一排序键。
        功能：把交易日、报告期和公告日拼成可比较元组。
        实现逻辑：依次读取 `trade_date`、`end_date` 和 `ann_date`，缺值时回退空串。
        可调参数：`row`。
        默认参数及原因：默认按这三个字段排序，原因是它们最能代表一条财务或行情记录的新旧程度。
        """

        return (
            str(row.get("trade_date", "") or ""),
            str(row.get("end_date", "") or ""),
            str(row.get("ann_date", "") or ""),
        )

    rows.sort(key=sort_key, reverse=True)
    return rows[0]


def _safe_dataframe_call(interface_name: str, **kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
    """
    设计目的：把单个 Tushare 接口调用包装成稳妥的错误边界。
    模块功能：返回最新一条记录，或者返回可追踪的错误说明。
    实现逻辑：动态调用接口，成功则取最新记录，失败则把错误文本包装回传。
    可调参数：接口名称和接口参数。
    默认参数及原因：错误不直接中断整个工具，原因是估值阶段允许部分接口缺失但不应整体失败。
    """

    pro = _get_tushare_pro_client()
    filtered_kwargs = {key: value for key, value in kwargs.items() if value is not None}
    try:
        dataframe = getattr(pro, interface_name)(**filtered_kwargs)
        return _pick_latest_row(dataframe), None
    except Exception as exc:
        return None, f"{interface_name}: {type(exc).__name__}: {exc}"


def _safe_dataframe_records_call(
    interface_name: str, **kwargs: Any
) -> tuple[list[dict[str, Any]], str | None]:
    """
    目的：给 peer info 场景提供保留多条记录的 Tushare 调用包装。
    功能：返回接口的完整记录列表，或返回可追溯的错误说明。
    实现逻辑：动态调用目标接口，成功时转成 records 列表，失败时统一返回错误文本。
    可调参数：接口名和接口参数。
    默认参数及原因：空结果返回空列表，原因是 peer 场景需要显式区分“无数据”和“调用报错”。
    """

    pro = _get_tushare_pro_client()
    filtered_kwargs = {key: value for key, value in kwargs.items() if value is not None}
    try:
        dataframe = getattr(pro, interface_name)(**filtered_kwargs)
        if dataframe is None or getattr(dataframe, "empty", True):
            return [], None
        return dataframe.to_dict(orient="records"), None
    except Exception as exc:
        return [], f"{interface_name}: {type(exc).__name__}: {exc}"


def _to_number(value: Any) -> float | None:
    """
    设计目的：统一处理 Tushare 原始结果里的数值转换。
    模块功能：把可解析值转成 `float`，无法解析时返回 `None`。
    实现逻辑：过滤空值后调用 `float()`。
    可调参数：任意原始值。
    默认参数及原因：错误时返回 `None`，原因是金融数据常见缺失值和字符串占位。
    """

    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_market_value_to_cny(value_in_ten_thousand: Any) -> float | None:
    """
    设计目的：把 `daily_basic` 的市值字段统一转换为人民币元。
    模块功能：将 Tushare 文档标明的“万元”单位换算成“元”。
    实现逻辑：先转成数字，再乘以 10000。
    可调参数：`daily_basic.total_mv` 或 `daily_basic.circ_mv`。
    默认参数及原因：空值返回 `None`，原因是部分公司或日期可能拿不到完整行情字段。
    """

    numeric_value = _to_number(value_in_ten_thousand)
    if numeric_value is None:
        return None
    return numeric_value * MARKET_VALUE_TO_CNY_MULTIPLIER


def _build_period_metric_values(
    *,
    market_data: dict[str, Any],
    financial_indicator: dict[str, Any],
    income_statement: dict[str, Any],
    balance_sheet: dict[str, Any],
) -> dict[str, float | None]:
    """
    目的：把单一期间的 Tushare 原始记录整理成 peer info 可直接消费的指标值字典。
    功能：输出直接指标和可稳定推导的派生指标，减少 agent 在 prompt 中临时算式。
    实现逻辑：先归一化常见原始字段，再按固定公式计算利润率和 EV 倍数等派生指标。
    可调参数：市场数据、财务指标、利润表和资产负债表记录。
    默认参数及原因：无法可靠计算的指标返回 `None`，原因是缺失比误算更安全。
    """

    revenue_amount = _to_number(income_statement.get("revenue")) or _to_number(
        income_statement.get("total_revenue")
    )
    operate_profit = _to_number(income_statement.get("operate_profit"))
    net_income = _to_number(income_statement.get("n_income_attr_p"))
    ebitda = _to_number(income_statement.get("ebitda"))
    total_assets = _to_number(balance_sheet.get("total_assets"))
    total_liab = _to_number(balance_sheet.get("total_liab"))
    money_cap = _to_number(balance_sheet.get("money_cap"))
    market_cap = _normalize_market_value_to_cny(market_data.get("total_mv"))

    def ratio(numerator: float | None, denominator: float | None) -> float | None:
        """
        目的：统一处理同行指标里的比率计算。
        功能：在分母非空且非零时返回浮点比率。
        实现逻辑：先判断输入是否可用，再做简单除法。
        可调参数：分子与分母。
        默认参数及原因：分母为空或为零时返回 `None`，原因是避免生成误导性极值。
        """

        if numerator is None or denominator in (None, 0):
            return None
        return numerator / denominator

    enterprise_value = None
    if market_cap is not None and total_liab is not None and money_cap is not None:
        enterprise_value = market_cap + total_liab - money_cap

    return {
        "revenue_amount": revenue_amount,
        "revenue_growth": _to_number(financial_indicator.get("or_yoy"))
        or _to_number(financial_indicator.get("q_sales_yoy")),
        "gross_margin": _to_number(financial_indicator.get("grossprofit_margin")),
        "operating_margin": _to_number(financial_indicator.get("op_of_gr"))
        or ratio(operate_profit, revenue_amount),
        "selling_expense_ratio": _to_number(financial_indicator.get("saleexp_to_gr"))
        or ratio(_to_number(income_statement.get("sell_exp")), revenue_amount),
        "administrative_expense_ratio": _to_number(financial_indicator.get("adminexp_of_gr"))
        or ratio(_to_number(income_statement.get("admin_exp")), revenue_amount),
        "rd_expense_ratio": ratio(
            _to_number(financial_indicator.get("rd_exp")) or _to_number(income_statement.get("rd_exp")),
            revenue_amount,
        ),
        "financial_expense_ratio": _to_number(financial_indicator.get("finaexp_of_gr"))
        or ratio(_to_number(income_statement.get("fin_exp")), revenue_amount),
        "net_margin": _to_number(financial_indicator.get("netprofit_margin"))
        or ratio(net_income, revenue_amount),
        "net_profit_growth": _to_number(financial_indicator.get("netprofit_yoy"))
        or _to_number(financial_indicator.get("q_netprofit_yoy"))
        or _to_number(financial_indicator.get("q_profit_yoy")),
        "ebitda_margin": ratio(ebitda, revenue_amount),
        "pe_ttm": _to_number(market_data.get("pe_ttm")) or _to_number(market_data.get("pe")),
        "pb": _to_number(market_data.get("pb")),
        "ps_ttm": _to_number(market_data.get("ps_ttm")),
        "ev_sales": ratio(enterprise_value, revenue_amount),
        "ev_ebitda": ratio(enterprise_value, ebitda),
        "asset_liability_ratio": _to_number(financial_indicator.get("debt_to_assets"))
        or ratio(total_liab, total_assets),
        "interest_bearing_debt_ratio": ratio(
            _to_number(financial_indicator.get("interestdebt")),
            total_assets,
        ),
        "asset_turnover": _to_number(financial_indicator.get("assets_turn")),
        "inventory_turnover": _to_number(financial_indicator.get("inv_turn")),
        "accounts_receivable_turnover": _to_number(financial_indicator.get("ar_turn")),
        "accounts_payable_turnover": None,
    }


def _build_peer_input(company_snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    设计目的：把聚合后的单家公司数据整理成可比估值工具可直接消费的行格式。
    模块功能：抽取市场值、利润表和资产负债表关键字段，形成标准 peer row。
    实现逻辑：优先使用已转换单位后的市值，再补齐收入、EBITDA、净利润和账面净资产。
    可调参数：单家公司聚合后的快照字典。
    默认参数及原因：无法安全计算的字段保留 `None`，原因是比错算更稳妥。
    """

    market_data = company_snapshot.get("market_data", {}) or {}
    income_statement = company_snapshot.get("income_statement", {}) or {}
    balance_sheet = company_snapshot.get("balance_sheet", {}) or {}

    market_cap = market_data.get("total_mv_cny")
    total_liab = _to_number(balance_sheet.get("total_liab"))
    money_cap = _to_number(balance_sheet.get("money_cap"))
    enterprise_value = None
    if market_cap is not None and total_liab is not None and money_cap is not None:
        enterprise_value = market_cap + total_liab - money_cap

    return {
        "company": company_snapshot.get("name"),
        "ts_code": company_snapshot.get("ts_code"),
        "industry": company_snapshot.get("industry"),
        "market_cap": market_cap,
        "enterprise_value": enterprise_value,
        "revenue": _to_number(income_statement.get("revenue")) or _to_number(income_statement.get("total_revenue")),
        "ebitda": _to_number(income_statement.get("ebitda")),
        "net_income": _to_number(income_statement.get("n_income_attr_p")),
        "book_value": _to_number(balance_sheet.get("total_hldr_eqy_exc_min_int")),
    }


class TushareValuationDataTool(BaseTool):
    """
    设计目的：给估值 crew 提供一个面向 Tushare 的统一取数工具。
    模块功能：解析公司标识、抓取市场与财务数据，并输出可直接用于估值比较的 JSON。
    实现逻辑：按公司逐个聚合 `stock_basic`、`daily_basic`、`fina_indicator`、`income`、`balancesheet` 和 `cashflow`。
    可调参数：公司列表、交易日期和报告期。
    默认参数及原因：默认返回最新可得记录，原因是估值阶段主要做当前时点比较。
    """

    name: str = "tushare_valuation_data_tool"
    description: str = (
        "Resolve China A-share company names/codes through Tushare, then fetch latest market data, "
        "financial indicators, and normalized comparable-company inputs for valuation analysis."
    )
    args_schema: type[BaseModel] = TushareValuationDataInput

    def _run(self, companies: str, trade_date: str = "", period: str = "") -> str:
        """
        设计目的：为估值 agent 提供单次聚合取数入口。
        模块功能：返回每家公司最新行情、财务指标、报表摘要和可比估值输入行。
        实现逻辑：先解析公司列表，再逐家公司拉取各接口最新记录，最后统一序列化成 JSON。
        可调参数：`companies`、`trade_date` 和 `period`。
        默认参数及原因：日期留空时读取最新可得数据，原因是当前估值任务优先关注最新横截面。
        """

        company_identifiers = _parse_companies_input(companies)
        snapshots: list[dict[str, Any]] = []
        peer_inputs: list[dict[str, Any]] = []

        for identifier in company_identifiers:
            resolved = _resolve_company_identifier(identifier)
            ts_code = str(resolved.get("ts_code", ""))
            interface_errors: list[str] = []

            daily_basic, daily_basic_error = _safe_dataframe_call(
                "daily_basic",
                ts_code=ts_code,
                trade_date=trade_date or None,
                fields=TUSHARE_DAILY_BASIC_FIELDS,
            )
            if daily_basic_error:
                interface_errors.append(daily_basic_error)

            fina_indicator, fina_indicator_error = _safe_dataframe_call(
                "fina_indicator",
                ts_code=ts_code,
                period=period or None,
                fields=TUSHARE_FINA_INDICATOR_FIELDS,
            )
            if fina_indicator_error:
                interface_errors.append(fina_indicator_error)

            income_statement, income_error = _safe_dataframe_call(
                "income",
                ts_code=ts_code,
                period=period or None,
                fields=TUSHARE_INCOME_FIELDS,
            )
            if income_error:
                interface_errors.append(income_error)

            balance_sheet, balance_sheet_error = _safe_dataframe_call(
                "balancesheet",
                ts_code=ts_code,
                period=period or None,
                fields=TUSHARE_BALANCESHEET_FIELDS,
            )
            if balance_sheet_error:
                interface_errors.append(balance_sheet_error)

            cashflow_statement, cashflow_error = _safe_dataframe_call(
                "cashflow",
                ts_code=ts_code,
                period=period or None,
                fields=TUSHARE_CASHFLOW_FIELDS,
            )
            if cashflow_error:
                interface_errors.append(cashflow_error)

            market_snapshot = dict(daily_basic or {})
            if market_snapshot:
                market_snapshot["total_mv_cny"] = _normalize_market_value_to_cny(market_snapshot.get("total_mv"))
                market_snapshot["circ_mv_cny"] = _normalize_market_value_to_cny(market_snapshot.get("circ_mv"))

            company_snapshot = {
                "input_identifier": identifier,
                "ts_code": ts_code,
                "symbol": resolved.get("symbol"),
                "name": resolved.get("name"),
                "area": resolved.get("area"),
                "industry": resolved.get("industry"),
                "market": resolved.get("market"),
                "list_date": resolved.get("list_date"),
                "market_data": market_snapshot,
                "financial_indicator": fina_indicator or {},
                "income_statement": income_statement or {},
                "balance_sheet": balance_sheet or {},
                "cashflow_statement": cashflow_statement or {},
                "interface_errors": interface_errors,
            }
            company_snapshot["valuation_peer_input"] = _build_peer_input(company_snapshot)
            snapshots.append(company_snapshot)
            peer_inputs.append(company_snapshot["valuation_peer_input"])

        payload = {
            "requested_companies": company_identifiers,
            "trade_date": trade_date or None,
            "period": period or None,
            "topic_scope": "valuation_market_and_financial_data",
            "units_note": {
                "daily_basic.total_mv": "Tushare 文档标明单位为万元，工具已转换为 total_mv_cny（元）",
                "daily_basic.circ_mv": "Tushare 文档标明单位为万元，工具已转换为 circ_mv_cny（元）",
                "income/balancesheet/cashflow": "保留 Tushare 原始字段命名和原始数量级，便于追溯接口定义",
            },
            "companies": snapshots,
            "valuation_peer_inputs": peer_inputs,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class TusharePeerDataTool(BaseTool):
    """
    目的：给 peer info 专题提供一个专门的 Tushare 多期同行取数工具。
    功能：解析同行公司、按多个期间抓取财务与市场快照，并输出指标矩阵、缺口和不支持项。
    实现逻辑：复用现有公司解析与数值处理 helper，再按期间循环聚合 `daily_basic`、`fina_indicator`、`income`、`balancesheet`。
    可调参数：公司列表、期间列表和所需指标列表。
    默认参数及原因：默认输出 peer info 指标全集，原因是同行专题通常希望先得到完整底稿再由 agent 按需取用。
    """

    name: str = "tushare_peer_data_tool"
    description: str = (
        "Resolve comparable-company identifiers through Tushare, fetch multi-period peer data, "
        "and return structured metric matrices, gaps, and unsupported items for peer analysis."
    )
    args_schema: type[BaseModel] = TusharePeerDataInput

    def _run(
        self, companies: str, periods: str = "", required_metrics: str = ""
    ) -> str:
        """
        目的：给同行专题 agent 提供单次批量取数入口。
        功能：返回多家公司、多期间的原始记录、派生指标、缺失指标和不支持指标。
        实现逻辑：先解析输入，再按公司和期间双层循环抓取接口数据，最后汇总成结构化 JSON。
        可调参数：`companies`、`periods` 和 `required_metrics`。
        默认参数及原因：`required_metrics` 留空时输出 peer info 默认指标全集，原因是这样最适合作为同行底稿。
        """

        company_identifiers = _parse_companies_input(companies)
        period_specs = _parse_peer_periods_input(periods)
        metric_keys = _parse_generic_string_list_input(
            required_metrics, field_name="required_metrics"
        ) or list(PEER_INFO_REQUIRED_METRIC_KEYS)
        valuation_tool_audit = assess_tushare_valuation_tool_peer_coverage()
        peer_data_tool_audit = assess_tushare_peer_data_tool_coverage()

        company_snapshots: list[dict[str, Any]] = []
        metric_matrix: dict[str, dict[str, dict[str, float | None]]] = {
            metric_key: {} for metric_key in metric_keys
        }
        unsupported_metrics = [
            metric_key
            for metric_key in metric_keys
            if peer_data_tool_audit["coverage"].get(metric_key, {}).get("status") == "unsupported"
        ]

        for identifier in company_identifiers:
            resolved = _resolve_company_identifier(identifier)
            ts_code = str(resolved.get("ts_code", ""))
            company_periods: dict[str, Any] = {}

            for period_spec in period_specs:
                label = period_spec["label"]
                trade_date = period_spec["trade_date"] or None
                statement_period = period_spec["statement_period"] or None
                interface_errors: list[str] = []

                daily_basic, daily_basic_error = _safe_dataframe_call(
                    "daily_basic",
                    ts_code=ts_code,
                    trade_date=trade_date,
                    fields=TUSHARE_DAILY_BASIC_FIELDS,
                )
                if daily_basic_error:
                    interface_errors.append(daily_basic_error)

                fina_indicator, fina_indicator_error = _safe_dataframe_call(
                    "fina_indicator",
                    ts_code=ts_code,
                    period=statement_period,
                    fields=TUSHARE_FINA_INDICATOR_FIELDS,
                )
                if fina_indicator_error:
                    interface_errors.append(fina_indicator_error)

                income_statement, income_error = _safe_dataframe_call(
                    "income",
                    ts_code=ts_code,
                    period=statement_period,
                    fields=TUSHARE_PEER_INCOME_FIELDS,
                )
                if income_error:
                    interface_errors.append(income_error)

                balance_sheet, balance_sheet_error = _safe_dataframe_call(
                    "balancesheet",
                    ts_code=ts_code,
                    period=statement_period,
                    fields=TUSHARE_BALANCESHEET_FIELDS,
                )
                if balance_sheet_error:
                    interface_errors.append(balance_sheet_error)

                cashflow_statement, cashflow_error = _safe_dataframe_call(
                    "cashflow",
                    ts_code=ts_code,
                    period=statement_period,
                    fields=TUSHARE_CASHFLOW_FIELDS,
                )
                if cashflow_error:
                    interface_errors.append(cashflow_error)

                market_snapshot = dict(daily_basic or {})
                if market_snapshot:
                    market_snapshot["total_mv_cny"] = _normalize_market_value_to_cny(
                        market_snapshot.get("total_mv")
                    )
                    market_snapshot["circ_mv_cny"] = _normalize_market_value_to_cny(
                        market_snapshot.get("circ_mv")
                    )

                metric_values = _build_period_metric_values(
                    market_data=market_snapshot,
                    financial_indicator=fina_indicator or {},
                    income_statement=income_statement or {},
                    balance_sheet=balance_sheet or {},
                )
                missing_metrics = [
                    metric_key
                    for metric_key in metric_keys
                    if metric_key not in unsupported_metrics
                    and metric_values.get(metric_key) is None
                ]
                period_payload = {
                    "label": label,
                    "statement_period": statement_period,
                    "trade_date": trade_date,
                    "market_data": market_snapshot,
                    "financial_indicator": fina_indicator or {},
                    "income_statement": income_statement or {},
                    "balance_sheet": balance_sheet or {},
                    "cashflow_statement": cashflow_statement or {},
                    "metric_values": {
                        metric_key: metric_values.get(metric_key) for metric_key in metric_keys
                    },
                    "missing_metrics": missing_metrics,
                    "unsupported_metrics": unsupported_metrics,
                    "interface_errors": interface_errors,
                }
                company_periods[label] = period_payload

                for metric_key in metric_keys:
                    metric_matrix.setdefault(metric_key, {})
                    metric_matrix[metric_key].setdefault(ts_code, {})
                    metric_matrix[metric_key][ts_code][label] = metric_values.get(metric_key)

            company_snapshots.append(
                {
                    "input_identifier": identifier,
                    "ts_code": ts_code,
                    "symbol": resolved.get("symbol"),
                    "name": resolved.get("name"),
                    "area": resolved.get("area"),
                    "industry": resolved.get("industry"),
                    "market": resolved.get("market"),
                    "list_date": resolved.get("list_date"),
                    "periods": company_periods,
                }
            )

        payload = {
            "requested_companies": company_identifiers,
            "requested_periods": period_specs,
            "required_metrics": metric_keys,
            "valuation_tool_peer_coverage_audit": valuation_tool_audit,
            "peer_data_tool_coverage_audit": peer_data_tool_audit,
            "unsupported_metrics": unsupported_metrics,
            "company_snapshots": company_snapshots,
            "metric_matrix": metric_matrix,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
