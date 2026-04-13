from __future__ import annotations

from automated_research_report_generator.tools.tushare_tools import (
    _parse_peer_periods_input,
    _normalize_market_value_to_cny,
    _parse_companies_input,
    assess_tushare_peer_data_tool_coverage,
    assess_tushare_valuation_tool_peer_coverage,
)


def test_parse_companies_input_supports_json_and_csv() -> None:
    """
    设计目的：锁住 Tushare 工具的公司列表输入兼容性。
    模块功能：验证 JSON 数组和逗号分隔字符串都能被解析成统一列表。
    实现逻辑：分别传入两种常见格式，再比较标准化结果。
    可调参数：无。
    默认参数及原因：测试只覆盖最常见两种输入，原因是这已经覆盖 valuation crew 的主要调用方式。
    """

    assert _parse_companies_input('["宁德时代", "300750.SZ"]') == ["宁德时代", "300750.SZ"]
    assert _parse_companies_input("宁德时代, 比亚迪") == ["宁德时代", "比亚迪"]


def test_normalize_market_value_to_cny_converts_wan_to_yuan() -> None:
    """
    设计目的：锁住 Tushare 每日指标市值字段的单位换算。
    模块功能：验证 `daily_basic.total_mv` 从万元转换为元的逻辑。
    实现逻辑：用简单数字样例做断言，确保乘数没有写错。
    可调参数：无。
    默认参数及原因：测试使用整数样例，原因是更容易直接看出换算结果是否正确。
    """

    assert _normalize_market_value_to_cny(123.45) == 1_234_500.0


def test_assess_tushare_valuation_tool_peer_coverage_exposes_gaps() -> None:
    """
    设计目的：锁住 peer info 对现有估值工具的能力审计结果，避免误判工具覆盖范围。
    模块功能：验证审计结果会明确标出直接覆盖、可推导和不支持的指标。
    实现逻辑：读取能力矩阵后，断言几个关键指标的分类和总览结论。
    可调参数：无。
    默认参数及原因：只验证代表性指标，原因是这里要防的是能力边界回归，而不是重复覆盖整张矩阵。
    """

    audit = assess_tushare_valuation_tool_peer_coverage()

    assert audit["summary"] == "current_valuation_tool_is_not_sufficient_for_full_peer_info_pack"
    assert audit["coverage"]["revenue_amount"]["status"] == "direct"
    assert audit["coverage"]["ev_ebitda"]["status"] == "derived"
    assert audit["coverage"]["selling_expense_ratio"]["status"] == "direct"
    assert audit["coverage"]["asset_turnover"]["status"] == "direct"
    assert audit["coverage"]["accounts_payable_turnover"]["status"] == "unsupported"


def test_parse_peer_periods_input_supports_default_and_explicit_periods() -> None:
    """
    设计目的：锁住 peer info 多期间输入的默认回退和 JSON 解析行为。
    模块功能：验证留空时会回退到 `LATEST`，显式输入时会保留标签与日期口径。
    实现逻辑：分别传入空串和一个最小 JSON 数组，再比较结构化结果。
    可调参数：无。
    默认参数及原因：测试只覆盖最小合法输入，原因是这已经能防住最关键的接口回归。
    """

    assert _parse_peer_periods_input("") == [
        {
            "label": "LATEST",
            "statement_period": "",
            "trade_date": "",
        }
    ]
    assert _parse_peer_periods_input(
        '[{"label":"FY-1","statement_period":"20241231","trade_date":"20250415"}]'
    ) == [
        {
            "label": "FY-1",
            "statement_period": "20241231",
            "trade_date": "20250415",
        }
    ]


def test_assess_tushare_peer_data_tool_coverage_matches_current_peer_scope() -> None:
    """
    设计目的：锁住同行数据工具自身的能力边界，避免把估值工具缺口误标到同行数据工具上。
    模块功能：验证当前同行数据工具已支持的费用率类指标不会被误判为不支持，同时保留真实缺口。
    实现逻辑：读取能力矩阵后，断言代表性指标的分类结果。
    可调参数：无。
    默认参数及原因：只验证代表性指标，原因是这里关注的是能力口径是否被错误复用。
    """

    audit = assess_tushare_peer_data_tool_coverage()

    assert audit["summary"] == "current_peer_data_tool_has_partial_gaps_for_peer_info_pack"
    assert audit["coverage"]["selling_expense_ratio"]["status"] == "direct"
    assert audit["coverage"]["ev_ebitda"]["status"] == "derived"
    assert audit["coverage"]["asset_turnover"]["status"] == "direct"
    assert audit["coverage"]["accounts_payable_turnover"]["status"] == "unsupported"
