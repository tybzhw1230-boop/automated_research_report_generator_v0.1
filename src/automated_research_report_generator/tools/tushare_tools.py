from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime
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
STANDARD_PERIOD_KIND_SEQUENCE = ("Q1", "H1", "Q3", "A")
STANDARD_PERIOD_END_MONTH_DAY = {
    "Q1": (3, 31),
    "H1": (6, 30),
    "Q3": (9, 30),
    "A": (12, 31),
}
STANDARD_PERIOD_DISCLOSURE_MONTH_DAY = {
    "Q1": (4, 30),
    "H1": (8, 31),
    "Q3": (10, 31),
    "A": (4, 30),
}
TTM_ALIAS_TOKENS = {
    "TTM",
    "LTM",
    "TRAILINGTWELVEMONTHS",
    "LASTTWELVEMONTHS",
    "LATESTTWELVEMONTHS",
    "ROLLINGTWELVEMONTHS",
    "ROLLING12MONTHS",
    "TRAILING12MONTHS",
    "LATEST12MONTHS",
    "最近12个月",
    "近12个月",
    "滚动12个月",
}
CURRENT_PERIOD_ALIAS_TOKENS = {
    "LATEST",
    "CURRENT",
    "CURRENTPERIOD",
    "THISPERIOD",
    "LATESTPERIOD",
    "LATESTREPORTPERIOD",
    "LATESTREPORTINGPERIOD",
    "CURRENTREPORTPERIOD",
    "CURRENTREPORTINGPERIOD",
    "FQ0/FY0",
    "FQ0FY0",
    "当前期",
    "本期",
    "最近一期",
    "最新一期",
    "最新报告期",
    "当前报告期",
}
PREVIOUS_PERIOD_ALIAS_TOKENS = {
    "PREVIOUSPERIOD",
    "LASTPERIOD",
    "PRIORPERIOD",
    "FQ-1",
    "上一期",
    "前一期",
    "上期",
}
PRIOR_YEAR_COMPARABLE_ALIAS_TOKENS = {
    "SAMEPERIODLASTYEAR",
    "PRIORYEARCOMPARABLE",
    "PRIORYEARCOMPARABLE",
    "LASTYEARCOMPARABLE",
    "上年同期",
    "去年同期",
}
PREVIOUS_FISCAL_YEAR_ALIAS_TOKENS = {
    "PREVIOUSFISCALYEAR",
    "LASTFISCALYEAR",
    "PRIORFISCALYEAR",
    "FY-1",
    "上一财年",
    "上个财年",
}
NEXT_FISCAL_YEAR_ALIAS_TOKENS = {
    "NEXTFISCALYEAR",
    "NEXTYEARFORECAST",
    "FY1",
    "明年预测",
}
SECOND_NEXT_FISCAL_YEAR_ALIAS_TOKENS = {
    "FOLLOWINGFISCALYEAR",
    "SECONDNEXTFISCALYEAR",
    "FOLLOWINGYEARFORECAST",
    "FY2",
    "后年预测",
}
PEER_PERIOD_LABEL_PATTERN = re.compile(r"^(?P<year>\d{4}|\d{2})(?P<suffix>A|E)$")
PEER_FY_WITH_PREFIX_PATTERN = re.compile(r"^FY(?P<year>\d{4}|\d{2})(?P<suffix>A|E)?$")
PEER_YEAR_WITH_FY_SUFFIX_PATTERN = re.compile(r"^(?P<year>\d{4}|\d{2})FY(?P<suffix>A|E)?$")
PEER_STANDARD_PERIOD_PATTERN = re.compile(
    r"^(?:FY)?(?P<year>\d{4}|\d{2})(?P<kind>Q1|Q2|Q3|Q4|H1)(?P<suffix>A)?$"
)
PEER_YYYY_CHINESE_PATTERN = re.compile(r"^(?P<year>\d{4}|\d{2})(?P<label>.+)$")
PEER_COMPACT_DATE_PATTERN = re.compile(r"^\d{8}$")

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


def _current_local_date() -> date:
    """
    目的：集中提供同行期间解析使用的“当前日期”锚点。
    功能：返回工具当前轮次应使用的本地日期，供相对期间和默认交易日推导复用。
    实现逻辑：统一从 `datetime.now().date()` 取值，避免多处重复读取系统时间导致测试不可控。
    可调参数：当前无显式参数；如需固定时间，测试可通过 monkeypatch 覆盖本函数。
    默认参数及原因：默认直接读取本地日期，原因是相对期间语义已经约定为“按当前日期锚点”解析。
    """

    return datetime.now().date()


def _normalize_period_token(token: str) -> str:
    """
    目的：把 agent 传入的期间别名先规整成统一 token，减少后续语义匹配分叉。
    功能：去掉外围包裹符号、统一全半角、压缩空白，并把常见英文别名归整到稳定写法。
    实现逻辑：先做 NFKC 归一化和首尾清洗，再移除空白与多余符号，最后保留中文词和关键分隔符用于语义解析。
    可调参数：`token` 为原始期间文本，可来自 JSON、CSV、自然语言别名或项目占位符。
    默认参数及原因：空输入返回空串，原因是上层解析链会统一把空 token 视为无效值并给出明确错误。
    """

    normalized = unicodedata.normalize("NFKC", str(token or "")).strip()
    normalized = normalized.strip("`'\"")
    if normalized.startswith("{") and normalized.endswith("}"):
        normalized = normalized[1:-1].strip()
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = normalized.replace("—", "-").replace("–", "-").replace("−", "-")
    normalized = normalized.replace("／", "/").replace("\\", "/")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.upper()
    normalized = normalized.replace("TRAILINGTWELVEMONTHS", "TTM")
    normalized = normalized.replace("LASTTWELVEMONTHS", "TTM")
    normalized = normalized.replace("LATESTTWELVEMONTHS", "TTM")
    normalized = normalized.replace("ROLLINGTWELVEMONTHS", "TTM")
    normalized = normalized.replace("ROLLING12MONTHS", "TTM")
    normalized = normalized.replace("TRAILING12MONTHS", "TTM")
    normalized = normalized.replace("LATEST12MONTHS", "TTM")
    normalized = normalized.replace("FYMINUS", "FY-")
    normalized = normalized.replace("FQMINUS", "FQ-")
    normalized = normalized.replace("_", "")
    return normalized


def _coerce_period_year(raw_year: str) -> int:
    """
    目的：把 2 位或 4 位年份文本统一成 4 位公历年份。
    功能：兼容 `24A`、`FY24` 和 `2024A` 这类混合写法。
    实现逻辑：先转整数，再把 2 位年份统一补到 2000 年以后。
    可调参数：`raw_year` 为正则提取出的年份文本。
    默认参数及原因：两位数一律补成 20xx，原因是当前仓库处理的同行数据全部落在现代上市公司语境内。
    """

    year = int(raw_year)
    return year + 2000 if year < 100 else year


def _format_compact_date(value: date) -> str:
    """
    目的：统一输出 Tushare 可直接识别的紧凑日期字符串。
    功能：把 `date` 对象转换成 `YYYYMMDD`。
    实现逻辑：固定使用 `%Y%m%d` 格式，避免不同调用点出现多种日期语法。
    可调参数：`value` 为已经验证过的日期对象。
    默认参数及原因：当前无默认值，原因是 provider-friendly 日期必须由明确日期对象生成。
    """

    return value.strftime("%Y%m%d")


def _build_standard_period_meta(year: int, kind: str) -> dict[str, Any]:
    """
    目的：集中构造标准报告期的基础元数据，避免多处重复拼装年、季度和日期字段。
    功能：返回标准期间的 canonical label、结束日、默认披露日和 period kind。
    实现逻辑：按仓库统一约定支持 `Q1/H1/Q3/A` 四种标准期，并在年报场景下把披露日推进到下一年四月底。
    可调参数：`year` 为报告期年份，`kind` 为标准期种类。
    默认参数及原因：不接受其它 kind，原因是当前工具只按 Tushare 稳定可对齐的标准披露期做归一化。
    """

    end_month, end_day = STANDARD_PERIOD_END_MONTH_DAY[kind]
    disclosure_month, disclosure_day = STANDARD_PERIOD_DISCLOSURE_MONTH_DAY[kind]
    end_date = date(year, end_month, end_day)
    disclosure_year = year + 1 if kind == "A" else year
    disclosure_date = date(disclosure_year, disclosure_month, disclosure_day)
    if kind == "A":
        label = f"{year}A"
    elif kind == "H1":
        label = f"{year}H1A"
    else:
        label = f"{year}{kind}A"
    return {
        "year": year,
        "kind": kind,
        "label": label,
        "end_date": end_date,
        "disclosure_date": disclosure_date,
    }


def _latest_ended_standard_period(today: date | None = None) -> dict[str, Any]:
    """
    目的：给相对期间语义提供“截至今天最近一个已结束标准报告期”的锚点。
    功能：从 `Q1/H1/Q3/A` 四类标准期中找出结束日不晚于当前日期的最新期间。
    实现逻辑：扫描当前年和上一年的候选期，按结束日倒序选出最近一个已结束期间。
    可调参数：`today` 允许测试显式传入固定日期。
    默认参数及原因：默认读取 `_current_local_date()`，原因是生产逻辑按当前日期锚点解析相对期间。
    """

    anchor_date = today or _current_local_date()
    candidates: list[dict[str, Any]] = []
    for year in range(anchor_date.year - 1, anchor_date.year + 1):
        for kind in STANDARD_PERIOD_KIND_SEQUENCE:
            meta = _build_standard_period_meta(year, kind)
            if meta["end_date"] <= anchor_date:
                candidates.append(meta)
    if not candidates:
        raise ValueError("Unable to determine the latest ended standard reporting period.")
    return max(candidates, key=lambda item: item["end_date"])


def _latest_disclosed_standard_period(today: date | None = None) -> dict[str, Any]:
    """
    目的：给 TTM 这类滚动口径提供“截至今天最稳定可得”的已披露标准报告期。
    功能：找出披露日不晚于当前日期的最新标准报告期，避免 TTM 锚到尚未稳定披露的年报期。
    实现逻辑：扫描当前年、上一年和上上年的候选期，按披露日倒序选出最近一个已披露期间。
    可调参数：`today` 允许测试显式传入固定日期。
    默认参数及原因：默认读取 `_current_local_date()`，原因是滚动口径需要围绕运行当天的稳定披露边界计算。
    """

    anchor_date = today or _current_local_date()
    candidates: list[dict[str, Any]] = []
    for year in range(anchor_date.year - 2, anchor_date.year + 1):
        for kind in STANDARD_PERIOD_KIND_SEQUENCE:
            meta = _build_standard_period_meta(year, kind)
            if meta["disclosure_date"] <= anchor_date:
                candidates.append(meta)
    if not candidates:
        raise ValueError("Unable to determine the latest disclosed standard reporting period.")
    return max(candidates, key=lambda item: item["disclosure_date"])


def _latest_ended_annual_period(today: date | None = None) -> dict[str, Any]:
    """
    目的：给 `FY-1/FY1` 这类财年相对别名提供统一的年度锚点。
    功能：返回截至当前日期最近一个已结束年度报告期。
    实现逻辑：复用年度元数据构造逻辑，只在年度候选集中按结束日倒序选取最新值。
    可调参数：`today` 允许测试固定年度锚点。
    默认参数及原因：默认读取 `_current_local_date()`，原因是财年递推语义已经锁定为按当前日期锚点处理。
    """

    anchor_date = today or _current_local_date()
    candidates: list[dict[str, Any]] = []
    for year in range(anchor_date.year - 1, anchor_date.year + 1):
        meta = _build_standard_period_meta(year, "A")
        if meta["end_date"] <= anchor_date:
            candidates.append(meta)
    if not candidates:
        raise ValueError("Unable to determine the latest ended annual reporting period.")
    return max(candidates, key=lambda item: item["end_date"])


def _previous_standard_period(meta: dict[str, Any]) -> dict[str, Any]:
    """
    目的：为“上一期/前一期/FQ-1”提供稳定的标准期递推规则。
    功能：返回给定标准期之前的一个可比标准期。
    实现逻辑：按 `Q1 -> 上一年A`、`H1 -> 当年Q1`、`Q3 -> 当年H1`、`A -> 当年Q3` 的顺序回退。
    可调参数：`meta` 为由标准期间元数据构造器返回的期间对象。
    默认参数及原因：当前无其它回退路径，原因是仓库已经把标准报告期边界固定为四种常见披露口径。
    """

    kind = str(meta["kind"])
    year = int(meta["year"])
    if kind == "Q1":
        return _build_standard_period_meta(year - 1, "A")
    if kind == "H1":
        return _build_standard_period_meta(year, "Q1")
    if kind == "Q3":
        return _build_standard_period_meta(year, "H1")
    return _build_standard_period_meta(year, "Q3")


def _prior_year_comparable_period(meta: dict[str, Any]) -> dict[str, Any]:
    """
    目的：给“上年同期/去年同期”提供同比可比口径的确定性映射。
    功能：返回与当前标准期同口径、但年份回退一年的期间。
    实现逻辑：年度保持年度、季度保持同季度、中期保持中期，只调整年份。
    可调参数：`meta` 为当前标准期间元数据。
    默认参数及原因：默认只做同口径同比回退，原因是“上年同期”语义本质上要求口径不变。
    """

    return _build_standard_period_meta(int(meta["year"]) - 1, str(meta["kind"]))


def _canonical_label_from_statement_period(statement_period: str) -> str:
    """
    目的：把明确的报告期结束日反推回 canonical label，统一工具输出标签口径。
    功能：识别 `0331/0630/0930/1231` 四种标准结束日，并返回 `Q1/H1/Q3/A` 对应标签。
    实现逻辑：优先校验日期格式，再按末四位月日映射到标准报告期种类。
    可调参数：`statement_period` 为 `YYYYMMDD` 紧凑日期字符串。
    默认参数及原因：不识别非标准月日，原因是当前工具仅对稳定标准报告期做语义归一化。
    """

    if not PEER_COMPACT_DATE_PATTERN.match(statement_period or ""):
        return ""
    year = int(statement_period[:4])
    month_day = statement_period[4:]
    if month_day == "0331":
        return f"{year}Q1A"
    if month_day == "0630":
        return f"{year}H1A"
    if month_day == "0930":
        return f"{year}Q3A"
    if month_day == "1231":
        return f"{year}A"
    return ""


def _build_period_resolution(
    *,
    label: str,
    statement_period: str,
    trade_date: str,
    input_label: str,
    source_token: str,
    resolution_source: str,
    resolution_note: str,
    period_kind: str,
    is_forecast: bool = False,
) -> dict[str, Any]:
    """
    目的：统一构造 peer data 工具内部使用的 canonical period spec。
    功能：把标签、日期口径、原始输入和解析说明封装成单个期间对象。
    实现逻辑：固定输出 `label/statement_period/trade_date/input_label/source_token/resolution_*` 这组字段，减少后续调用点分叉。
    可调参数：各字段都由上游解析函数显式传入，便于测试按场景精确断言。
    默认参数及原因：`is_forecast` 默认 `False`，原因是大多数 period spec 仍然是历史或滚动口径。
    """

    return {
        "label": label,
        "statement_period": statement_period,
        "trade_date": trade_date,
        "input_label": input_label,
        "source_token": source_token,
        "resolution_source": resolution_source,
        "resolution_note": resolution_note,
        "period_kind": period_kind,
        "is_forecast": is_forecast,
    }


def _build_standard_period_resolution(
    meta: dict[str, Any],
    *,
    input_label: str,
    source_token: str,
    resolution_source: str,
    resolution_note: str,
    explicit_statement_period: str = "",
    explicit_trade_date: str = "",
) -> dict[str, Any]:
    """
    目的：把标准报告期元数据转换成 Tushare 可直接消费的 canonical period spec。
    功能：输出标准标签、报告期结束日和默认交易日，并允许显式日期覆盖默认值。
    实现逻辑：默认交易日按标准披露日推导，再与当前日期做截断；若外部已给定显式日期，则优先保留显式值。
    可调参数：`meta` 为标准期间元数据，其余参数用于补充原始输入和解析说明。
    默认参数及原因：显式日期默认留空，原因是绝大多数 alias 只提供标签，不会同时携带完整 provider 日期。
    """

    today = _current_local_date()
    default_trade_date = min(meta["disclosure_date"], today)
    statement_period = explicit_statement_period or _format_compact_date(meta["end_date"])
    trade_date = explicit_trade_date or _format_compact_date(default_trade_date)
    return _build_period_resolution(
        label=str(meta["label"]),
        statement_period=statement_period,
        trade_date=trade_date,
        input_label=input_label,
        source_token=source_token,
        resolution_source=resolution_source,
        resolution_note=resolution_note,
        period_kind=str(meta["kind"]),
    )


def _build_forecast_period_resolution(
    year: int,
    *,
    input_label: str,
    source_token: str,
    resolution_source: str,
    resolution_note: str,
) -> dict[str, Any]:
    """
    目的：给预测期 alias 构造稳定输出，同时避免工具误把预测列当成历史财报口径请求。
    功能：输出 canonical 预测标签，并显式保留空的 `statement_period/trade_date`。
    实现逻辑：预测期只保留 `YYYYE` 标签和说明字段，交由下游按“无信息/未返回”处理，不伪造 provider 日期。
    可调参数：`year` 为预测年度，其余参数用于记录原始输入和解析来源。
    默认参数及原因：provider 日期默认留空，原因是当前工具不负责为预测期臆造财报或市场快照日期。
    """

    return _build_period_resolution(
        label=f"{year}E",
        statement_period="",
        trade_date="",
        input_label=input_label,
        source_token=source_token,
        resolution_source=resolution_source,
        resolution_note=resolution_note,
        period_kind="FORECAST",
        is_forecast=True,
    )


def _build_ttm_period_resolution(
    *,
    input_label: str,
    source_token: str,
    resolution_source: str,
    resolution_note: str,
) -> dict[str, Any]:
    """
    目的：把 `TTM/LTM/最近12个月` 这类滚动期间统一映射到稳定可得的 provider 口径。
    功能：输出 `TTM` 标签，并把底层报告期锚到最近一个已披露标准报告期。
    实现逻辑：滚动期的 `statement_period` 使用最近已披露标准期的结束日，`trade_date` 使用当前日期，兼顾财报和市场快照的稳定性。
    可调参数：`input_label`、`source_token` 和解析来源字段用于保留原始输入上下文。
    默认参数及原因：当前无显式日期覆盖，原因是滚动口径的目标就是统一收敛到最近稳定可得的 provider 视图。
    """

    today = _current_local_date()
    disclosed_meta = _latest_disclosed_standard_period(today)
    resolution = _build_period_resolution(
        label="TTM",
        statement_period=_format_compact_date(disclosed_meta["end_date"]),
        trade_date=_format_compact_date(today),
        input_label=input_label,
        source_token=source_token,
        resolution_source=resolution_source,
        resolution_note=(
            f"{resolution_note}; ttm_anchor={disclosed_meta['label']}"
            if resolution_note
            else f"ttm_anchor={disclosed_meta['label']}"
        ),
        period_kind="TTM",
    )
    resolution["ttm_anchor_label"] = disclosed_meta["label"]
    return resolution


def _resolve_standard_period_alias(token: str) -> dict[str, Any] | None:
    """
    目的：把绝对期间、项目占位符和相对自然语言 alias 解析成标准报告期口径。
    功能：支持年度、季度、中期、滚动期以及围绕当前日期锚点的相对期间递推。
    实现逻辑：先匹配强语义 alias，再处理项目占位符和绝对标签，最后处理自然语言相对期间。
    可调参数：`token` 为预处理后的单个 alias。
    默认参数及原因：无法识别时返回 `None`，原因是上层需要保留对真正未知 token 的明确报错边界。
    """

    today = _current_local_date()
    ended_anchor = _latest_ended_standard_period(today)
    annual_anchor = _latest_ended_annual_period(today)

    if token in TTM_ALIAS_TOKENS:
        return _build_ttm_period_resolution(
            input_label=token,
            source_token=token,
            resolution_source="rolling_alias",
            resolution_note="resolved from rolling-period alias",
        )

    if token in CURRENT_PERIOD_ALIAS_TOKENS:
        return _build_standard_period_resolution(
            ended_anchor,
            input_label=token,
            source_token=token,
            resolution_source="current_period_alias",
            resolution_note="resolved from current-period alias using latest ended standard period",
        )

    if token in PREVIOUS_PERIOD_ALIAS_TOKENS:
        return _build_standard_period_resolution(
            _previous_standard_period(ended_anchor),
            input_label=token,
            source_token=token,
            resolution_source="previous_period_alias",
            resolution_note="resolved from previous-period alias using latest ended standard period",
        )

    if token in PRIOR_YEAR_COMPARABLE_ALIAS_TOKENS:
        return _build_standard_period_resolution(
            _prior_year_comparable_period(ended_anchor),
            input_label=token,
            source_token=token,
            resolution_source="prior_year_comparable_alias",
            resolution_note="resolved from prior-year comparable alias using latest ended standard period",
        )

    if token in PREVIOUS_FISCAL_YEAR_ALIAS_TOKENS:
        return _build_standard_period_resolution(
            _build_standard_period_meta(int(annual_anchor["year"]) - 1, "A"),
            input_label=token,
            source_token=token,
            resolution_source="previous_fiscal_year_alias",
            resolution_note="resolved from previous-fiscal-year alias using latest ended annual period",
        )

    if token in NEXT_FISCAL_YEAR_ALIAS_TOKENS:
        return _build_forecast_period_resolution(
            int(annual_anchor["year"]) + 1,
            input_label=token,
            source_token=token,
            resolution_source="next_fiscal_year_alias",
            resolution_note="resolved from next-fiscal-year alias using latest ended annual period",
        )

    if token in SECOND_NEXT_FISCAL_YEAR_ALIAS_TOKENS:
        return _build_forecast_period_resolution(
            int(annual_anchor["year"]) + 2,
            input_label=token,
            source_token=token,
            resolution_source="second_next_fiscal_year_alias",
            resolution_note="resolved from second-next-fiscal-year alias using latest ended annual period",
        )

    fy_relative_match = re.match(r"^FY-(?P<offset>[1-9])$", token)
    if fy_relative_match:
        offset = int(fy_relative_match.group("offset"))
        return _build_standard_period_resolution(
            _build_standard_period_meta(int(annual_anchor["year"]) - offset, "A"),
            input_label=token,
            source_token=token,
            resolution_source="relative_fiscal_year_alias",
            resolution_note="resolved from relative fiscal-year alias using latest ended annual period",
        )

    fy_forward_match = re.match(r"^FY(?P<offset>[1-9])$", token)
    if fy_forward_match:
        offset = int(fy_forward_match.group("offset"))
        return _build_forecast_period_resolution(
            int(annual_anchor["year"]) + offset,
            input_label=token,
            source_token=token,
            resolution_source="forward_fiscal_year_alias",
            resolution_note="resolved from forward fiscal-year alias using latest ended annual period",
        )

    fq_relative_match = re.match(r"^FQ-(?P<offset>[1-9])$", token)
    if fq_relative_match:
        offset = int(fq_relative_match.group("offset"))
        current_meta = dict(ended_anchor)
        for _ in range(offset):
            current_meta = _previous_standard_period(current_meta)
        return _build_standard_period_resolution(
            current_meta,
            input_label=token,
            source_token=token,
            resolution_source="relative_standard_period_alias",
            resolution_note="resolved from relative standard-period alias using latest ended standard period",
        )

    period_match = PEER_STANDARD_PERIOD_PATTERN.match(token)
    if period_match:
        year = _coerce_period_year(period_match.group("year"))
        raw_kind = period_match.group("kind")
        kind = "H1" if raw_kind == "Q2" else "A" if raw_kind == "Q4" else raw_kind
        return _build_standard_period_resolution(
            _build_standard_period_meta(year, kind),
            input_label=token,
            source_token=token,
            resolution_source="absolute_standard_period",
            resolution_note="resolved from absolute standard-period label",
        )

    annual_label_match = PEER_PERIOD_LABEL_PATTERN.match(token)
    if annual_label_match:
        year = _coerce_period_year(annual_label_match.group("year"))
        suffix = annual_label_match.group("suffix")
        if suffix == "A":
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "A"),
                input_label=token,
                source_token=token,
                resolution_source="absolute_annual_period",
                resolution_note="resolved from absolute annual-period label",
            )
        return _build_forecast_period_resolution(
            year,
            input_label=token,
            source_token=token,
            resolution_source="absolute_forecast_period",
            resolution_note="resolved from absolute forecast-period label",
        )

    annual_with_prefix_match = PEER_FY_WITH_PREFIX_PATTERN.match(token)
    if annual_with_prefix_match:
        year = _coerce_period_year(annual_with_prefix_match.group("year"))
        suffix = annual_with_prefix_match.group("suffix") or "A"
        if suffix == "A":
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "A"),
                input_label=token,
                source_token=token,
                resolution_source="absolute_annual_alias",
                resolution_note="resolved from FY-prefixed annual alias",
            )
        return _build_forecast_period_resolution(
            year,
            input_label=token,
            source_token=token,
            resolution_source="absolute_forecast_alias",
            resolution_note="resolved from FY-prefixed forecast alias",
        )

    annual_with_suffix_match = PEER_YEAR_WITH_FY_SUFFIX_PATTERN.match(token)
    if annual_with_suffix_match:
        year = _coerce_period_year(annual_with_suffix_match.group("year"))
        suffix = annual_with_suffix_match.group("suffix") or "A"
        if suffix == "A":
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "A"),
                input_label=token,
                source_token=token,
                resolution_source="absolute_annual_alias",
                resolution_note="resolved from FY-suffixed annual alias",
            )
        return _build_forecast_period_resolution(
            year,
            input_label=token,
            source_token=token,
            resolution_source="absolute_forecast_alias",
            resolution_note="resolved from FY-suffixed forecast alias",
        )

    chinese_match = PEER_YYYY_CHINESE_PATTERN.match(token)
    if chinese_match:
        year = _coerce_period_year(chinese_match.group("year"))
        chinese_label = chinese_match.group("label")
        if chinese_label in {"年报", "年度", "年度报告"}:
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "A"),
                input_label=token,
                source_token=token,
                resolution_source="chinese_annual_alias",
                resolution_note="resolved from Chinese annual alias",
            )
        if chinese_label in {"一季报", "第一季度", "第一季度报告"}:
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "Q1"),
                input_label=token,
                source_token=token,
                resolution_source="chinese_quarter_alias",
                resolution_note="resolved from Chinese Q1 alias",
            )
        if chinese_label in {"半年报", "中报", "半年度", "半年度报告"}:
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "H1"),
                input_label=token,
                source_token=token,
                resolution_source="chinese_midyear_alias",
                resolution_note="resolved from Chinese H1 alias",
            )
        if chinese_label in {"三季报", "第三季度", "第三季度报告"}:
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "Q3"),
                input_label=token,
                source_token=token,
                resolution_source="chinese_quarter_alias",
                resolution_note="resolved from Chinese Q3 alias",
            )
        if chinese_label in {"四季报", "第四季度", "第四季度报告"}:
            return _build_standard_period_resolution(
                _build_standard_period_meta(year, "A"),
                input_label=token,
                source_token=token,
                resolution_source="chinese_q4_alias",
                resolution_note="resolved from Chinese Q4 alias as annual period",
            )
        if chinese_label == "预测":
            return _build_forecast_period_resolution(
                year,
                input_label=token,
                source_token=token,
                resolution_source="chinese_forecast_alias",
                resolution_note="resolved from Chinese forecast alias",
            )

    return None


def _normalize_compact_date_string(value: str, *, field_name: str) -> str:
    """
    目的：校验并标准化显式传入的 provider 日期，避免工具继续传递非法日期字符串。
    功能：返回清洗后的 `YYYYMMDD`，并把未来交易日截断到当前日期。
    实现逻辑：先做 NFKC 和空白清洗，再校验 8 位数字格式；对于 `trade_date` 额外做未来日期截断。
    可调参数：`value` 为原始日期文本，`field_name` 用于区分不同字段的校验和报错信息。
    默认参数及原因：空字符串直接返回空串，原因是显式日期本身就是可选增强信息。
    """

    cleaned = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not cleaned:
        return ""
    if not PEER_COMPACT_DATE_PATTERN.match(cleaned):
        raise ValueError(f"`{field_name}` must use YYYYMMDD when provided.")
    if field_name != "trade_date":
        return cleaned
    parsed = datetime.strptime(cleaned, "%Y%m%d").date()
    return _format_compact_date(min(parsed, _current_local_date()))


def _resolve_peer_period_item(item: Any) -> dict[str, Any]:
    """
    目的：把 period 输入中的单个元素解析成 canonical period spec。
    功能：兼容字符串 alias 和显式 object 两种输入形式，并统一补齐 provider-friendly 日期字段。
    实现逻辑：优先使用显式 object 里的日期字段，缺失时再通过 label alias 推导；若 label 和日期都无法解释则明确报错。
    可调参数：`item` 可为字符串、字典或其它可转成字符串的单值。
    默认参数及原因：字典中的 `label` 仍建议提供，原因是它能帮助工具保留原始输入轨迹和解析说明。
    """

    if isinstance(item, dict):
        raw_label = " ".join(str(item.get("label", "") or "").split()).strip()
        explicit_statement_period = _normalize_compact_date_string(
            str(item.get("statement_period", "") or ""),
            field_name="statement_period",
        )
        explicit_trade_date = _normalize_compact_date_string(
            str(item.get("trade_date", "") or ""),
            field_name="trade_date",
        )
        source_token = _normalize_period_token(raw_label)
        resolution = _resolve_standard_period_alias(source_token) if source_token else None
        canonical_label = (
            _canonical_label_from_statement_period(explicit_statement_period)
            if explicit_statement_period
            else ""
        )
        if resolution is None and canonical_label:
            source_kind = "A"
            if canonical_label.endswith("Q1A"):
                source_kind = "Q1"
            elif canonical_label.endswith("H1A"):
                source_kind = "H1"
            elif canonical_label.endswith("Q3A"):
                source_kind = "Q3"
            resolution = _build_standard_period_resolution(
                _build_standard_period_meta(int(canonical_label[:4]), source_kind),
                input_label=raw_label or canonical_label,
                source_token=source_token or canonical_label,
                resolution_source="explicit_period_object",
                resolution_note="resolved from explicit period object",
                explicit_statement_period=explicit_statement_period,
                explicit_trade_date=explicit_trade_date,
            )
        if resolution is None and raw_label:
            raise ValueError(f"Unsupported period label `{raw_label}`.")
        if resolution is None:
            raise ValueError("Each item in `periods` must include a valid `label` or `statement_period`.")
        resolved = dict(resolution)
        if canonical_label:
            resolved["label"] = canonical_label
        if explicit_statement_period:
            resolved["statement_period"] = explicit_statement_period
        if explicit_trade_date:
            resolved["trade_date"] = explicit_trade_date
        resolved["input_label"] = raw_label or resolved["label"]
        resolved["source_token"] = source_token or resolved["label"]
        resolved["resolution_source"] = "explicit_period_object"
        resolved["resolution_note"] = "resolved from explicit period object"
        return resolved

    raw_label = " ".join(str(item or "").split()).strip()
    if not raw_label:
        raise ValueError("Each item in `periods` must include a non-empty label token.")
    source_token = _normalize_period_token(raw_label)
    resolution = _resolve_standard_period_alias(source_token)
    if resolution is None:
        raise ValueError(f"Unsupported period token `{raw_label}`.")
    resolved = dict(resolution)
    resolved["input_label"] = raw_label
    resolved["source_token"] = source_token
    return resolved


def _merge_duplicate_period_specs(period_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    目的：把不同 alias 归一后产生的同标签重复期间收敛成单个 period spec，避免后续结果字典被覆盖。
    功能：按首次出现顺序去重，并用后续元素补齐缺失日期或说明字段。
    实现逻辑：以 canonical label 为主键保留第一条记录，再把后续同标签记录的非空字段并入已有结果。
    可调参数：`period_specs` 为解析后的期间列表。
    默认参数及原因：默认保留首个标签顺序，原因是 agent 传入的列顺序通常直接对应后续 Markdown 表头顺序。
    """

    merged_by_label: dict[str, dict[str, Any]] = {}
    ordered_labels: list[str] = []
    for period_spec in period_specs:
        label = str(period_spec["label"])
        existing = merged_by_label.get(label)
        if existing is None:
            merged_by_label[label] = dict(period_spec)
            ordered_labels.append(label)
            continue
        for field_name in (
            "statement_period",
            "trade_date",
            "input_label",
            "source_token",
            "resolution_source",
            "resolution_note",
        ):
            if not existing.get(field_name) and period_spec.get(field_name):
                existing[field_name] = period_spec[field_name]
    return [merged_by_label[label] for label in ordered_labels]


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
            "期间列表。推荐使用 JSON object-array，也兼容单 object、string-array、CSV 和换行 token 列表。"
            "可以直接传常见 alias，例如 `FY-1`、`FQ0/FY0`、`2025H1A`、`2024年报`、`TTM` 或 `明年预测`。"
            "显式 object 示例：`[{\"label\":\"FY-1\",\"statement_period\":\"20241231\",\"trade_date\":\"20250415\"}]`。"
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


def _parse_peer_periods_input(periods: str) -> list[dict[str, Any]]:
    """
    目的：把 peer info 的期间输入解析成统一的 canonical period spec 列表。
    功能：兼容 JSON object-array、单 object、string-array、CSV、换行 token 列表和常见期间别名。
    实现逻辑：优先解析 JSON；失败时回退到受控分隔符拆分，再逐项做 period resolution 和去重。
    可调参数：`periods` 原始字符串。
    默认参数及原因：留空时默认回退到 `TTM`，原因是 peer info 场景更需要最近滚动口径的稳定 provider 日期。
    """

    cleaned = (periods or "").strip()
    if not cleaned:
        return [
            _build_ttm_period_resolution(
                input_label="TTM",
                source_token="TTM",
                resolution_source="default_period",
                resolution_note="defaulted to rolling TTM period because `periods` was empty",
            )
        ]

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            raw_items = parsed
        else:
            raw_items = [parsed]
    except json.JSONDecodeError:
        raw_items = [
            part.strip().lstrip("-*•").strip()
            for part in re.split(r"[\r\n,，;；]+", cleaned)
            if part.strip().lstrip("-*•").strip()
        ]

    if not raw_items:
        raise ValueError("`periods` was provided but no valid period items were parsed.")

    normalized_periods = [_resolve_peer_period_item(item) for item in raw_items]
    return _merge_duplicate_period_specs(normalized_periods)


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
        supported_metric_keys = [
            metric_key for metric_key in metric_keys if metric_key not in unsupported_metrics
        ]

        for identifier in company_identifiers:
            resolved = _resolve_company_identifier(identifier)
            ts_code = str(resolved.get("ts_code", ""))
            company_periods: dict[str, Any] = {}

            for period_spec in period_specs:
                label = period_spec["label"]
                trade_date = period_spec["trade_date"] or None
                statement_period = period_spec["statement_period"] or None
                input_label = str(period_spec.get("input_label", label) or label)
                source_token = str(period_spec.get("source_token", label) or label)
                resolution_source = str(period_spec.get("resolution_source", "") or "")
                resolution_note = str(period_spec.get("resolution_note", "") or "")
                period_kind = str(period_spec.get("period_kind", "") or "")
                is_forecast = bool(period_spec.get("is_forecast", False))
                interface_errors: list[str] = []
                daily_basic: dict[str, Any] | None = None
                fina_indicator: dict[str, Any] | None = None
                income_statement: dict[str, Any] | None = None
                balance_sheet: dict[str, Any] | None = None
                cashflow_statement: dict[str, Any] | None = None
                market_snapshot: dict[str, Any] = {}

                if not is_forecast:
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
                else:
                    metric_values = {metric_key: None for metric_key in metric_keys}

                missing_metrics = [
                    metric_key
                    for metric_key in supported_metric_keys
                    if metric_values.get(metric_key) is None
                ]
                effective_resolution_note = resolution_note
                if is_forecast:
                    effective_resolution_note = (
                        f"{resolution_note}; provider queries skipped for forecast period"
                        if resolution_note
                        else "provider queries skipped for forecast period"
                    )
                period_payload = {
                    "label": label,
                    "statement_period": statement_period,
                    "trade_date": trade_date,
                    "input_label": input_label,
                    "source_token": source_token,
                    "resolution_source": resolution_source,
                    "resolution_note": effective_resolution_note,
                    "period_kind": period_kind,
                    "is_forecast": is_forecast,
                    "provider_query_skipped": is_forecast,
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
