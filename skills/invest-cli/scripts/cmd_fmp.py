#!/usr/bin/env python3
"""
FMP 美股深取 — 三表 JSON / TTM 比率 / 结构化评级 / 年度分析师预测。

定位：sec_edgar 同型的深取专项源，**不进快照链**（免费档 250 次/天扛不住日常
兜底消耗）。分工：`invest-cli sec` 给 10-K XBRL 原文级；本命令给加工好的
比率/评级/预测。边界见 sources/fmp.py 顶部注释。

用法：
  invest-cli fmp AAPL                 深取快照（行情+TTM 比率+评级+公司概况）
  invest-cli fmp AAPL --estimates     年度分析师预测（EPS/营收 低/均/高）
  invest-cli fmp AAPL --statements    三表（--period annual，--limit N）
  invest-cli fmp AAPL --dividends     分红历史
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _fetch_envelope(symbol: str, mode: str, period: str = "annual",
                    limit: int = 2) -> dict[str, Any]:
    from sources import fmp

    if mode == "estimates":
        return fmp.analyst_estimates(symbol)
    if mode == "statements":
        return fmp.statements(symbol, period=period, limit=limit)
    if mode == "dividends":
        return fmp.dividends(symbol)
    return fmp.deep_snapshot(symbol)


def run(args: Any) -> int:
    """invest-cli 入口。返回进程退出码；错误走 stderr。"""
    mode = ("estimates" if args.estimates else
            "statements" if args.statements else
            "dividends" if args.dividends else "deep")
    res = _fetch_envelope(args.symbol, mode, period=args.period, limit=args.limit)
    if not res.get("ok") or not isinstance(res.get("data"), dict):
        print(f"错误: {res.get('error') or 'FMP 取数失败'}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res["data"], ensure_ascii=False, indent=2))
        return 0
    data = res["data"]
    if mode == "estimates":
        print(_format_estimates(data))
    elif mode == "statements":
        print(_format_statements(data))
    elif mode == "dividends":
        print(_format_dividends(data))
    else:
        print(_format_deep(data))
    return 0


def _table_row(label: str, value: Any, width: int = 16) -> str:
    """渲染一行：缺失印 -；浮点原样 str() 会拖尾巴，走 6 位有效数字。"""
    if value is None:
        shown = "-"
    elif isinstance(value, float):
        shown = f"{value:,.2f}" if abs(value) >= 1e6 else format(value, ".6g")
    else:
        shown = str(value)
    return f"  {label:<{width}} {shown:>18}"


def _pct(v: Any) -> str:
    """小数比率 → 百分数字符串（渲染器与 cmd_us 同一量级纪律）。"""
    return "-" if v is None else f"{v * 100:.2f}%"


def _format_deep(data: dict[str, Any]) -> str:
    from cmd_us import _money

    q = data.get("quote", {})
    f = data.get("financial", {})
    r = data.get("rating", {})
    b = data.get("business", {})
    currency = data.get("currency", "USD")
    lines = [
        f"\n{'=' * 60}",
        f"  {data.get('name', data['symbol'])}（{data.get('symbol', '?')}）— FMP 深取快照",
        f"  货币: {currency}",
        f"{'=' * 60}",
        "",
        f"  {'指标':<16} {'数值':>18}",
        f"  {'-' * 36}",
    ]
    if q.get("price") is not None:
        lines.append(f"  {'当前价格':<16} {_money(q['price'], currency):>18}")
    if q.get("change_pct") is not None:
        lines.append(f"  {'涨跌幅':<16} {q['change_pct']:>+17.2f}%")
    for label, key in (("前收盘", "previous_close"), ("当日最高", "day_high"), ("当日最低", "day_low"),
                       ("年内最高", "year_high"), ("年内最低", "year_low"),
                       ("50日均价", "price_avg_50"), ("200日均价", "price_avg_200"), ("Beta", "beta")):
        if q.get(key) is not None:
            lines.append(_table_row(label, q[key]))
    if q.get("market_cap") is not None:
        mc = q["market_cap"]
        lines.append(_table_row("总市值", f"{mc / 1e8:.0f}亿" if mc >= 1e8 else f"{mc / 1e6:.0f}M"))
    if q.get("volume") is not None:
        lines.append(_table_row("成交量", f"{q['volume']:,}"))
    if q.get("avg_volume") is not None:
        lines.append(_table_row("平均成交量", f"{q['avg_volume']:,}"))

    if f:
        lines += ["", f"  {'TTM 比率（FMP）':<16} {'数值':>18}", f"  {'-' * 36}"]
        for label, key, is_ratio in [
            ("毛利率", "gross_margin", True), ("经营利润率", "operating_margin", True),
            ("净利率", "net_margin", True), ("EBITDA利润率", "ebitda_margin", True),
            ("流动比率", "current_ratio", False), ("负债/资产", "debt_to_assets", True),
            ("负债/权益", "debt_to_equity", True), ("股息率", "dividend_yield", True),
            ("派息率", "payout_ratio", True), ("每股股息", "dividend_per_share", False),
            ("每股自由现金流", "fcf_per_share", False), ("市价/FCF", "price_to_fcf", False),
            ("企业价值EV", "enterprise_value", False), ("EV/销售", "ev_to_sales", False),
            ("EV/FCF", "ev_to_fcf", False), ("EV/EBITDA", "ev_to_ebitda", False),
            ("净负债/EBITDA", "net_debt_to_ebitda", False),
        ]:
            if f.get(key) is None:
                continue
            shown = _pct(f[key]) if is_ratio else (
                f"{f[key] / 1e8:.0f}亿" if key == "enterprise_value" and abs(f[key]) >= 1e8
                else format(f[key], ".6g"))
            lines.append(f"  {label:<16} {shown:>18}")

    if r:
        rating_txt = f"{r.get('rating', '-')}（总分 {r.get('overall_score', '-')}）"
        lines += ["", f"  {'FMP 评级':<16} {rating_txt:>18}"]
        subs = " / ".join(f"{n} {r[s]}" for n, s in [
            ("DCF", "dcf_score"), ("ROE", "roe_score"), ("ROA", "roa_score"),
            ("负债", "debt_score"), ("PE", "pe_score"), ("PB", "pb_score")] if r.get(s) is not None)
        if subs:
            lines.append(f"  {'分项得分':<16} {subs:>18}")

    if b:
        lines += ["", f"  {'公司概况':<16} {'':>18}"]
        if b.get("sector"):
            sector_txt = f"{b.get('sector', '')} / {b.get('industry', '')}"
            lines.append(f"  {'行业':<16} {sector_txt:>18}")
        for label, key in (("CEO", "ceo"), ("员工数", "employees"), ("上市日期", "ipo_date"),
                           ("交易所", "exchange"), ("CIK", "cik"), ("ISIN", "isin")):
            if b.get(key) is not None:
                lines.append(_table_row(label, b[key]))

    lines += ["", f"  数据来源: Financial Modeling Prep（免费档 250 次/天，深取专项）",
              f"  数据时间: {data.get('timestamp', '')}"]
    return "\n".join(lines)


# 三表打印键（→ 中文标签）；整表字段 60+ 个，只打印分析常用集
_STATEMENT_KEYS = {
    "income": [("revenue", "营收"), ("grossProfit", "毛利润"), ("operatingIncome", "经营利润"),
               ("ebitda", "EBITDA"), ("netIncome", "净利润"), ("eps", "EPS"),
               ("epsDiluted", "EPS(摊薄)")],
    "balance": [("totalAssets", "总资产"), ("totalLiabilities", "总负债"),
                ("totalStockholdersEquity", "股东权益"), ("cashAndCashEquivalents", "现金及等价物"),
                ("totalDebt", "总负债(有息)"), ("retainedEarnings", "留存收益")],
    "cash_flow": [("operatingCashFlow", "经营现金流"), ("capitalExpenditure", "资本开支"),
                  ("freeCashFlow", "自由现金流"), ("dividendsPaid", "分红支付"),
                  ("stockRepurchased", "回购")],
}
_STATEMENT_TITLES = {"income": "利润表", "balance": "资产负债表", "cash_flow": "现金流量表"}


def _format_statements(data: dict[str, Any]) -> str:
    lines = [
        f"\n{'=' * 60}",
        f"  {data.get('symbol', '?')} — FMP 三表（{data.get('period', 'annual')}，最近各期）",
        f"{'=' * 60}",
    ]
    for name, rows in (data.get("statements") or {}).items():
        if not rows:
            continue
        lines.append(f"\n  【{_STATEMENT_TITLES.get(name, name)}】")
        header = [str((r.get("date") or "")[:10]) for r in rows]
        lines.append("  " + f"{'科目':<14}" + "".join(f"{h:>16}" for h in reversed(header)))
        for key, label in _STATEMENT_KEYS.get(name, []):
            cells = []
            for r in rows:
                v = r.get(key)
                cells.append("-" if v is None else (f"{v / 1e8:.0f}亿" if abs(v) >= 1e8 else format(v, ".6g")))
            lines.append("  " + f"{label:<14}" + "".join(f"{c:>16}" for c in reversed(cells)))
    for err in data.get("statement_errors") or []:
        lines.append(f"\n  ⚠ {err}")
    lines.append("\n  数据来源: Financial Modeling Prep（as-reported，含 filingDate）")
    return "\n".join(lines)


def _format_estimates(data: dict[str, Any]) -> str:
    rows = data.get("rows") or []
    lines = [
        f"\n{'=' * 60}",
        f"  {data.get('symbol', '?')} — FMP 年度分析师预测",
        f"{'=' * 60}",
        "",
        f"  {'年度':<8}{'营收(均)':>16}{'EPS(均)':>12}{'分析师数':>10}",
        f"  {'-' * 48}",
    ]
    for r in rows:
        rev = r.get("revenue_avg")
        rev_s = "-" if rev is None else (f"{rev / 1e8:.0f}亿" if abs(rev) >= 1e8 else f"{rev:.4g}")
        eps = r.get("eps_avg")
        lines.append(
            f"  {r.get('date', '-'):<8}{rev_s:>16}"
            f"{'-' if eps is None else format(eps, '.4g'):>12}"
            f"{str(r.get('num_analysts') or '-'):>10}"
        )
    lines += ["", "  口径: FMP 汇总的卖方一致预期（annual；quarterly 为付费档）"]
    return "\n".join(lines)


def _format_dividends(data: dict[str, Any]) -> str:
    rows = (data.get("rows") or [])[-10:]
    lines = [
        f"\n{'=' * 60}",
        f"  {data.get('symbol', '?')} — FMP 分红历史（共 {data.get('count', 0)} 条，合计 {data.get('total', 0)}）",
        f"{'=' * 60}",
        "",
        f"  {'除权日':<12}{'记录日':<12}{'支付日':<12}{'每股分红':>12}",
        f"  {'-' * 48}",
    ]
    for r in reversed(rows):
        d = r.get("dividend")
        lines.append(
            f"  {r.get('date', '-'):<12}{r.get('record_date', '-'):<12}"
            f"{r.get('payment_date', '-'):<12}{'-' if d is None else format(d, '.4g'):>12}"
        )
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="FMP 美股深取（三表/比率/评级/预测）")
    parser.add_argument("symbol", help="美股代码，如 AAPL、MSFT")
    parser.add_argument("--estimates", action="store_true", help="年度分析师预测")
    parser.add_argument("--statements", action="store_true", help="三表（income/balance/cash-flow）")
    parser.add_argument("--dividends", action="store_true", help="分红历史")
    parser.add_argument("--period", default="annual", help="三表口径 annual/quarterly（默认 annual）")
    parser.add_argument("--limit", type=int, default=2, help="三表期数（默认 2）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
