from __future__ import annotations

import json
import re
from datetime import date

from automated_research_report_generator.tools import tushare_tools as tushare_tools_module
from automated_research_report_generator.tools.tushare_tools import (
    TusharePeerDataTool,
    _normalize_market_value_to_cny,
    _parse_companies_input,
    _parse_peer_periods_input,
    assess_tushare_peer_data_tool_coverage,
    assess_tushare_valuation_tool_peer_coverage,
)


def test_parse_companies_input_supports_json_and_csv() -> None:
    """
    目的：锁住 Tushare 工具的公司列表输入兼容性。
    功能：验证 JSON 数组和逗号分隔字符串都能被解析成统一列表。
    实现逻辑：分别传入两种最常见格式，再比较标准化结果。
    可调参数：无。
    默认参数及原因：只覆盖 valuation 和 peer info 场景的主路径输入，原因是这已经能防住最关键的回归。
    """

    assert _parse_companies_input('["宁德时代", "300750.SZ"]') == ["宁德时代", "300750.SZ"]
    assert _parse_companies_input("宁德时代, 比亚迪") == ["宁德时代", "比亚迪"]


def test_normalize_market_value_to_cny_converts_wan_to_yuan() -> None:
    """
    目的：锁住 Tushare 市值字段的单位换算。
    功能：验证 `daily_basic.total_mv` 从“万元”转换为“元”的逻辑。
    实现逻辑：使用简单数值样例直接断言乘数是否正确。
    可调参数：无。
    默认参数及原因：样例保持简单，原因是更容易快速定位单位换算是否被改坏。
    """

    assert _normalize_market_value_to_cny(123.45) == 1_234_500.0


def test_assess_tushare_valuation_tool_peer_coverage_exposes_gaps() -> None:
    """
    目的：锁住 valuation 工具对 peer info 指标集的能力边界。
    功能：验证审计结果会明确标出直接覆盖、可推导和不支持的指标。
    实现逻辑：读取能力矩阵后，断言几个代表性指标和总览结论。
    可调参数：无。
    默认参数及原因：只验证代表性指标，原因是这里要防的是能力口径回归，而不是重复穷举整张矩阵。
    """

    audit = assess_tushare_valuation_tool_peer_coverage()

    assert audit["summary"] == "current_valuation_tool_is_not_sufficient_for_full_peer_info_pack"
    assert audit["coverage"]["revenue_amount"]["status"] == "direct"
    assert audit["coverage"]["ev_ebitda"]["status"] == "derived"
    assert audit["coverage"]["selling_expense_ratio"]["status"] == "direct"
    assert audit["coverage"]["asset_turnover"]["status"] == "direct"
    assert audit["coverage"]["accounts_payable_turnover"]["status"] == "unsupported"


def test_assess_tushare_peer_data_tool_coverage_matches_current_peer_scope() -> None:
    """
    目的：锁住同行数据工具自身的能力边界。
    功能：验证当前同行工具已支持的费用率和周转类指标不会被误判为不支持。
    实现逻辑：读取能力矩阵后，断言代表性指标分类结果。
    可调参数：无。
    默认参数及原因：只验证关键指标，原因是这里关心的是能力口径是否被错误复用。
    """

    audit = assess_tushare_peer_data_tool_coverage()

    assert audit["summary"] == "current_peer_data_tool_has_partial_gaps_for_peer_info_pack"
    assert audit["coverage"]["selling_expense_ratio"]["status"] == "direct"
    assert audit["coverage"]["ev_ebitda"]["status"] == "derived"
    assert audit["coverage"]["asset_turnover"]["status"] == "direct"
    assert audit["coverage"]["accounts_payable_turnover"]["status"] == "unsupported"


def test_parse_peer_periods_input_defaults_to_ttm_and_supports_explicit_object(
    monkeypatch,
) -> None:
    """
    目的：锁住 `periods` 为空时的默认回退和显式 object 输入的兼容性。
    功能：验证默认值不再回退到旧的 `LATEST`，并且单个 object 能被归一化为 canonical period spec。
    实现逻辑：固定当前日期后，分别断言空输入和显式 object 输入的解析结果。
    可调参数：通过 monkeypatch 固定当前日期。
    默认参数及原因：锚点固定为 2026-04-16，原因是这能稳定覆盖年报未披露、Q3 已披露的边界场景。
    """

    monkeypatch.setattr(
        tushare_tools_module,
        "_current_local_date",
        lambda: date(2026, 4, 16),
    )

    default_specs = _parse_peer_periods_input("")
    assert len(default_specs) == 1
    assert default_specs[0]["label"] == "TTM"
    assert default_specs[0]["statement_period"] == "20250930"
    assert default_specs[0]["trade_date"] == "20260416"
    assert default_specs[0]["period_kind"] == "TTM"
    assert default_specs[0]["is_forecast"] is False
    assert "ttm_anchor=2025Q3A" in default_specs[0]["resolution_note"]

    explicit_specs = _parse_peer_periods_input(
        '{"label":"FY-1","statement_period":"20241231","trade_date":"20250415"}'
    )
    assert explicit_specs == [
        {
            "label": "2024A",
            "statement_period": "20241231",
            "trade_date": "20250415",
            "input_label": "FY-1",
            "source_token": "FY-1",
            "resolution_source": "explicit_period_object",
            "resolution_note": "resolved from explicit period object",
            "period_kind": "A",
            "is_forecast": False,
        }
    ]


def test_parse_peer_periods_input_resolves_relative_aliases_from_current_anchor(
    monkeypatch,
) -> None:
    """
    目的：锁住相对期间 alias 的锚点语义。
    功能：验证 `FQ0/FY0`、`上一期`、`上年同期`、`FY-1/FY1/FY2/FY-3` 都围绕当前日期锚点解析。
    实现逻辑：固定当前日期为 2026-04-16，逐个断言解析后的 canonical 标签。
    可调参数：通过 monkeypatch 固定当前日期。
    默认参数及原因：选择 2026-04-16，原因是它能区分“最近已结束期间”和“最近已披露期间”。
    """

    monkeypatch.setattr(
        tushare_tools_module,
        "_current_local_date",
        lambda: date(2026, 4, 16),
    )

    assert _parse_peer_periods_input("FQ0/FY0")[0]["label"] == "2026Q1A"
    assert _parse_peer_periods_input("最近一期")[0]["label"] == "2026Q1A"
    assert _parse_peer_periods_input("上一期")[0]["label"] == "2025A"
    assert _parse_peer_periods_input("上年同期")[0]["label"] == "2025Q1A"
    assert _parse_peer_periods_input("上一财年")[0]["label"] == "2024A"
    assert _parse_peer_periods_input("FY-1")[0]["label"] == "2024A"
    assert _parse_peer_periods_input("{FY-3}")[0]["label"] == "2022A"
    assert _parse_peer_periods_input("FY1")[0]["label"] == "2026E"
    assert _parse_peer_periods_input("fy_2")[0]["label"] == "2027E"
    assert _parse_peer_periods_input("明年预测")[0]["label"] == "2026E"


def test_parse_peer_periods_input_supports_absolute_quarter_and_rolling_aliases(
    monkeypatch,
) -> None:
    """
    目的：锁住绝对期间、季度/中期别名和滚动期间别名的归一化。
    功能：验证年度、中期、季度和滚动口径都会落到 provider-friendly 的日期字段。
    实现逻辑：固定当前日期后，分别覆盖年度别名、H1/Q2 等价关系、Q1/Q3 和 TTM/LTM 族。
    可调参数：通过 monkeypatch 固定当前日期。
    默认参数及原因：选择 2026-04-16，原因是它能稳定给出 `TTM -> 20250930/20260416` 的输出。
    """

    monkeypatch.setattr(
        tushare_tools_module,
        "_current_local_date",
        lambda: date(2026, 4, 16),
    )

    for token in ("2024A", "FY2024", "2024FY", "2024年报", "2024年度", "24A"):
        period_spec = _parse_peer_periods_input(token)[0]
        assert period_spec["label"] == "2024A"
        assert period_spec["statement_period"] == "20241231"
        assert period_spec["trade_date"] == "20250430"

    for token in ("2025H1A", "2025Q2A", "2025半年报", "2025中报"):
        period_spec = _parse_peer_periods_input(token)[0]
        assert period_spec["label"] == "2025H1A"
        assert period_spec["statement_period"] == "20250630"
        assert period_spec["trade_date"] == "20250831"

    q1_period_spec = _parse_peer_periods_input("2025Q1A")[0]
    assert q1_period_spec["label"] == "2025Q1A"
    assert q1_period_spec["statement_period"] == "20250331"
    assert q1_period_spec["trade_date"] == "20250430"

    q3_period_spec = _parse_peer_periods_input("2025Q3A")[0]
    assert q3_period_spec["label"] == "2025Q3A"
    assert q3_period_spec["statement_period"] == "20250930"
    assert q3_period_spec["trade_date"] == "20251031"

    for token in ("TTM", "LTM", "Trailing Twelve Months", "最近12个月", "近12个月", "滚动12个月"):
        period_spec = _parse_peer_periods_input(token)[0]
        assert period_spec["label"] == "TTM"
        assert period_spec["statement_period"] == "20250930"
        assert period_spec["trade_date"] == "20260416"


def test_parse_peer_periods_input_outputs_provider_friendly_dates(monkeypatch) -> None:
    """
    目的：锁住期间归一化结果的 provider-friendly 日期格式。
    功能：验证所有非空的 `statement_period` 和 `trade_date` 都是 `YYYYMMDD`。
    实现逻辑：使用混合 alias 输入，遍历检查所有非空日期字段。
    可调参数：通过 monkeypatch 固定当前日期。
    默认参数及原因：使用混合输入，原因是能一次覆盖标准期、滚动期和预测期的不同输出形态。
    """

    monkeypatch.setattr(
        tushare_tools_module,
        "_current_local_date",
        lambda: date(2026, 4, 16),
    )

    period_specs = _parse_peer_periods_input("FY-1,FQ0/FY0,2025H1A,TTM,2026E")
    for period_spec in period_specs:
        if period_spec["statement_period"]:
            assert re.fullmatch(r"\d{8}", period_spec["statement_period"])
        if period_spec["trade_date"]:
            assert re.fullmatch(r"\d{8}", period_spec["trade_date"])

    forecast_spec = _parse_peer_periods_input("2026E")[0]
    assert forecast_spec["is_forecast"] is True
    assert forecast_spec["statement_period"] == ""
    assert forecast_spec["trade_date"] == ""


def test_tushare_peer_data_tool_accepts_label_only_periods_and_skips_forecast_queries(
    monkeypatch,
) -> None:
    """
    目的：回归验证本次真实故障和 forecast 边界。
    功能：确认 label-only `periods` 不再因“必须是 JSON array”失败，同时预测期不会继续打 provider。
    实现逻辑：mock 掉公司解析和 Tushare 接口，直接调用工具 `_run()` 并检查 `requested_periods` 与 period payload。
    可调参数：通过 monkeypatch 固定当前日期和底层接口返回。
    默认参数及原因：只保留一个历史期加一个预测期，原因是这正好覆盖本次修复的关键分叉。
    """

    monkeypatch.setattr(
        tushare_tools_module,
        "_current_local_date",
        lambda: date(2026, 4, 16),
    )
    monkeypatch.setattr(
        tushare_tools_module,
        "_resolve_company_identifier",
        lambda identifier: {
            "ts_code": "688380.SH",
            "symbol": "688380",
            "name": "中微半导体",
            "area": "深圳",
            "industry": "半导体",
            "market": "STAR",
            "list_date": "20220805",
        },
    )

    call_log: list[tuple[str, str | None, str | None]] = []

    def fake_safe_dataframe_call(interface_name: str, **kwargs):
        """
        目的：为工具层回归测试提供稳定的假数据接口。
        功能：记录调用轨迹，并按接口名返回最小可用数据。
        实现逻辑：把 period 和 trade_date 记入调用日志，再返回固定字典。
        可调参数：`interface_name` 和调用参数由待测工具传入。
        默认参数及原因：返回最小字段集合，原因是这里只验证 period 解析和 forecast 跳过逻辑。
        """

        call_log.append(
            (
                interface_name,
                kwargs.get("period"),
                kwargs.get("trade_date"),
            )
        )
        fake_rows = {
            "daily_basic": {
                "total_mv": 100.0,
                "circ_mv": 80.0,
                "pe_ttm": 12.3,
                "pb": 1.5,
                "ps_ttm": 2.1,
            },
            "fina_indicator": {
                "or_yoy": 23.4,
            },
            "income": {
                "revenue": 1000.0,
                "operate_profit": 120.0,
                "n_income_attr_p": 90.0,
                "ebitda": 140.0,
            },
            "balancesheet": {
                "total_assets": 3000.0,
                "total_liab": 1000.0,
                "money_cap": 200.0,
            },
            "cashflow": {},
        }
        return fake_rows.get(interface_name, {}), None

    monkeypatch.setattr(
        tushare_tools_module,
        "_safe_dataframe_call",
        fake_safe_dataframe_call,
    )

    payload = json.loads(
        TusharePeerDataTool()._run(
            companies="688380.SH",
            periods="2024A,2026E",
            required_metrics="revenue_growth,pe_ttm",
        )
    )

    assert [period_spec["label"] for period_spec in payload["requested_periods"]] == ["2024A", "2026E"]
    assert payload["requested_periods"][0]["statement_period"] == "20241231"
    assert payload["requested_periods"][0]["trade_date"] == "20250430"
    assert payload["requested_periods"][1]["is_forecast"] is True

    company_periods = payload["company_snapshots"][0]["periods"]
    assert company_periods["2024A"]["provider_query_skipped"] is False
    assert company_periods["2024A"]["metric_values"]["revenue_growth"] == 23.4
    assert company_periods["2024A"]["metric_values"]["pe_ttm"] == 12.3

    assert company_periods["2026E"]["provider_query_skipped"] is True
    assert company_periods["2026E"]["is_forecast"] is True
    assert company_periods["2026E"]["metric_values"]["revenue_growth"] is None
    assert company_periods["2026E"]["metric_values"]["pe_ttm"] is None
    assert company_periods["2026E"]["missing_metrics"] == ["revenue_growth", "pe_ttm"]
    assert "provider queries skipped for forecast period" in company_periods["2026E"]["resolution_note"]

    assert len(call_log) == 5
    assert {entry[0] for entry in call_log} == {
        "daily_basic",
        "fina_indicator",
        "income",
        "balancesheet",
        "cashflow",
    }
